# OpenTelemetry Collector exporter

SketchLog already includes an in-process Python OpenTelemetry integration. The Collector exporter is different: it lets an existing OpenTelemetry Collector pipeline send selected metrics, traces, and logs into SketchLog without changing application code.

## What it does

The exporter converts OpenTelemetry signals into SketchLog event batches:

| Signal | Conversion |
|---|---|
| Selected metrics | mapped to latency, event, or unique streams |
| Traces | span durations in milliseconds |
| Logs | event counts from `EventName`, severity, or `log_record` fallback |

It writes to the existing SketchLog ingestion API:

```text
POST /v1/namespaces/{namespace}/streams/{stream}/events
```

## Example Collector config

```yaml
receivers:
  otlp:
    protocols:
      grpc:
      http:

exporters:
  sketchlog:
    endpoint: http://sketchlog:8000
    namespace: production
    auth_token: ${env:SKETCHLOG_AUTH_TOKEN}
    timeout: 10s
    max_batch_items: 1000
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

service:
  pipelines:
    metrics:
      receivers: [otlp]
      exporters: [sketchlog]
    traces:
      receivers: [otlp]
      exporters: [sketchlog]
    logs:
      receivers: [otlp]
      exporters: [sketchlog]
```

## Metric mapping

Each metric mapping has:

- `name`: OTEL metric name to match.
- `stream`: SketchLog stream to write.
- `kind`: `latency`, `event`, or `unique`.
- `event_name`: required for `event` mappings.
- `scale`: optional multiplier, useful for converting seconds to milliseconds.

## Authentication

If the SketchLog server uses `SKETCHLOG_AUTH_TOKEN`, set `auth_token` from an environment variable. The exporter sends it as `X-SketchLog-Auth-Token`.

## Module location

The Collector module lives in:

```text
contrib/otelcol-sketchlog-exporter
```

It is intended for use with a custom Collector build.
