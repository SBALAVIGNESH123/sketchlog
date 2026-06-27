# Smart SLO Engine

The **Smart SLO Engine** introduces a declarative way to track Service Level Objectives (SLOs) directly against the streaming sketch data, without needing complex PromQL queries or heavy time-series infrastructure.

## Defining SLOs

An SLO is defined by a target percentile (e.g., p99 latency must be < 200ms) or an error rate threshold (e.g., HTTP 5xx < 1%).

You can evaluate SLOs in real-time using the `/slo/evaluate` endpoint.

### Example: Latency SLO

```json
POST /v1/namespaces/default/streams/api-gateway/slo/evaluate
{
  "baseline_stream_id": "api-gateway-baseline",
  "target_percentile": 0.99,
  "budget_percent": 5.0
}
```

Response:
```json
{
  "stream_id": "api-gateway",
  "baseline_stream_id": "api-gateway-baseline",
  "target_percentile": 0.99,
  "target_latency": 250.0,
  "budget_percent": 5.0,
  "slo_met": true,
  "current_latency": 184.2,
  "margin_ms": 65.8,
  "status": "HEALTHY"
}
```

## Self-Writing SLOs (Auto-Calibration)

The engine can also analyze the historical sketch of a service and automatically recommend reasonable SLOs based on the past 7 days of performance.

To request an auto-calibrated SLO, send an empty request to the recommendation endpoint:

```bash
curl -X GET "http://localhost:8000/v1/streams/checkout-service/slo/recommend"
```

The system will return a mathematically sound set of objectives based on the service's historical bounds.
