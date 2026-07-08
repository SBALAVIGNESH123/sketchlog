"""Datadog export integration for SketchLog.

Sends metric series to the Datadog Metrics API v2
(``POST https://api.{site}/api/v2/series``).  Supports GAUGE, COUNT,
and RATE metric types, per-metric tags, host resources, metric name
prefixing, and both standalone and context-manager usage patterns.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import httpx

from sketchlog.exporters.base import ExporterError


class MetricType(str, Enum):
    """Datadog metric submission type.

    See https://docs.datadoghq.com/metrics/types/ for semantics.
    """

    GAUGE = "gauge"
    COUNT = "count"
    RATE  = "rate"


@dataclass(frozen=True)
class DatadogConfig:
    """Immutable configuration for :class:`DatadogExporter`.

    Args:
        api_key: Datadog API key (``DD-API-KEY`` header).
        site: Datadog site domain, e.g. ``datadoghq.com`` (default) or
            ``datadoghq.eu`` for the EU region.
        metric_prefix: Optional prefix prepended to every metric name,
            e.g. ``"myapp"`` → metric ``"latency"`` becomes
            ``"myapp.latency"``.
        default_tags: Tags applied to every metric, e.g.
            ``("env:prod", "region:us-east-1")``.
        timeout: Request timeout in seconds (default ``10.0``).
    """

    api_key: str
    site: str = "datadoghq.com"
    metric_prefix: str = ""
    default_tags: list[str] = field(default_factory=list)
    timeout: float = 10.0

    def __post_init__(self) -> None:
        if not self.api_key:
            raise ValueError("api_key must not be empty")
        if self.timeout <= 0:
            raise ValueError("timeout must be positive")


@dataclass
class DatadogMetric:
    """A single Datadog metric data point.

    Args:
        name: Metric name (without prefix).
        value: Numeric value.
        metric_type: Submission type (default :attr:`MetricType.GAUGE`).
        tags: Per-metric tags merged with config ``default_tags``.
        timestamp: Unix epoch seconds.  Defaults to *now* when ``None``.
        host: Optional host resource name.
    """

    name: str
    value: float
    metric_type: MetricType = MetricType.GAUGE
    tags: list[str] = field(default_factory=list)
    timestamp: int | None = None
    host: str | None = None

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("name must not be empty")


class DatadogExporter:
    """Sends metrics to the Datadog Metrics API v2.

    Use as a context manager for connection reuse::

        cfg = DatadogConfig(api_key="abc", default_tags=["env:prod"])
        with DatadogExporter(cfg) as exp:
            exp.send_metric(DatadogMetric("request.count", 42, MetricType.COUNT))

    Or call :meth:`send_metric` / :meth:`send_metrics` directly.
    """

    def __init__(self, config: DatadogConfig) -> None:
        self._config = config
        self._client: httpx.Client | None = None

    # ── lifecycle ────────────────────────────────────────────────────────────

    def __enter__(self) -> "DatadogExporter":
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
            headers={"DD-API-KEY": self._config.api_key},
            timeout=self._config.timeout,
        )

    def _endpoint(self) -> str:
        return f"https://api.{self._config.site}/api/v2/series"

    def _full_name(self, name: str) -> str:
        p = self._config.metric_prefix
        return f"{p}.{name}" if p else name

    def _build_payload(self, metrics: list[DatadogMetric]) -> dict[str, Any]:
        series: list[dict[str, Any]] = []
        for m in metrics:
            ts = m.timestamp if m.timestamp is not None else int(time.time())
            tags = list(self._config.default_tags) + m.tags
            point: dict[str, Any] = {
                "metric": self._full_name(m.name),
                "type": m.metric_type.value,
                "points": [{"timestamp": ts, "value": m.value}],
                "tags": tags,
            }
            if m.host:
                point["resources"] = [{"name": m.host, "type": "host"}]
            series.append(point)
        return {"series": series}

    def _do_send(self, payload: dict[str, Any]) -> None:
        client = self._client
        owned = client is None
        if owned:
            client = self._make_client()
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
        finally:
            if owned:
                client.close()

    # ── public API ───────────────────────────────────────────────────────────

    def send_metric(self, metric: DatadogMetric) -> None:
        """Send a single metric to Datadog.

        Args:
            metric: The metric to submit.

        Raises:
            ExporterError: On HTTP error or network timeout.
        """
        self.send_metrics([metric])

    def send_metrics(self, metrics: list[DatadogMetric]) -> None:
        """Send multiple metrics in a single API call.

        Args:
            metrics: Non-empty list of metrics.

        Raises:
            ValueError: If *metrics* is empty.
            ExporterError: On HTTP error or network timeout.
        """
        if not metrics:
            raise ValueError("metrics must not be empty")
        self._do_send(self._build_payload(metrics))
