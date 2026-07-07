# Rate Limiting and Quota Enforcement

SketchLog's rate-limiting module enforces per-namespace request rates and
daily/hourly quotas so no single tenant can starve other namespaces.

## Quick start

```bash
sketchlog-rate-check --demo
sketchlog-rate-check --config rate_limit.json --format json
```

## How it works

Each namespace gets an independent **token bucket** (continuous refill at
`rate_per_second`, up to `burst` tokens) and an **atomic quota counter**
(rolling hourly and daily windows).

Enforcement order:
1. Atomic quota check + increment (hourly → daily)
2. Token bucket consume
3. Returns `ALLOWED`, `RATE_LIMITED`, or `QUOTA_EXCEEDED`

## Configuration

```json
{
  "quotas": [
    {"namespace": "prod",    "rate_per_second": 500, "burst": 1000,
     "hourly_quota": 100000, "daily_quota": 2000000},
    {"namespace": "staging", "rate_per_second": 100, "burst": 200},
    {"namespace": "*",       "rate_per_second": 50,  "burst": 100}
  ],
  "default_rate_per_second": 20,
  "default_burst": 40
}
```

Namespace matching: **exact > wildcard (`*`) > defaults**.

## Python API

```python
from sketchlog.rate_limit import RateLimitConfig, NamespaceQuota, RateLimitEnforcer

config = RateLimitConfig(
    quotas=[NamespaceQuota("prod", rate_per_second=500.0, burst=1000)],
)
enforcer = RateLimitEnforcer(config)
decision = enforcer.check("prod")
print(decision.result)   # ALLOWED / RATE_LIMITED / QUOTA_EXCEEDED
```

## Exit codes

| Code | Meaning |
|------|---------|
| `0`  | PASS — config is valid |
| `1`  | WARN — config is valid but has potential issues |
| `2`  | FAIL — config is invalid or missing required argument |

## Caveats

- Token buckets and quota counters are **in-process only** — not shared across multiple server processes.
- For multi-process deployments, use a shared Redis-backed rate limiter alongside this module.
- All quota counters reset on process restart.
