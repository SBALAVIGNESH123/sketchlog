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
    RATE = "rate"


@dataclass(frozen=True)
class DatadogConfig:
    """Immutable configuration for :class:`DatadogExporter`.

    Args:
        api_key: Datadog API key.
        site: Datadog site, e.g. ``datadoghq.com`` (default) or
            ``datadoghq.eu`` for the EU region.
        metric_prefix: Optional prefix prepended to every metric name
            (e.g. ``"sketchlog."``).
        default_tags: Tags applied to every submitted metric series.
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
        if not self.site:
            raise ValueError("site must not be empty")


@dataclass
class DatadogMetric:
    """A single Datadog metric series point.

    Args:
        name: Metric name (without prefix).
        value: Numeric metric value.
        metric_type: Submission type (GAUGE / COUNT / RATE).
        tags: Additional tags for this metric.
        timestamp: Unix epoch timestamp (auto-assigned if ``None``).
        host: Host name resource tag.
    """

    name: str
    value: float
    metric_type: MetricType = MetricType.GAUGE
    tags: list[str] = field(default_factory=list)
    timestamp: int | None = None
    host: str | None = None


class DatadogExporter:
    """Sends metrics to the Datadog Metrics API v2.

    Can be used standalone or as a context manager::

        with DatadogExporter(cfg) as exp:
            exp.send_metric(DatadogMetric("requests", 42))
    """

    def __init__(self, config: DatadogConfig, client: httpx.Client | None = None) -> None:
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

    def __enter__(self) -> DatadogExporter:
        self.open()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    # ── public API ─────────────────────────────────────────────────────────

    def send_metric(self, metric: DatadogMetric) -> None:
        """Submit a single :class:`DatadogMetric`."""
        self.send_metrics([metric])

    def send_metrics(self, metrics: list[DatadogMetric]) -> None:
        """Submit multiple :class:`DatadogMetric` objects in one request."""
        now = int(time.time())
        series = []
        for m in metrics:
            name = f"{self._cfg.metric_prefix}{m.name}" if self._cfg.metric_prefix else m.name
            tags = list(self._cfg.default_tags) + list(m.tags)
            point: dict[str, Any] = {
                "metric": name,
                "type": m.metric_type.value,
                "points": [{"timestamp": m.timestamp or now, "value": m.value}],
                "tags": tags,
            }
            if m.host:
                point["resources"] = [{"name": m.host, "type": "host"}]
            series.append(point)
        self._do_send({"series": series})

    # ── internal ───────────────────────────────────────────────────────────

    def _endpoint(self) -> str:
        return f"https://api.{self._cfg.site}/api/v2/series"

    def _make_client(self) -> httpx.Client:
        return httpx.Client(
            headers={"DD-API-KEY": self._cfg.api_key, "Content-Type": "application/json"},
            timeout=self._cfg.timeout,
        )

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
        except httpx.RequestError as exc:
            raise ExporterError(f"Datadog transport error: {exc}") from exc
        finally:
            if owned:
                client.close()
