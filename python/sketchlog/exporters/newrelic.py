"""New Relic Events and Metric API exporter for SketchLog."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Union

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
        api_key: New Relic Ingest API key (``NRII-...``).
        account_id: New Relic account ID (required for Events API).
        region: ``US`` (default) or ``EU``.
        timeout: HTTP request timeout in seconds.
    """

    api_key: str
    account_id: str = ""
    region: NewRelicRegion = NewRelicRegion.US
    timeout: float = 10.0

    def __post_init__(self) -> None:
        if not self.api_key:
            raise ValueError("api_key must not be empty")
        if self.timeout <= 0:
            raise ValueError("timeout must be > 0")


@dataclass
class NewRelicEvent:
    """A New Relic custom event.

    Args:
        event_type: Event type name (alphanumeric + underscore).
        attributes: Dict of event attributes.
        timestamp: Unix timestamp in seconds (defaults to now).
    """

    event_type: str
    attributes: Dict[str, Any] = field(default_factory=dict)
    timestamp: Optional[int] = None


@dataclass
class NewRelicMetric:
    """A New Relic metric data point.

    Args:
        name: Metric name.
        value: Numeric value. For SUMMARY metrics, pass a dict with keys
            ``count``, ``sum``, ``min``, ``max``.
        metric_type: GAUGE, COUNT, or SUMMARY.
        attributes: Additional attributes/tags.
        interval_ms: Interval in milliseconds (required for COUNT and SUMMARY).
    """

    name: str
    value: Union[float, Dict[str, float]]
    metric_type: NewRelicMetricType = NewRelicMetricType.GAUGE
    attributes: Dict[str, Any] = field(default_factory=dict)
    interval_ms: Optional[int] = None


class NewRelicExporter:
    """Send events and metrics to New Relic.

    Example::

        cfg = NewRelicConfig(api_key="NRII-...", account_id="12345")
        with NewRelicExporter(cfg) as exp:
            exp.send_event(NewRelicEvent("PageView", {"url": "/home"}))
            exp.send_metric(NewRelicMetric("cpu.usage", 0.72,
                                           NewRelicMetricType.GAUGE))
    """

    def __init__(self, config: NewRelicConfig, client: Optional[httpx.Client] = None) -> None:
        self._config = config
        self._client: Optional[httpx.Client] = client
        self._owned = client is None

    def _make_client(self) -> httpx.Client:
        return httpx.Client(
            headers={"Api-Key": self._config.api_key, "Content-Type": "application/json"},
            timeout=self._config.timeout,
        )

    def _events_url(self) -> str:
        cfg = self._config
        if cfg.region == NewRelicRegion.EU:
            return (
                f"https://insights-collector.eu01.nr-data.net"
                f"/v1/accounts/{cfg.account_id}/events"
            )
        return f"https://insights-collector.newrelic.com/v1/accounts/{cfg.account_id}/events"

    def _metrics_url(self) -> str:
        if self._config.region == NewRelicRegion.EU:
            return "https://metric-api.eu.newrelic.com/metric/v1"
        return "https://metric-api.newrelic.com/metric/v1"

    def open(self) -> "NewRelicExporter":
        """Open the underlying HTTP client."""
        if self._client is None:
            self._client = self._make_client()
        return self

    def close(self) -> None:
        """Close the underlying HTTP client."""
        if self._client is not None and self._owned:
            self._client.close()
            self._client = None

    def __enter__(self) -> "NewRelicExporter":
        return self.open()

    def __exit__(self, *_: Any) -> None:
        self.close()

    def _do_post(self, url: str, payload: Any) -> None:
        # Use local variable so mypy can narrow Optional[httpx.Client] -> httpx.Client
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
        except httpx.RequestError as exc:
            raise ExporterError(f"New Relic connection error: {exc}") from exc
        finally:
            if owned:
                client.close()

    def _build_event(self, event: NewRelicEvent) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"eventType": event.event_type}
        payload.update(event.attributes)
        payload["timestamp"] = (
            event.timestamp if event.timestamp is not None else int(time.time())
        )
        return payload

    def _build_metric(self, metric: NewRelicMetric) -> Dict[str, Any]:
        ts_ms = int(time.time() * 1000)
        m: Dict[str, Any] = {
            "name": metric.name,
            "type": metric.metric_type.value,
            "value": metric.value,
            "timestamp": ts_ms,
            "attributes": metric.attributes,
        }
        if metric.interval_ms is not None:
            m["interval.ms"] = metric.interval_ms
        return m

    def send_event(self, event: NewRelicEvent) -> None:
        """Send a single custom event to New Relic."""
        if not self._config.account_id:
            raise ValueError("account_id is required to send events")
        self._do_post(self._events_url(), [self._build_event(event)])

    def send_events(self, events: List[NewRelicEvent]) -> None:
        """Send multiple custom events in a single request."""
        if not self._config.account_id:
            raise ValueError("account_id is required to send events")
        self._do_post(self._events_url(), [self._build_event(e) for e in events])

    def send_metric(self, metric: NewRelicMetric) -> None:
        """Send a single metric to New Relic."""
        self._do_post(self._metrics_url(), [{"metrics": [self._build_metric(metric)]}])

    def send_metrics(self, metrics: List[NewRelicMetric]) -> None:
        """Send multiple metrics in a single request."""
        self._do_post(
            self._metrics_url(),
            [{"metrics": [self._build_metric(m) for m in metrics]}],
        )
