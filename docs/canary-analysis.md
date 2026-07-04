# Canary release analysis

SketchLog can compare a baseline stream against a candidate stream and return a release risk verdict for canary deployments, rollouts, and before/after deployment checks.

This workflow is built on existing SketchLog primitives:

- sketch diffing for distribution drift
- Smart SLO burn-rate evaluation
- anomaly scoring
- optional event-count deltas for errors or other release signals
- namespace-aware stream lookup

It does not require raw event storage.

## HTTP API

```bash
curl -X POST http://localhost:8000/v1/namespaces/production/streams/canary.checkout.latency/canary/analyze \
  -H 'Content-Type: application/json' \
  -d '{
    "baseline_stream_id": "stable.checkout.latency",
    "error_event_name": "checkout_error",
    "target_percentile": 0.995,
    "budget_percent": 0.005
  }'
```

The default namespace route also works:

```bash
curl -X POST http://localhost:8000/v1/streams/canary.checkout.latency/canary/analyze \
  -H 'Content-Type: application/json' \
  -d '{"baseline_stream_id":"stable.checkout.latency"}'
```

## Verdicts

The response includes a machine-readable `verdict`:

- `safe` means the candidate stayed inside configured guardrails.
- `warning` means one or more signals degraded but did not cross rollback thresholds.
- `rollback_recommended` means a rollback-level threshold was crossed.

Example response excerpt:

```json
{
  "verdict": "warning",
  "confidence": 0.42,
  "reasons": ["p99 latency increased by 18.4%", "SLO burn rate is 1.80x"],
  "latency": {
    "shift_percent": {"p50": 4.1, "p95": 9.8, "p99": 18.4}
  },
  "distribution": {
    "ks_statistic": 0.11,
    "wasserstein_distance": 24.2
  }
}
```

## Thresholds

Every guardrail can be tuned per request:

```json
{
  "baseline_stream_id": "stable.checkout.latency",
  "warning_p99_shift_percent": 10.0,
  "rollback_p99_shift_percent": 35.0,
  "warning_slo_burn_rate": 1.0,
  "rollback_slo_burn_rate": 4.0,
  "warning_ks_statistic": 0.12,
  "rollback_ks_statistic": 0.30
}
```

Use stricter thresholds for high-risk services and looser thresholds for noisy staging traffic.

## Production notes

- Use namespaces to compare `production` and `canary` streams without naming collisions.
- Include `error_event_name` when you want the release decision to account for error events, not only latency.
- Keep `min_latency_count` above the smallest sample size you trust for rollout decisions.
- Canary analysis is a decision aid. It should complement deployment policy, not replace human incident response.
