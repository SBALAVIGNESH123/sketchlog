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
    """New Relic metric submission type."""

    GAUGE = "gauge"
    COUNT = "count"
    SUMMARY = "summary"


@dataclass(frozen=True)
class NewRelicConfig:
    """Immutable configuration for :class:`NewRelicExporter`.

    Args:
        api_key: New Relic Ingest – License key (starts with ``NRAL``).
        account_id: New Relic account ID (required for Events API).
        region: Data centre region (US or EU, default US).
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


@dataclass
class NewRelicEvent:
    """A single New Relic custom event.

    Args:
        event_type: Event type name (e.g. ``"SketchLogEvent"``).
        attributes: Arbitrary key-value attributes for this event.
        timestamp: Unix epoch timestamp (auto-assigned if ``None``).
    """

    event_type: str
    attributes: dict[str, Any] = field(default_factory=dict)
    timestamp: int | None = None


@dataclass
class NewRelicMetric:
    """A single New Relic metric data point.

    Args:
        name: Metric name.
        value: Numeric value.  For SUMMARY metrics this should be a dict
            with keys ``count``, ``sum``, ``min``, ``max``.
        metric_type: Submission type (GAUGE / COUNT / SUMMARY).
        attributes: Arbitrary key-value attributes.
        interval_ms: Interval in milliseconds (required for COUNT/SUMMARY).
    """

    name: str
    value: Union[float, dict[str, float]]
    metric_type: NewRelicMetricType = NewRelicMetricType.GAUGE
    attributes: dict[str, Any] = field(default_factory=dict)
    interval_ms: int | None = None


class NewRelicExporter:
    """Sends events and metrics to New Relic.

    Can be used standalone or as a context manager::

        with NewRelicExporter(cfg) as exp:
            exp.send_event(NewRelicEvent("Deploy", {"version": "1.2"}))
    """

    def __init__(self, config: NewRelicConfig, client: httpx.Client | None = None) -> None:
        self._cfg = config
        self._client = client
        self._owned = client is None

    # ── lifecycle ──────────────────────────────────────────────────────────

    def open(self) -> None:
        """Open a persistent HTTP connection pool."""
        if self._client is None:
            self._client = self._make_client()

    def close(self) -> None:
        """Close the persistent connection pool (idempotent)."""
        if self._client is not None and self._owned:
            self._client.close()
            self._client = None

    def __enter__(self) -> NewRelicExporter:
        self.open()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    # ── public API ─────────────────────────────────────────────────────────

    def send_event(self, event: NewRelicEvent) -> None:
        """Submit a single :class:`NewRelicEvent`."""
        self.send_events([event])

    def send_events(self, events: list[NewRelicEvent]) -> None:
        """Submit multiple :class:`NewRelicEvent` objects in one request."""
        now = int(time.time())
        payload = []
        for e in events:
            item: dict[str, Any] = {"eventType": e.event_type, **e.attributes}
            item["timestamp"] = e.timestamp or now
            payload.append(item)
        self._do_post(self._events_url(), payload)

    def send_metric(self, metric: NewRelicMetric) -> None:
        """Submit a single :class:`NewRelicMetric`."""
        self.send_metrics([metric])

    def send_metrics(self, metrics: list[NewRelicMetric]) -> None:
        """Submit multiple :class:`NewRelicMetric` objects in one request."""
        now = int(time.time())
        data = []
        for m in metrics:
            item: dict[str, Any] = {
                "name": m.name,
                "type": m.metric_type.value,
                "value": m.value,
                "timestamp": now,
                "attributes": m.attributes,
            }
            if m.interval_ms is not None:
                item["interval.ms"] = m.interval_ms
            data.append(item)
        payload = [{"metrics": data}]
        self._do_post(self._metrics_url(), payload)

    # ── internal ───────────────────────────────────────────────────────────

    def _events_url(self) -> str:
        if self._cfg.region == NewRelicRegion.EU:
            return f"https://insights-collector.eu01.nr-data.net/v1/accounts/{self._cfg.account_id}/events"
        return f"https://insights-collector.nr-data.net/v1/accounts/{self._cfg.account_id}/events"

    def _metrics_url(self) -> str:
        if self._cfg.region == NewRelicRegion.EU:
            return "https://metric-api.eu.newrelic.com/metric/v1"
        return "https://metric-api.newrelic.com/metric/v1"

    def _make_client(self) -> httpx.Client:
        return httpx.Client(
            headers={
                "Api-Key": self._cfg.api_key,
                "Content-Type": "application/json",
            },
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
