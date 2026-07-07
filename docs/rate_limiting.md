# Rate Limiting and Quota Enforcement

SketchLog's rate limiting module provides per-namespace token-bucket rate
limiting with configurable burst, hourly quotas, and daily quotas.
All enforcement is thread-safe and stdlib-only.

## Roles

| Concept | Description |
|---|---|
| `rate_per_second` | Sustained request rate (tokens per second) |
| `burst` | Maximum tokens that can accumulate (absorbs spikes) |
| `hourly_quota` | Max requests per rolling hour (0 = unlimited) |
| `daily_quota` | Max requests per rolling 24 hours (0 = unlimited) |

## Quick Start

```python
import os
from sketchlog.rate_limit import (
    NamespaceQuota, RateLimitConfig, RateLimitEnforcer, LimitResult
)

config = RateLimitConfig(
    quotas=[
        NamespaceQuota("prod",    rate_per_second=5000, burst=20000, daily_quota=10_000_000),
        NamespaceQuota("staging", rate_per_second=500,  burst=2000,  daily_quota=1_000_000),
        NamespaceQuota("*",       rate_per_second=100,  burst=500),
    ],
    enabled=True,
)

enforcer = RateLimitEnforcer(config)

decision = enforcer.check("prod")
if decision.result != LimitResult.ALLOWED:
    raise PermissionError(f"Rate limited: {decision.reason}")
```

## CLI

```bash
sketchlog-rate-check --demo --format json
```

## Decision results

| Result | Meaning |
|---|---|
| `allowed` | Request permitted |
| `rate_limited` | Token bucket exhausted |
| `quota_exceeded` | Hourly or daily quota exceeded |

## Configuration validation

```python
from sketchlog.rate_limit import check_rate_limit_config

result = check_rate_limit_config(config)
print(result.to_json())
```

## Caveats

- Counters are in-process only; distributed quota enforcement requires
  an external store (Redis, etc.).
- Token bucket state is not persisted across restarts.
- `daily_quota` uses a rolling window from the first request, not midnight.
