# Smart SLO engine

The SLO engine derives a latency target from an explicit historical stream,
then estimates the current fraction above that target. Percentages are supplied
as fractions: `0.005` means 0.5%.

```http
POST /v1/streams/api-gateway/slo/evaluate
Content-Type: application/json

{
  "baseline_stream_id": "api-gateway-baseline",
  "target_percentile": 0.99,
  "budget_percent": 0.005
}
```

The response contains `target_latency`, `current_events`, `current_errors`,
`current_error_rate`, `burn_rate`, and `is_alerting`. DDSketch cannot recover
ordering within a bucket, so threshold counts deliberately use a conservative
upper bound; an alert may fire early but will not hide a possible violation
inside the threshold bucket.

To derive only the target:

```bash
curl "http://localhost:8000/v1/streams/api-gateway-baseline/slo/recommend?target_percentile=0.99&budget_percent=0.005"
```

Both endpoints reject empty baselines and values outside `(0, 1)`.
