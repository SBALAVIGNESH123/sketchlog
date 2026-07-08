# SketchLog Export Integrations

SketchLog ships with ready-made exporters for three popular observability
back-ends.  All exporters use **`httpx`** — already a SketchLog dependency —
so no additional packages need to be installed.

---

## Overview

| Exporter | Protocol | API |
|---|---|---|
| **Loki** | HTTP push | `POST /loki/api/v1/push` |
| **Datadog** | HTTP | `POST /api/v2/series` (Metrics API v2) |
| **New Relic** | HTTP | Events API & Metric API |

All exporters share the same design principles:

* **Frozen config dataclass** — immutable, validated at construction time.
* **Context-manager lifecycle** — `with Exporter(cfg) as exp:` reuses a
  single HTTP connection across multiple calls.
* **Standalone usage** — each method also works without a context manager
  (a transient connection is created per call).
* **Typed exceptions** — `ExporterError` with an optional `status_code`.
* **Zero extra dependencies** — `httpx` only.

---

## Installation

No extra install step is required.  Import directly:

```python
from sketchlog.exporters import LokiExporter, LokiConfig
```

---

## Loki Exporter

### Configuration

```python
from sketchlog.exporters import LokiConfig

cfg = LokiConfig(
    url="http://loki:3100",          # Required — Loki base URL
    labels={"app": "myapp",          # Default labels applied to every stream
            "env": "production"},
    auth_token="eyJ...",             # Optional Bearer token
    # username="admin",              # Optional HTTP basic auth
    # password="secret",
    timeout=10.0,                    # Request timeout in seconds
)
```

| Parameter | Type | Default | Description |
|---|---|---|---|
| `url` | `str` | — | Base URL of the Loki instance |
| `labels` | `dict[str, str]` | `{}` | Default label set for all streams |
| `auth_token` | `str \| None` | `None` | Bearer token |
| `username` | `str \| None` | `None` | Basic-auth username |
| `password` | `str \| None` | `None` | Basic-auth password |
| `timeout` | `float` | `10.0` | Request timeout (seconds) |

### Basic usage

```python
from sketchlog.exporters import LokiExporter, LokiConfig

cfg = LokiConfig(url="http://loki:3100", labels={"app": "api"})

# Context manager — recommended for multiple pushes
with LokiExporter(cfg) as exp:
    exp.push(["user authenticated", "token issued"])
    exp.push(["order placed"], extra_labels={"trace_id": "abc123"})

# Standalone — transient connection per call
exp = LokiExporter(cfg)
exp.push(["single log line"])
```

### Multiple streams

Push several label-differentiated streams in a single HTTP request:

```python
from sketchlog.exporters import LokiExporter, LokiConfig, LokiStream

cfg = LokiConfig(url="http://loki:3100")
streams = [
    LokiStream(labels={"service": "auth"},  lines=["login ok"]),
    LokiStream(labels={"service": "order"}, lines=["order #42 created"]),
]

with LokiExporter(cfg) as exp:
    exp.push_streams(streams)
```

### Custom timestamps

```python
import time

s = LokiStream(
    labels={"app": "batch"},
    lines=["job started", "job finished"],
    timestamps_ns=[time.time_ns() - 1_000_000_000, time.time_ns()],
)
```

### Error handling

```python
from sketchlog.exporters import ExporterError

try:
    with LokiExporter(cfg) as exp:
        exp.push(["msg"])
except ExporterError as e:
    print(f"Failed ({e.status_code}): {e}")
```

---

## Datadog Exporter

### Configuration

```python
from sketchlog.exporters import DatadogConfig

cfg = DatadogConfig(
    api_key="abc1234567890",         # Required — Datadog API key
    site="datadoghq.com",            # datadoghq.com (US) or datadoghq.eu (EU)
    metric_prefix="myapp",           # Optional prefix: "myapp.latency"
    default_tags=["env:prod",        # Tags applied to every metric
                  "region:us-east"],
    timeout=10.0,
)
```

| Parameter | Type | Default | Description |
|---|---|---|---|
| `api_key` | `str` | — | Datadog API key |
| `site` | `str` | `"datadoghq.com"` | Datadog site (US/EU) |
| `metric_prefix` | `str` | `""` | Prefix prepended to metric names |
| `default_tags` | `list[str]` | `[]` | Tags applied to every metric |
| `timeout` | `float` | `10.0` | Request timeout (seconds) |

### Metric types

```python
from sketchlog.exporters import MetricType

MetricType.GAUGE   # Instantaneous value (default)
MetricType.COUNT   # Cumulative count
MetricType.RATE    # Per-second rate
```

### Basic usage

