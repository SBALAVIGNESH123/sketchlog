# Integrations

SketchLog is built to fit neatly into modern observability stacks without creating architectural friction.

## OpenTelemetry Exporter

You can seamlessly bridge SketchLog into any OpenTelemetry-compatible observability backend (like Jaeger, Datadog, Honeycomb, or New Relic) using the built-in OTLP asynchronous integration. 

This exporter works by registering asynchronous OTel instruments (like `ObservableGauge`), mapping your StreamLog percentiles, cardinality estimates, and event frequencies into OTLP metrics. It guarantees zero memory duplication and respects OpenTelemetry temporality.

```python
from sketchlog import StreamLog
from sketchlog.integrations.opentelemetry import SketchLogOTelPublisher

log = StreamLog()
# You can use the high-level publisher to immediately spin up a Periodic MetricReader
publisher = SketchLogOTelPublisher(
    streamlog=log, 
    export_interval_millis=60000, 
    export_events=["cache_miss", "db_query"]
)

log.add_latency(42.0)
log.add_unique("user_123")
log.add_event("cache_miss")

# At shutdown
publisher.shutdown()
```

If you prefer to wire it into an existing `MeterProvider`, you can instantiate `OpenTelemetryAdapter` directly.

## Prometheus Exporter

Easily expose sketch metrics to Prometheus using the built-in HTTP exporter. It exports p50/p95/p99 quantiles, unique counts, total events, and memory usage.

```python
from sketchlog import ThreadSafeStreamLog
from sketchlog.integrations.prometheus import PrometheusExporter

log = ThreadSafeStreamLog()
exporter = PrometheusExporter(log)

# Starts a background HTTP server on port 9090
exporter.start(port=9090)

# Add metrics in your main thread
log.add_latency(42.0)
```

## FastAPI / Starlette Middleware

Track request latency, endpoint hits, and status codes across your entire API without configuring separate prometheus clients or statsd servers.

```python
from fastapi import FastAPI
from sketchlog import StreamLog
from sketchlog.integrations.fastapi import SketchLogMiddleware

app = FastAPI()
log = StreamLog()

# Pass the log instance explicitly
app.add_middleware(SketchLogMiddleware, log=log)

@app.get("/api/users")
def get_users():
    return {"status": "ok"}
```

