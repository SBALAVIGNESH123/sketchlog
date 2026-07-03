# SketchLog OpenTelemetry Collector Exporter

This module adds a Collector-level SketchLog exporter. It is separate from the in-process Python OpenTelemetry integration: the exporter lets teams route telemetry from a standard OpenTelemetry Collector deployment into SketchLog without changing application code.

## Supported signals

- Metrics: selected OTEL metric names are mapped to SketchLog latency, event, or unique streams.
- Traces: span durations are exported as latency samples.
- Logs: log records are exported as event counts.

The exporter writes to SketchLog's existing HTTP ingestion API:

```text
POST /v1/namespaces/{namespace}/streams/{stream}/events
```

## Example

```yaml
exporters:
  sketchlog:
    endpoint: http://sketchlog:8000
    namespace: production
    auth_token: ${env:SKETCHLOG_AUTH_TOKEN}
    metrics:
      - name: http.server.duration
        stream: api.latency
        kind: latency
        scale: 1000
      - name: app.errors
        stream: app.events
        kind: event
        event_name: error
    span_duration_stream: traces.duration
    log_event_stream: logs.events
```

Use this module with a custom Collector build via the OpenTelemetry Collector Builder.

## Local validation

```bash
go test ./...
```
