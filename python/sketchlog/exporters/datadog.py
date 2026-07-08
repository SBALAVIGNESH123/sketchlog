"""Datadog Metrics API v2 exporter for SketchLog."""
from __future__ import annotations
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any
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
        api_key: Datadog API key (required).
        site: Datadog site URL, e.g. ``datadoghq.com`` (default) or ``datadoghq.eu``.
        timeout: HTTP request timeout in seconds (default 10).
        prefix: Optional metric name prefix, e.g. ``myapp.``.
        default_tags: Optional list of default tags applied to every metric.
    """

    api_key: str
    site: str = "datadoghq.com"
    timeout: float = 10.0
    prefix: str = ""
    default_tags: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.api_key:
            raise ValueError("api_key must not be empty")
        if not self.site:
            raise ValueError("site must not be empty")


@dataclass
class DatadogMetric:
    """A single Datadog metric point.

    Args:
        name: Metric name (without prefix).
        value: Numeric metric value.
        type: :class:`MetricType` (default GAUGE).
        tags: Additional tags for this metric.
        timestamp: Optional UNIX timestamp (seconds). Current time used if omitted.
        host: Optional host tag.
    """

    name: str
    value: float
    type: MetricType = MetricType.GAUGE
    tags: list[str] = field(default_factory=list)
    timestamp: int | None = None
    host: str | None = None


class DatadogExporter:
    """Synchronous Datadog metrics exporter.

    Supports context-manager usage::

        with DatadogExporter(cfg) as exp:
            exp.send_metric(DatadogMetric("cpu", 42.0))

    Args:
        config: :class:`DatadogConfig` instance.
        client: Optional pre-built ``httpx.Client`` (for testing / reuse).
    """

    def __init__(self, config: DatadogConfig, client: httpx.Client | None = None) -> None:
        self._cfg = config
        self._client = client
        self._owned = client is None

    # ── context manager ───────────────────────────────────────────────────────
    def __enter__(self) -> "DatadogExporter":
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

    # ── public API ────────────────────────────────────────────────────────────
    def send_metric(self, metric: DatadogMetric) -> None:
        """Send a single metric to Datadog.

        Args:
            metric: :class:`DatadogMetric` to send.

        Raises:
            ExporterError: On HTTP or transport failure.
        """
        self.send_metrics([metric])

    def send_metrics(self, metrics: list[DatadogMetric]) -> None:
        """Send multiple metrics in one request.

        Args:
            metrics: List of :class:`DatadogMetric` to send.

        Raises:
            ExporterError: On HTTP or transport failure.
        """
        series = []
        for m in metrics:
            name = f"{self._cfg.prefix}{m.name}" if self._cfg.prefix else m.name
            ts = m.timestamp if m.timestamp is not None else int(time.time())
            tags = list(self._cfg.default_tags) + list(m.tags)
            point: dict[str, Any] = {
                "metric": name,
                "type": m.type.value,
                "points": [{"timestamp": ts, "value": m.value}],
            }
            if tags:
                point["tags"] = tags
            if m.host:
                point["resources"] = [{"name": m.host, "type": "host"}]
            series.append(point)
        self._do_send({"series": series})

    # ── internals ─────────────────────────────────────────────────────────────
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
