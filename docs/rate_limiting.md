# Per-Namespace Rate Limiting and Quota Enforcement

SketchLog's rate-limiting module enforces per-namespace request rates and
quotas using a clock-injected token bucket and rolling-window quota counters.

## Quick start

```bash
sketchlog-rate-check --demo
```

## Architecture

### Clock injection

Every time-sensitive class (`_TokenBucket`, `_QuotaCounter`, `RateLimitEnforcer`)
accepts a `clock` callable parameter. Production code uses `time.monotonic` /
`time.time` (the defaults). Tests pass a deterministic fake clock — **zero
`mock.patch`, zero `time.sleep`, works identically on all platforms**.

This is the same pattern used by Go's `time.Now` injection and Netflix's
clock abstraction.

### Token bucket

- Continuous refill at `rate_per_second` tokens/second
- Capped at `burst` tokens
- Thread-safe with `threading.Lock`

### Quota counters

- Rolling hourly and daily windows
- Atomic `check_and_increment()` — read, check, and write under one lock
- No overshoot under concurrent requests

### Namespace resolution

`exact match` → `wildcard '*'` → `default`

## Configuration

```python
from sketchlog.rate_limit import RateLimitConfig, NamespaceQuota, RateLimitEnforcer, LimitResult

config = RateLimitConfig(
    quotas=[
        NamespaceQuota("prod",    rate_per_second=1000.0, burst=2000,
                       hourly_quota=500_000, daily_quota=5_000_000),
        NamespaceQuota("staging", rate_per_second=100.0,  burst=200),
        NamespaceQuota("*",       rate_per_second=50.0,   burst=100),
    ],
    default_rate_per_second=10.0,
    default_burst=20,
)
enforcer = RateLimitEnforcer(config)
```

## Enforcement

```python
decision = enforcer.check("prod")
if decision.result != LimitResult.ALLOWED:
    # Return HTTP 429
    raise TooManyRequests(decision.reason)
```

## CLI

```bash
# Validate a config file
sketchlog-rate-check --config rate_limit.json

# Demo mode
sketchlog-rate-check --demo --format json
```

## Exit codes

| Code | Meaning |
|------|---------|
| 0 | PASS — all checks passed |
| 1 | WARN — low rates or burst warnings |
| 2 | FAIL — invalid configuration or error |

## Caveats

- Token bucket refill is continuous, not discrete — actual throughput may
  vary slightly from `rate_per_second` depending on call frequency.
- Quota counters are in-memory and reset on restart. Windows are based on request timestamps, not calendar-aligned UTC hours/days — a restart clears all counts.
- Rate limiting state is in-memory — restarts reset all counters.
- For distributed rate limiting across multiple SketchLog nodes, use an
  external store (Redis, Memcached) as a shared counter backend.
