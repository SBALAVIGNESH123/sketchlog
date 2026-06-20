# SketchLog Runbooks

This document outlines the Service Level Indicators (SLIs), Service Level Objectives (SLOs), and Runbooks for the SketchLog streaming metrics server.

## SLIs & SLOs

### Ingestion Success (SLI)
- **Metric**: `sum(rate(sketchlog_http_requests_total{status=~"2.."}[5m])) / sum(rate(sketchlog_http_requests_total[5m]))`
- **SLO Target**: 99.9%
- **Meaning**: What percentage of ingestion and read requests successfully complete without encountering an internal server error or rejection.

### Query Latency (SLI)
- **Metric**: `histogram_quantile(0.99, sum(rate(sketchlog_http_request_duration_seconds_bucket{method="GET", path=~"/v1/streams/.*/metrics"}[5m])) by (le))`
- **SLO Target**: < 10ms
- **Meaning**: 99% of metric retrieval queries should return in under 10 milliseconds.

---

## Alerts & Runbooks

### Alert: High Memory / Degraded Readiness
- **Trigger**: The `/ready` endpoint returns HTTP 503, indicating that the `psutil` memory usage threshold (>90%) has been breached.
- **Impact**: The load balancer will stop sending new traffic to the node. If all nodes breach this, global ingestion will fail.
- **Runbook**:
  1. Inspect the `sketchlog_active_streams` gauge. If it is near `SKETCHLOG_MAX_STREAMS`, the cache is full.
  2. If memory is exhausted *before* the stream limit is reached, it indicates that individual streams are consuming too much memory. Consider lowering `SKETCHLOG_MAX_BATCH_SIZE`.
  3. Action: Provision nodes with larger RAM, or horizontally scale out and partition the stream IDs using a consistent hash ring.

### Alert: High Eviction Rate / Cache Thrashing
- **Trigger**: `rate(sketchlog_stream_evictions_total[5m]) > 10`
- **Impact**: Streams are being deleted from the registry before their lifecycle is naturally complete. Subsequent reads to an evicted stream will return 404 or start a fresh, empty stream, causing data loss.
- **Runbook**:
  1. This occurs when the cardinality of active `stream_id`s exceeds `SKETCHLOG_MAX_STREAMS`.
  2. Action: Increase `SKETCHLOG_MAX_STREAMS` via environment variable and restart the process, or scale out to more nodes.

### Alert: Overload / High Rejection Rate
- **Trigger**: `rate(sketchlog_rejections_total[5m]) > 5`
- **Impact**: Clients are sending payloads larger than `SKETCHLOG_MAX_REQUEST_BYTES` and receiving 413 Payload Too Large responses.
- **Runbook**:
  1. Check the `reason` label on `sketchlog_rejections_total`.
  2. If clients legitimately need to send larger batches, increase `SKETCHLOG_MAX_REQUEST_BYTES`.
  3. Otherwise, instruct clients to flush batches more frequently to avoid dropping telemetry.

### Alert: Failed Releases / Data Format Changes
- **Trigger**: `sketchlog_http_requests_total{status="422"}` spikes immediately after a deployment.
- **Impact**: The ingestion payload schema or parameter validation has changed, rendering older clients incompatible.
- **Runbook**:
  1. Roll back the deployment immediately.
  2. Introduce backwards compatibility into the `EventBatch` Pydantic model for the deprecated fields before re-releasing.
