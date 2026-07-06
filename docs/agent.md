# SketchLog Agent

`sketchlog-agent` is a lightweight collector that runs beside an application and forwards selected telemetry into SketchLog streams.

This first production slice focuses on a safe, dependency-free path: scrape Prometheus text endpoints, map selected metrics into SketchLog streams, and forward latency samples, event counts, or unique values to the existing SketchLog ingestion API.

The agent is different from the eBPF universal collector. Use the eBPF collector for kernel-level TCP latency signals. Use `sketchlog-agent` when you want a simple sidecar-style bridge from Prometheus exposition to SketchLog.

## Example configuration

```json
{
  "server": {
    "endpoint": "http://localhost:8000",
    "namespace": "production",
    "auth_token_env": "SKETCHLOG_AGENT_TOKEN",
    "timeout_seconds": 3
  },
  "interval_seconds": 15,
  "max_batch_items": 1000,
  "prometheus": {
    "targets": [
      {
        "name": "checkout-api",
        "url": "http://checkout-api:9100/metrics",
        "timeout_seconds": 2
      }
    ]
  },
  "mappings": [
    {
      "metric": "http_request_duration_seconds",
      "kind": "latency",
      "stream": "checkout.latency",
      "value_scale": 1000,
      "label_filters": {"route": "/checkout"}
    },
    {
      "metric": "http_requests_total",
      "kind": "event",
      "stream": "checkout.events",
      "event_name": "http_requests",
      "counter_mode": "delta",
      "label_filters": {"route": "/checkout"}
    }
  ]
}
```

## Run once

```bash
sketchlog-agent --config sketchlog-agent.json --once
```

The command prints a JSON summary such as:

```json
{"forwarded_batches": 1, "forwarded_items": 2, "matched_samples": 2, "scraped_targets": 1, "skipped_samples": 0}
```

## Run continuously

```bash
sketchlog-agent --config sketchlog-agent.json
```

The agent repeats the scrape and forward cycle every `interval_seconds` and prints one operational summary line per cycle.

## Mapping types

| Kind | SketchLog payload | Use case |
|---|---|---|
| `latency` | `latencies` | Gauge-like duration samples, scaled into the desired unit |
| `event` | `events` | Counter metrics converted to per-scrape deltas by default |
| `unique` | `uniques` | Numeric identifiers or bucket values converted to unique strings |

For event mappings, `counter_mode: "delta"` is the production-safe default. The first scrape initializes the baseline and does not send the lifetime value. Later scrapes forward only positive deltas. `counter_mode: "absolute"` is available for metrics that already represent per-interval counts.

## Security notes

- Prefer `auth_token_env` over inline `auth_token` so secrets do not live in config files.
- Use HTTPS for remote SketchLog servers.
- Keep scrape targets inside trusted networks; the agent intentionally connects only to URLs configured by the operator.
- The agent uses only the Python standard library, keeping sidecar images small and avoiding optional server dependencies.

## Deployment modes

The same config works for local development, Docker sidecars, Kubernetes sidecars, small VM deployments, and CI smoke tests that need to bridge Prometheus text into SketchLog.

Future agent work can add container runtime metrics, richer log-derived events, and OpenTelemetry receiving. This first slice establishes the production-safe collection and forwarding contract.
