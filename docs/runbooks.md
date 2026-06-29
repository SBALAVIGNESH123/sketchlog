# SketchLog Runbooks

This document outlines the Service Level Indicators (SLIs), Service Level Objectives (SLOs), and Runbooks for the SketchLog streaming metrics server.

## SLIs & SLOs

### Ingestion Success (SLI)
- **Metric**: `sum(rate(sketchlog_http_requests_total{status=~"2..",path=~".*/events"}[5m])) / sum(rate(sketchlog_http_requests_total{path=~".*/events"}[5m]))`
- **SLO Target**: 99.9%
- **Meaning**: What percentage of ingestion and read requests successfully complete without encountering an internal server error or rejection.

### Query Latency (SLI)
- **Metric**: `histogram_quantile(0.99, sum(rate(sketchlog_http_request_duration_seconds_bucket{method="GET",path=~".*/metrics"}[5m])) by (le))`
- **SLO Target**: < 10ms
- **Meaning**: 99% of metric retrieval queries should return in under 10 milliseconds.

---

## Alerts & Runbooks

### Alert: High Memory / Degraded Readiness
- **Trigger**: `/ready` returns HTTP 503. Inspect the one active
  `sketchlog_readiness_status{cause=...}` series to distinguish configuration,
  memory pressure, memory-measurement failure, and storage failure.
- **Impact**: The load balancer will stop sending new traffic to the node. If all nodes breach this, global ingestion will fail.
- **Runbook**:
  1. Inspect the `sketchlog_active_streams` gauge. If it is near `SKETCHLOG_MAX_STREAMS`, the cache is full.
  2. If memory is exhausted *before* the stream limit is reached, inspect process overhead and the configured memory limit. Lowering batch size reduces transient request memory but not the resident default sketch size.
  3. Action: Lower `SKETCHLOG_MAX_STREAMS`, provision more RAM, enable Sketch Mesh for coherent replicas, or externally partition independent standalone nodes.

### Alert: High Eviction Rate / Cache Thrashing
- **Trigger**: `rate(sketchlog_stream_evictions_total[5m]) > 10`
- **Impact**: Without storage, evicted streams are ephemeral and subsequent reads return 404 or start fresh. With storage, eviction waits for a successful save, but sustained churn increases database traffic and reload latency.
- **Runbook**:
  1. This occurs when the cardinality of active `stream_id`s exceeds `SKETCHLOG_MAX_STREAMS`.
  2. Action: Increase `SKETCHLOG_MAX_STREAMS` via environment variable and restart the process, or scale out to more nodes.

### Alert: Overload / High Rejection Rate
- **Trigger**: `rate(sketchlog_rejections_total[5m]) > 5`
- **Impact**: Requests are being rejected for the bounded reason shown by the metric label, such as body size, batch size, or capacity.
- **Runbook**:
  1. Check the `reason` label on `sketchlog_rejections_total`.
  2. If clients legitimately need to send larger batches, increase `SKETCHLOG_MAX_REQUEST_BYTES`.
  3. Otherwise, instruct clients to flush batches more frequently to avoid dropping telemetry.

### Alert: Mesh deletion capacity exhausted
- **Trigger**: Delete requests return HTTP 503 with `local mesh tombstone capacity exhausted`.
- **Impact**: New deletes are refused so old peer snapshots cannot be silently resurrected.
- **Runbook**:
  1. Confirm durable storage is healthy and back up the tombstone table.
  2. Increase `SKETCHLOG_MAX_LOCAL_TOMBSTONES` within its documented limit and restart every node with consistent configuration.
  3. Do not manually remove tombstones unless every possible stale origin snapshot has been retired.

### Alert: Failed Releases / Data Format Changes
- **Trigger**: `sketchlog_http_requests_total{status="422"}` spikes immediately after a deployment.
- **Impact**: The ingestion payload schema or parameter validation has changed, rendering older clients incompatible.
- **Runbook**:
  1. Roll back the deployment immediately.
  2. Introduce backwards compatibility into the `EventBatch` Pydantic model for the deprecated fields before re-releasing.