```python
from sketchlog.exporters import DatadogExporter, DatadogConfig, DatadogMetric, MetricType

cfg = DatadogConfig(api_key="abc", default_tags=["env:prod"])

with DatadogExporter(cfg) as exp:
    exp.send_metric(DatadogMetric("request.count", 42, MetricType.COUNT))
    exp.send_metric(DatadogMetric("cpu.usage", 0.72))
```

### Sending multiple metrics

```python
metrics = [
    DatadogMetric("latency.p99", 0.120, tags=["endpoint:/api/v1"]),
    DatadogMetric("latency.p50", 0.045, tags=["endpoint:/api/v1"]),
    DatadogMetric("error.rate",  0.001, MetricType.RATE),
]

with DatadogExporter(cfg) as exp:
    exp.send_metrics(metrics)
```

### Per-metric options

```python
DatadogMetric(
    name="db.query.time",
    value=0.032,
    metric_type=MetricType.GAUGE,
    tags=["db:postgres", "query:select"],
    timestamp=1700000000,   # Unix epoch seconds (default: now)
    host="db-primary-01",   # Optional host resource
)
```

---

## New Relic Exporter

### Configuration

```python
from sketchlog.exporters import NewRelicConfig, NewRelicRegion

cfg = NewRelicConfig(
    api_key="NRAK-XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX",
    account_id="12345678",
    region=NewRelicRegion.US,   # or NewRelicRegion.EU
    timeout=10.0,
)
```

| Parameter | Type | Default | Description |
|---|---|---|---|
| `api_key` | `str` | — | New Relic Ingest API key |
| `account_id` | `str` | — | New Relic account ID (Events API) |
| `region` | `NewRelicRegion` | `US` | Data centre region |
| `timeout` | `float` | `10.0` | Request timeout (seconds) |

### Events API

```python
from sketchlog.exporters import NewRelicExporter, NewRelicConfig, NewRelicEvent

cfg = NewRelicConfig(api_key="NRAK-...", account_id="12345678")

with NewRelicExporter(cfg) as exp:
    exp.send_event(NewRelicEvent(
        event_type="PageView",
        attributes={"url": "/home", "duration_ms": 42, "user_id": "u1"},
    ))

    # Batch events
    exp.send_events([
        NewRelicEvent("OrderPlaced", {"order_id": "o1", "amount": 99.99}),
        NewRelicEvent("OrderShipped", {"order_id": "o1"}),
    ])
```

### Metric API

```python
from sketchlog.exporters import NewRelicMetric, NewRelicMetricType

with NewRelicExporter(cfg) as exp:
    # GAUGE — instantaneous value
    exp.send_metric(NewRelicMetric("cpu.usage", 0.72))

    # COUNT — requires interval_ms
    exp.send_metric(NewRelicMetric(
        "requests.total", 1500,
        metric_type=NewRelicMetricType.COUNT,
        interval_ms=60_000,
    ))

    # SUMMARY — requires interval_ms
    exp.send_metric(NewRelicMetric(
        "response.time", 0.045,
        metric_type=NewRelicMetricType.SUMMARY,
        interval_ms=60_000,
        attributes={"endpoint": "/api/orders"},
    ))
```

### EU region

```python
cfg = NewRelicConfig(
    api_key="NRAK-...",
    account_id="12345678",
    region=NewRelicRegion.EU,
)
```

---

## Error handling

All exporters raise `ExporterError` on failure:

```python
from sketchlog.exporters import ExporterError

try:
    exp.push(["log line"])
except ExporterError as e:
    if e.status_code == 429:
        # rate-limited — implement back-off
        ...
    elif e.status_code is None:
        # network timeout or connection error
        ...
    else:
        raise
```

---

## API reference

### `ExporterError`

```
ExporterError(message: str, *, status_code: int | None = None)
```

Base exception for all exporter errors.  `status_code` is the HTTP
response status code, or `None` for non-HTTP errors (timeout, DNS failure,
etc.).

### `LokiExporter`

| Method | Description |
|---|---|
| `push(lines, extra_labels=None)` | Push a list of log strings |
| `push_stream(stream)` | Push a single `LokiStream` |
| `push_streams(streams)` | Push multiple streams in one request |
| `close()` | Close the HTTP client |

### `DatadogExporter`

| Method | Description |
|---|---|
| `send_metric(metric)` | Send a single `DatadogMetric` |
| `send_metrics(metrics)` | Send multiple metrics in one API call |
| `close()` | Close the HTTP client |

### `NewRelicExporter`

| Method | Description |
|---|---|
| `send_event(event)` | Send a single `NewRelicEvent` |
| `send_events(events)` | Send multiple events in one request |
| `send_metric(metric)` | Send a single `NewRelicMetric` |
| `send_metrics(metrics)` | Send multiple metrics in one request |
| `close()` | Close the HTTP client |
