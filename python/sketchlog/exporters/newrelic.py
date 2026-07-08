"""New Relic export integration for SketchLog."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Union

import httpx

from sketchlog.exporters.base import ExporterError


class NewRelicRegion(str, Enum):
    """New Relic data centre region."""

    US = "us"
    EU = "eu"


class NewRelicMetricType(str, Enum):
    """New Relic metric submission type."""

    GAUGE = "gauge"
    COUNT = "count"
    SUMMARY = "summary"


@dataclass(frozen=True)
class NewRelicConfig:
    """Immutable configuration for :class:`NewRelicExporter`.

    Args:
        api_key: New Relic ingest licence key.
        account_id: New Relic account ID (required for Events API).
        region: Data centre region — :attr:`NewRelicRegion.US` (default) or
            :attr:`NewRelicRegion.EU`.
        timeout: Request timeout in seconds (default ``10.0``).
    """

    api_key: str
    account_id: str = ""
    region: NewRelicRegion = NewRelicRegion.US
    timeout: float = 10.0

    def __post_init__(self) -> None:
        if not self.api_key:
            raise ValueError("api_key must not be empty")


@dataclass
class NewRelicEvent:
    """A single New Relic custom event.

    Args:
        event_type: Custom event type name.
        attributes: Arbitrary key-value attributes.
        timestamp: Unix timestamp in seconds (default: current time).
    """

    event_type: str
    attributes: Dict[str, Any] = field(default_factory=dict)
    timestamp: Optional[float] = None


@dataclass
class NewRelicMetric:
    """A single New Relic metric data point.

    Args:
        name: Metric name.
        value: Numeric value, or a summary dict
            ``{"count": N, "sum": S, "min": m, "max": M}`` for SUMMARY metrics.
        metric_type: Submission type.
        attributes: Arbitrary key-value attributes.
        interval_ms: Interval in milliseconds (required for COUNT/SUMMARY).
    """

    name: str
    value: Union[float, Dict[str, float]]
    metric_type: NewRelicMetricType = NewRelicMetricType.GAUGE
    attributes: Dict[str, Any] = field(default_factory=dict)
    interval_ms: Optional[int] = None


class NewRelicExporter:
    """Sends events and metrics to New Relic.

    Can be used standalone or as a context manager::

        with NewRelicExporter(cfg) as exp:
            exp.send_event(NewRelicEvent("Purchase", {"amount": 9.99}))

    Args:
        config: :class:`NewRelicConfig` instance.
        client: Optional pre-configured :class:`httpx.Client`.
    """

    def __init__(self, config: NewRelicConfig, client: Optional[httpx.Client] = None) -> None:
        self._config = config
        self._client = client

    def __enter__(self) -> "NewRelicExporter":
        self.open()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def open(self) -> None:
        if self._client is None:
            self._client = self._make_client()

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    # ── Events API ────────────────────────────────────────────────────────────

    def send_event(self, event: NewRelicEvent) -> None:
        """Send a single custom event."""
        self.send_events([event])

    def send_events(self, events: List[NewRelicEvent]) -> None:
        """Send a batch of custom events."""
        payload = []
        for e in events:
            item: Dict[str, Any] = {"eventType": e.event_type}
            item.update(e.attributes)
            if e.timestamp is not None:
                item["timestamp"] = int(e.timestamp)
            payload.append(item)
        self._do_post(self._events_url(), payload)

    # ── Metric API ────────────────────────────────────────────────────────────

    def send_metric(self, metric: NewRelicMetric) -> None:
        """Send a single metric."""
        self.send_metrics([metric])

    def send_metrics(self, metrics: List[NewRelicMetric]) -> None:
        """Send a batch of metrics."""
        now_ms = int(time.time() * 1000)
        items: List[Dict[str, Any]] = []
        for m in metrics:
            item: Dict[str, Any] = {
                "name": m.name,
                "type": m.metric_type.value,
                "value": m.value,
                "timestamp": now_ms,
                "attributes": m.attributes,
            }
            if m.interval_ms is not None:
                item["interval.ms"] = m.interval_ms
            items.append(item)
        self._do_post(self._metrics_url(), [{"metrics": items}])

    # ── URL helpers ───────────────────────────────────────────────────────────

    def _events_url(self) -> str:
        if self._config.region == NewRelicRegion.EU:
            host = "insights-collector.eu01.nr-data.net"
        else:
            host = "insights-collector.nr-data.net"
        return f"https://{host}/v1/accounts/{self._config.account_id}/events"

    def _metrics_url(self) -> str:
        if self._config.region == NewRelicRegion.EU:
            host = "metric-api.eu.newrelic.com"
        else:
            host = "metric-api.newrelic.com"
        return f"https://{host}/metric/v1"

    # ── internals ─────────────────────────────────────────────────────────────

    def _make_client(self) -> httpx.Client:
        return httpx.Client(
            headers={"Api-Key": self._config.api_key, "Content-Type": "application/json"},
            timeout=self._config.timeout,
        )

    def _do_post(self, url: str, payload: Any) -> None:
        owned = self._client is None
        _client: httpx.Client = self._client if self._client is not None else self._make_client()
        try:
            resp = _client.post(url, json=payload)
            try:
                resp.raise_for_status()
            except httpx.HTTPStatusError as exc:
                raise ExporterError(str(exc), status_code=exc.response.status_code) from exc
        except ExporterError:
            raise
        except httpx.TimeoutException as exc:
            raise ExporterError(f"New Relic post timed out: {exc}") from exc
        except httpx.RequestError as exc:
            raise ExporterError(f"New Relic post failed: {exc}") from exc
        finally:
            if owned:
                _client.close()
