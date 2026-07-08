"""Datadog export integration for SketchLog."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional

import httpx

from sketchlog.exporters.base import ExporterError


class MetricType(str, Enum):
    """Datadog metric submission type."""

    GAUGE = "gauge"
    COUNT = "count"
    RATE = "rate"


@dataclass(frozen=True)
class DatadogConfig:
    """Immutable configuration for :class:`DatadogExporter`.

    Args:
        api_key: Datadog API key.
        site: Datadog site, e.g. ``datadoghq.com`` (default) or ``datadoghq.eu``.
        metric_prefix: Optional prefix prepended to every metric name.
        default_tags: Tags added to every metric.
        timeout: Request timeout in seconds (default ``10.0``).
    """

    api_key: str
    site: str = "datadoghq.com"
    metric_prefix: str = ""
    default_tags: List[str] = field(default_factory=list)
    timeout: float = 10.0

    def __post_init__(self) -> None:
        if not self.api_key:
            raise ValueError("api_key must not be empty")
        if not self.site:
            raise ValueError("site must not be empty")


@dataclass
class DatadogMetric:
    """A single Datadog metric data point.

    Args:
        name: Metric name (without prefix).
        value: Numeric value.
        metric_type: Submission type (default ``GAUGE``).
        tags: Additional tags for this metric.
        timestamp: Unix timestamp in seconds (default: current time).
        host: Optional host name to associate with the metric.
    """

    name: str
    value: float
    metric_type: MetricType = MetricType.GAUGE
    tags: List[str] = field(default_factory=list)
    timestamp: Optional[float] = None
    host: Optional[str] = None


class DatadogExporter:
    """Sends metrics to the Datadog Metrics API v2.

    Can be used standalone or as a context manager::

        with DatadogExporter(cfg) as exp:
            exp.send_metric(DatadogMetric("requests", 42.0))

    Args:
        config: :class:`DatadogConfig` instance.
        client: Optional pre-configured :class:`httpx.Client`.
    """

    def __init__(self, config: DatadogConfig, client: Optional[httpx.Client] = None) -> None:
        self._config = config
        self._client = client

    def __enter__(self) -> "DatadogExporter":
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

    def send_metric(self, metric: DatadogMetric) -> None:
        """Send a single metric."""
        self.send_metrics([metric])

    def send_metrics(self, metrics: List[DatadogMetric]) -> None:
        """Send a batch of metrics."""
        series = []
        for m in metrics:
            name = (self._config.metric_prefix + m.name) if self._config.metric_prefix else m.name
            tags = list(self._config.default_tags) + list(m.tags)
            ts = m.timestamp if m.timestamp is not None else time.time()
            point: Dict[str, object] = {
                "metric": name,
                "type": m.metric_type.value,
                "points": [{"timestamp": int(ts), "value": m.value}],
                "tags": tags,
            }
            if m.host:
                point["resources"] = [{"name": m.host, "type": "host"}]
            series.append(point)
        self._do_send({"series": series})

    def _endpoint(self) -> str:
        return f"https://api.{self._config.site}/api/v2/series"

    def _make_client(self) -> httpx.Client:
        return httpx.Client(
            headers={"DD-API-KEY": self._config.api_key, "Content-Type": "application/json"},
            timeout=self._config.timeout,
        )

    def _do_send(self, payload: Dict[str, object]) -> None:
        owned = self._client is None
        _client: httpx.Client = self._client if self._client is not None else self._make_client()
        try:
            resp = _client.post(self._endpoint(), json=payload)
            try:
                resp.raise_for_status()
            except httpx.HTTPStatusError as exc:
                raise ExporterError(str(exc), status_code=exc.response.status_code) from exc
        except ExporterError:
            raise
        except httpx.TimeoutException as exc:
            raise ExporterError(f"Datadog send timed out: {exc}") from exc
        except httpx.RequestError as exc:
            raise ExporterError(f"Datadog send failed: {exc}") from exc
        finally:
            if owned:
                _client.close()
