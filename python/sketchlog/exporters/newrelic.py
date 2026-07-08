"""New Relic Events + Metric API exporter for SketchLog."""
from __future__ import annotations
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Union
import httpx
from .base import ExporterError


class NewRelicRegion(str, Enum):
    """New Relic account region."""

    US = "us"
    EU = "eu"


class NewRelicMetricType(str, Enum):
    """New Relic metric type."""

    GAUGE = "gauge"
    COUNT = "count"
    SUMMARY = "summary"


@dataclass(frozen=True)
class NewRelicConfig:
    """Configuration for the New Relic exporter.

    Args:
        api_key: New Relic Ingest - License key (required).
        account_id: New Relic account ID (required for Events API).
        region: :class:`NewRelicRegion` — US (default) or EU.
        timeout: HTTP request timeout in seconds (default 10).
    """

    api_key: str
    account_id: str
    region: NewRelicRegion = NewRelicRegion.US
    timeout: float = 10.0

    def __post_init__(self) -> None:
        if not self.api_key:
            raise ValueError("api_key must not be empty")
        if not self.account_id:
            raise ValueError("account_id must not be empty")


@dataclass
class NewRelicEvent:
    """A New Relic custom event.

    Args:
        event_type: New Relic event type name (alphanumeric + underscore).
        attributes: Mapping of attribute name → value.
        timestamp: Optional UNIX timestamp (seconds). Current time used if omitted.
    """

    event_type: str
    attributes: dict[str, Any] = field(default_factory=dict)
    timestamp: int | None = None


@dataclass
class NewRelicMetric:
    """A New Relic metric data point.

    Args:
        name: Metric name.
        value: Numeric value for GAUGE/COUNT, or a ``dict`` with keys
               ``sum``, ``count``, ``min``, ``max`` for SUMMARY metrics.
        type: :class:`NewRelicMetricType` (default GAUGE).
        attributes: Optional metric attributes.
        interval_ms: Required for COUNT and SUMMARY metrics (milliseconds).
    """

    name: str
    value: Union[float, dict[str, float]]
    type: NewRelicMetricType = NewRelicMetricType.GAUGE
    attributes: dict[str, Any] = field(default_factory=dict)
    interval_ms: int | None = None


class NewRelicExporter:
    """Synchronous New Relic exporter — Events API and Metric API.

    Supports context-manager usage::

        with NewRelicExporter(cfg) as exp:
            exp.send_event(NewRelicEvent("MyEvent", {"key": "value"}))

    Args:
        config: :class:`NewRelicConfig` instance.
        client: Optional pre-built ``httpx.Client`` (for testing / reuse).
    """

    def __init__(self, config: NewRelicConfig, client: httpx.Client | None = None) -> None:
        self._cfg = config
        self._client = client
        self._owned = client is None

    # ── context manager ───────────────────────────────────────────────────────
    def __enter__(self) -> "NewRelicExporter":
        if self._owned and self._client is None:
            self._client = self._make_client()
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    def close(self) -> None:
        """Close the underlying HTTP client if it was created by this exporter."""
        if self._owned and self._client is not None:
            self._client.close()
            self._client = None

    # ── public API — Events ───────────────────────────────────────────────────
    def send_event(self, event: NewRelicEvent) -> None:
        """Send a single custom event.

        Args:
            event: :class:`NewRelicEvent` to send.

        Raises:
            ExporterError: On HTTP or transport failure.
        """
        self.send_events([event])

    def send_events(self, events: list[NewRelicEvent]) -> None:
        """Send multiple custom events in one request.

        Args:
            events: List of :class:`NewRelicEvent` to send.

        Raises:
            ExporterError: On HTTP or transport failure.
        """
        payload = []
        for e in events:
            obj: dict[str, Any] = {"eventType": e.event_type, **e.attributes}
            if e.timestamp is not None:
                obj["timestamp"] = e.timestamp
            payload.append(obj)
        self._do_post(self._events_url(), payload)

    # ── public API — Metrics ──────────────────────────────────────────────────
    def send_metric(self, metric: NewRelicMetric) -> None:
        """Send a single metric data point.

        Args:
            metric: :class:`NewRelicMetric` to send.

        Raises:
            ExporterError: On HTTP or transport failure.
        """
        self.send_metrics([metric])

    def send_metrics(self, metrics: list[NewRelicMetric]) -> None:
        """Send multiple metric data points in one request.

        Args:
            metrics: List of :class:`NewRelicMetric` to send.

        Raises:
            ExporterError: On HTTP or transport failure.
        """
        data = []
        for m in metrics:
            point: dict[str, Any] = {
                "name": m.name,
                "type": m.type.value,
                "value": m.value,
                "timestamp": int(time.time() * 1000),
            }
            if m.interval_ms is not None:
                point["interval.ms"] = m.interval_ms
            if m.attributes:
                point["attributes"] = m.attributes
            data.append(point)
        self._do_post(self._metrics_url(), [{"metrics": data}])

    # ── internals ─────────────────────────────────────────────────────────────
    def _events_url(self) -> str:
        if self._cfg.region == NewRelicRegion.EU:
            return (
                f"https://insights-collector.eu01.nr-data.net/v1/accounts"
                f"/{self._cfg.account_id}/events"
            )
        return (
            f"https://insights-collector.nr-data.net/v1/accounts"
            f"/{self._cfg.account_id}/events"
        )

    def _metrics_url(self) -> str:
        if self._cfg.region == NewRelicRegion.EU:
            return "https://metric-api.eu.newrelic.com/metric/v1"
        return "https://metric-api.newrelic.com/metric/v1"

    def _make_client(self) -> httpx.Client:
        return httpx.Client(
            headers={"Api-Key": self._cfg.api_key, "Content-Type": "application/json"},
            timeout=self._cfg.timeout,
        )

    def _do_post(self, url: str, payload: Any) -> None:
        client = self._client
        owned = client is None
        if owned:
            client = self._make_client()
        try:
            resp = client.post(url, json=payload)
            try:
                resp.raise_for_status()
            except httpx.HTTPStatusError as exc:
                raise ExporterError(str(exc), status_code=exc.response.status_code) from exc
        except ExporterError:
            raise
        except httpx.TimeoutException as exc:
            raise ExporterError(f"New Relic post timed out: {exc}") from exc
        except httpx.RequestError as exc:
            raise ExporterError(f"New Relic transport error: {exc}") from exc
        finally:
            if owned:
                client.close()
