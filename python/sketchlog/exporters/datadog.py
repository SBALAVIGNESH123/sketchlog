"""Datadog Metrics API v2 exporter for SketchLog."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

import httpx

from .base import ExporterError


class MetricType(str, Enum):
    """Datadog metric type."""

    GAUGE = "gauge"
    COUNT = "count"
    RATE = "rate"


@dataclass(frozen=True)
class DatadogConfig:
    """Configuration for the Datadog exporter.

    Args:
        api_key: Datadog API key.
        site: Datadog site hostname (default ``datadoghq.com``). Use
            ``datadoghq.eu`` for the EU region.
        metric_prefix: Optional prefix prepended to every metric name.
        default_tags: Tags added to every metric sent.
        timeout: HTTP request timeout in seconds.
    """

    api_key: str
    site: str = "datadoghq.com"
    metric_prefix: str = ""
    default_tags: List[str] = field(default_factory=list)
    timeout: float = 10.0

    def __post_init__(self) -> None:
        if not self.api_key:
            raise ValueError("api_key must not be empty")
        if self.timeout <= 0:
            raise ValueError("timeout must be > 0")


@dataclass
class DatadogMetric:
    """A single Datadog metric data point.

    Args:
        name: Metric name (without prefix).
        value: Numeric value.
        metric_type: GAUGE, COUNT, or RATE.
        tags: Additional tags for this metric.
        timestamp: Unix timestamp in seconds (defaults to now).
        host: Optional host tag.
    """

    name: str
    value: float
    metric_type: MetricType = MetricType.GAUGE
    tags: List[str] = field(default_factory=list)
    timestamp: Optional[int] = None
    host: Optional[str] = None


class DatadogExporter:
    """Send metrics to the Datadog Metrics API v2.

    Example::

        cfg = DatadogConfig(api_key="your-key", metric_prefix="myapp.")
        with DatadogExporter(cfg) as exp:
            exp.send_metric(DatadogMetric("requests", 42, MetricType.COUNT,
                                          tags=["env:prod"]))
    """

    _DD_METRIC_TYPES = {MetricType.GAUGE: 3, MetricType.COUNT: 1, MetricType.RATE: 2}

    def __init__(self, config: DatadogConfig, client: Optional[httpx.Client] = None) -> None:
        self._config = config
        self._client: Optional[httpx.Client] = client
        self._owned = client is None

    def _make_client(self) -> httpx.Client:
        return httpx.Client(
            headers={"DD-API-KEY": self._config.api_key, "Content-Type": "application/json"},
            timeout=self._config.timeout,
        )

    def _endpoint(self) -> str:
        return f"https://api.{self._config.site}/api/v2/series"

    def open(self) -> "DatadogExporter":
        """Open the underlying HTTP client."""
        if self._client is None:
            self._client = self._make_client()
        return self

    def close(self) -> None:
        """Close the underlying HTTP client."""
        if self._client is not None and self._owned:
            self._client.close()
            self._client = None

    def __enter__(self) -> "DatadogExporter":
        return self.open()

    def __exit__(self, *_: Any) -> None:
        self.close()

    def _build_series(self, metric: DatadogMetric) -> Dict[str, Any]:
        cfg = self._config
        name = f"{cfg.metric_prefix}{metric.name}" if cfg.metric_prefix else metric.name
        ts = metric.timestamp if metric.timestamp is not None else int(time.time())
        tags = list(cfg.default_tags) + list(metric.tags)
        series: Dict[str, Any] = {
            "metric": name,
            "type": self._DD_METRIC_TYPES[metric.metric_type],
            "points": [{"timestamp": ts, "value": metric.value}],
            "tags": tags,
        }
        if metric.host:
            series["resources"] = [{"name": metric.host, "type": "host"}]
        return series

    def _do_send(self, payload: Dict[str, Any]) -> None:
        # Use local variable so mypy can narrow Optional[httpx.Client] -> httpx.Client
        client = self._client
        if client is None:
            client = self._make_client()
            owned = True
        else:
            owned = False
        try:
            resp = client.post(self._endpoint(), json=payload)
            try:
                resp.raise_for_status()
            except httpx.HTTPStatusError as exc:
                raise ExporterError(str(exc), status_code=exc.response.status_code) from exc
        except ExporterError:
            raise
        except httpx.TimeoutException as exc:
            raise ExporterError(f"Datadog send timed out: {exc}") from exc
        except httpx.RequestError as exc:
            raise ExporterError(f"Datadog connection error: {exc}") from exc
        finally:
            if owned:
                client.close()

    def send_metric(self, metric: DatadogMetric) -> None:
        """Send a single metric to Datadog."""
        self._do_send({"series": [self._build_series(metric)]})

    def send_metrics(self, metrics: List[DatadogMetric]) -> None:
        """Send multiple metrics in a single request."""
        self._do_send({"series": [self._build_series(m) for m in metrics]})
