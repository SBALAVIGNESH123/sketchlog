"""SketchLog export integrations — Loki, Datadog, and New Relic.

Provides ready-made exporters that forward SketchLog data to popular
observability backends.  All exporters use ``httpx`` (already a
SketchLog dependency) — no extra packages required.

Quick start::

    from sketchlog.exporters import LokiExporter, LokiConfig

    cfg = LokiConfig(url="http://loki:3100", labels={"app": "myapp"})
    with LokiExporter(cfg) as exp:
        exp.push(["user logged in", "order placed"])
"""
from __future__ import annotations

from sketchlog.exporters.base import ExporterError
from sketchlog.exporters.datadog import DatadogConfig, DatadogExporter, DatadogMetric, MetricType
from sketchlog.exporters.loki import LokiConfig, LokiExporter, LokiStream
from sketchlog.exporters.newrelic import (
    NewRelicConfig,
    NewRelicEvent,
    NewRelicExporter,
    NewRelicMetric,
    NewRelicMetricType,
    NewRelicRegion,
)

__all__ = [
    "ExporterError",
    "LokiConfig",
    "LokiExporter",
    "LokiStream",
    "DatadogConfig",
    "DatadogExporter",
    "DatadogMetric",
    "MetricType",
    "NewRelicConfig",
    "NewRelicEvent",
    "NewRelicExporter",
    "NewRelicMetric",
    "NewRelicMetricType",
    "NewRelicRegion",
]
