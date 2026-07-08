"""New Relic export integration for SketchLog.

Supports two New Relic APIs:

* **Events API** – ``POST .../v1/accounts/{id}/events``
* **Metric API** – ``POST .../metric/v1``

Both US and EU data centre regions are supported.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Union, Any

import httpx

from sketchlog.exporters.base import ExporterError


class NewRelicRegion(str, Enum):
    """New Relic data centre region."""

    US = "us"
    EU = "eu"


class NewRelicMetricType(str, Enum):
    """New Relic metric submission type.

    See https://docs.newrelic.com/docs/data-apis/ingest-apis/metric-api/report-metrics-metric-api/
    """

    GAUGE   = "gauge"
    COUNT   = "count"
    SUMMARY = "summary"


_EVENTS_URL: dict[NewRelicRegion, str] = {
    NewRelicRegion.US: "https://insights-collector.newrelic.com/v1/accounts/{account_id}/events",
    NewRelicRegion.EU: "https://insights-collector.eu01.nr-data.net/v1/accounts/{account_id}/events",
}

_METRICS_URL: dict[NewRelicRegion, str] = {
    NewRelicRegion.US: "https://metric-api.newrelic.com/metric/v1",
    NewRelicRegion.EU: "https://metric-api.eu.newrelic.com/metric/v1",
}


@dataclass(frozen=True)
class NewRelicConfig:
    """Immutable configuration for :class:`NewRelicExporter`.

    Args:
        api_key: New Relic Ingest API key (``Api-Key`` header).
        account_id: New Relic account identifier (used by the Events API).
        region: Data centre region (default :attr:`NewRelicRegion.US`).
        timeout: Request timeout in seconds (default ``10.0``).
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
        if self.timeout <= 0:
            raise ValueError("timeout must be positive")


@dataclass
class NewRelicEvent:
    """A New Relic custom event.

    Args:
        event_type: Event type name (required by New Relic).
        attributes: Arbitrary key/value pairs attached to the event.
        timestamp: Unix epoch seconds.  Defaults to *now* when ``None``.
    """

    event_type: str
    attributes: dict[str, Any] = field(default_factory=dict)
    timestamp: int | None = None

    def __post_init__(self) -> None:
        if not self.event_type:
            raise ValueError("event_type must not be empty")


@dataclass
class NewRelicMetric:
    """A New Relic metric data point.

    Args:
        name: Metric name.
        value: Numeric value (use a :class:`dict` with keys ``count``,
            ``sum``, ``min``, ``max`` for SUMMARY metrics).
        metric_type: Submission type (default :attr:`NewRelicMetricType.GAUGE`).
        attributes: Arbitrary dimensions/tags attached to the metric.
        timestamp: Unix epoch milliseconds.  Defaults to *now* when ``None``.
        interval_ms: Reporting interval in milliseconds.  Required for
            COUNT and SUMMARY metrics.
    """

    name: str
    value: Union[float, Dict[str, float]]
    metric_type: NewRelicMetricType = NewRelicMetricType.GAUGE
    attributes: dict[str, Any] = field(default_factory=dict)
    timestamp: int | None = None
    interval_ms: int | None = None

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("name must not be empty")
        if self.metric_type in (NewRelicMetricType.COUNT, NewRelicMetricType.SUMMARY):
            if self.interval_ms is None:
                raise ValueError(
                    f"interval_ms is required for {self.metric_type.value} metrics"
                )


class NewRelicExporter:
    """Sends events and metrics to New Relic.

    Use as a context manager for connection reuse::

        cfg = NewRelicConfig(api_key="NRAK-...", account_id="12345678")
        with NewRelicExporter(cfg) as exp:
            exp.send_event(NewRelicEvent("PageView", {"url": "/home"}))
            exp.send_metric(NewRelicMetric("cpu.usage", 0.72))

    Or call methods directly without a context manager.
    """

    def __init__(self, config: NewRelicConfig) -> None:
        self._config = config
        self._client: httpx.Client | None = None

    # ── lifecycle ────────────────────────────────────────────────────────────

    def __enter__(self) -> "NewRelicExporter":
        self._client = self._make_client()
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    def close(self) -> None:
        """Close the underlying HTTP client and release resources."""
        if self._client is not None:
            self._client.close()
            self._client = None

    # ── internals ────────────────────────────────────────────────────────────

    def _make_client(self) -> httpx.Client:
        return httpx.Client(
            headers={"Api-Key": self._config.api_key, "Content-Type": "application/json"},
            timeout=self._config.timeout,
        )

    def _events_url(self) -> str:
        return _EVENTS_URL[self._config.region].format(account_id=self._config.account_id)

    def _metrics_url(self) -> str:
        return _METRICS_URL[self._config.region]

    def _do_post(self, url: str, payload: Any) -> None:
        client = self._client
        if client is None:
            client = self._make_client()
            owned = True
        else:
            owned = False
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
        finally:
            if owned:
                client.close()

    # ── public API — events ──────────────────────────────────────────────────

    def send_event(self, event: NewRelicEvent) -> None:
        """Send a single custom event to New Relic.

        Args:
            event: The event to send.

        Raises:
            ExporterError: On HTTP error or network timeout.
        """
        self.send_events([event])

    def send_events(self, events: list[NewRelicEvent]) -> None:
        """Send multiple custom events in a single request.

        Args:
            events: Non-empty list of events.

        Raises:
            ValueError: If *events* is empty.
            ExporterError: On HTTP error or network timeout.
        """
        if not events:
            raise ValueError("events must not be empty")
        now = int(time.time())
        payload: list[dict[str, Any]] = []
        for ev in events:
            body: dict[str, Any] = {"eventType": ev.event_type, **ev.attributes}
            body["timestamp"] = ev.timestamp if ev.timestamp is not None else now
            payload.append(body)
        self._do_post(self._events_url(), payload)

    # ── public API — metrics ─────────────────────────────────────────────────

    def send_metric(self, metric: NewRelicMetric) -> None:
        """Send a single metric data point to New Relic.

        Args:
            metric: The metric to send.

        Raises:
            ExporterError: On HTTP error or network timeout.
        """
        self.send_metrics([metric])

    def send_metrics(self, metrics: list[NewRelicMetric]) -> None:
        """Send multiple metric data points in a single request.

        Args:
            metrics: Non-empty list of metrics.

        Raises:
            ValueError: If *metrics* is empty.
            ExporterError: On HTTP error or network timeout.
        """
        if not metrics:
            raise ValueError("metrics must not be empty")
        now_ms = int(time.time() * 1000)
        items: list[dict[str, Any]] = []
        for m in metrics:
            ts = m.timestamp if m.timestamp is not None else now_ms
            item: dict[str, Any] = {
                "name": m.name,
                "type": m.metric_type.value,
                "value": m.value,
                "timestamp": ts,
                "attributes": m.attributes,
            }
            if m.interval_ms is not None:
                item["interval.ms"] = m.interval_ms
            items.append(item)
        self._do_post(self._metrics_url(), [{"metrics": items}])
