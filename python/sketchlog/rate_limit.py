"""sketchlog.rate_limit — per-namespace token-bucket rate limiting and quota enforcement."""
from __future__ import annotations
import argparse, json, logging, math, sys, threading, time
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)

_WILDCARD = "*"

class LimitResult(str, Enum):
    ALLOWED        = "ALLOWED"
    RATE_LIMITED   = "RATE_LIMITED"
    QUOTA_EXCEEDED = "QUOTA_EXCEEDED"


@dataclass(frozen=True)
class NamespaceQuota:
    """Per-namespace rate/burst/quota configuration."""
    namespace:        str
    rate_per_second:  float
    burst:            int
    hourly_quota:     int = 0   # 0 = unlimited
    daily_quota:      int = 0   # 0 = unlimited

    def __post_init__(self) -> None:
        errors: List[str] = []
        if not isinstance(self.namespace, str) or not self.namespace.strip():
            errors.append("namespace must be a non-empty string")
        if not isinstance(self.rate_per_second, (int, float)) or math.isnan(self.rate_per_second) or self.rate_per_second <= 0:
            errors.append("rate_per_second must be a positive number")
        if not isinstance(self.burst, int) or self.burst < 1:
            errors.append("burst must be >= 1")
        if not isinstance(self.hourly_quota, int) or self.hourly_quota < 0:
            errors.append("hourly_quota must be >= 0")
        if not isinstance(self.daily_quota, int) or self.daily_quota < 0:
            errors.append("daily_quota must be >= 0")
        if errors:
            raise ValueError("NamespaceQuota errors: " + "; ".join(errors))


@dataclass
class RateLimitConfig:
    """Registry of per-namespace quotas with fallback to wildcard/default."""
    quotas:                  List[NamespaceQuota] = field(default_factory=list)
    default_rate_per_second: float = 100.0
    default_burst:           int   = 200
    default_hourly_quota:    int   = 0
    default_daily_quota:     int   = 0

    def __post_init__(self) -> None:
        errors: List[str] = []
        if not isinstance(self.default_rate_per_second, (int, float)) or self.default_rate_per_second <= 0:
            errors.append("default_rate_per_second must be > 0")
        if not isinstance(self.default_burst, int) or self.default_burst < 1:
            errors.append("default_burst must be >= 1")
        if not isinstance(self.default_hourly_quota, int) or self.default_hourly_quota < 0:
            errors.append("default_hourly_quota must be >= 0")
        if not isinstance(self.default_daily_quota, int) or self.default_daily_quota < 0:
            errors.append("default_daily_quota must be >= 0")
        if errors:
            raise ValueError("RateLimitConfig errors: " + "; ".join(errors))

    def get_quota(self, namespace: str) -> NamespaceQuota:
        """Return quota: exact match > wildcard > default."""
        for q in self.quotas:
            if q.namespace == namespace:
                return q
        for q in self.quotas:
            if q.namespace == _WILDCARD:
                return q
        return NamespaceQuota(
            namespace=namespace,
            rate_per_second=self.default_rate_per_second,
            burst=self.default_burst,
            hourly_quota=self.default_hourly_quota,
            daily_quota=self.default_daily_quota,
        )


class _TokenBucket:
    """Thread-safe continuous token bucket."""

    def __init__(self, rate: float, burst: int) -> None:
        self._rate   = rate
        self._burst  = burst
        self._tokens = float(burst)
        self._last   = time.monotonic()
        self._lock   = threading.Lock()

    @property
    def available(self) -> float:
        with self._lock:
            self._refill()
            return self._tokens

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last
        self._tokens = min(float(self._burst), self._tokens + elapsed * self._rate)
        self._last = now

    def consume(self, tokens: int = 1) -> bool:
        with self._lock:
            self._refill()
            if self._tokens >= tokens:
                self._tokens -= tokens
                return True
            return False


class _QuotaCounter:
    """Thread-safe rolling-window hourly and daily counter with atomic check-and-increment."""

    def __init__(self) -> None:
        self._lock         = threading.Lock()
        self._hourly_count = 0
        self._daily_count  = 0
        self._hour_start   = time.time()
        self._day_start    = time.time()

    def _reset_if_needed(self) -> None:
        now = time.time()
        if now - self._hour_start >= 3600:
            self._hourly_count = 0
            self._hour_start   = now
        if now - self._day_start >= 86400:
            self._daily_count = 0
            self._day_start   = now

    @property
    def hourly_count(self) -> int:
        with self._lock:
            self._reset_if_needed()
            return self._hourly_count

    @property
    def daily_count(self) -> int:
        with self._lock:
            self._reset_if_needed()
            return self._daily_count

    def check_and_increment(self, hourly_limit: int, daily_limit: int) -> tuple[bool, str]:
        """Atomically check quota and increment if allowed. Returns (allowed, reason)."""
        with self._lock:
            self._reset_if_needed()
            if hourly_limit > 0 and self._hourly_count >= hourly_limit:
                return False, f"hourly quota exceeded ({self._hourly_count}/{hourly_limit})"
            if daily_limit > 0 and self._daily_count >= daily_limit:
                return False, f"daily quota exceeded ({self._daily_count}/{daily_limit})"
            self._hourly_count += 1
            self._daily_count  += 1
            return True, "ok"


@dataclass(frozen=True)
class RateLimitDecision:
    """Structured enforcement result."""
    namespace:         str
    result:            LimitResult
    reason:            str
    tokens_remaining:  float
    hourly_used:       int
    daily_used:        int

    def to_dict(self) -> dict:
        return {
            "namespace":        self.namespace,
            "result":           self.result.value,
            "reason":           self.reason,
            "tokens_remaining": round(self.tokens_remaining, 3),
            "hourly_used":      self.hourly_used,
            "daily_used":       self.daily_used,
        }


class RateLimitEnforcer:
    """Central per-namespace enforcement: quota check -> token bucket -> decision."""

    def __init__(self, config: RateLimitConfig) -> None:
        self._config   = config
        self._buckets:  Dict[str, _TokenBucket]  = {}
        self._counters: Dict[str, _QuotaCounter] = {}
        self._lock      = threading.Lock()

    def _get_or_create(self, namespace: str, quota: NamespaceQuota) -> tuple[_TokenBucket, _QuotaCounter]:
        with self._lock:
            if namespace not in self._buckets:
                self._buckets[namespace]  = _TokenBucket(quota.rate_per_second, quota.burst)
                self._counters[namespace] = _QuotaCounter()
            return self._buckets[namespace], self._counters[namespace]

    def check(self, namespace: str, tokens: int = 1) -> RateLimitDecision:
        quota   = self._config.get_quota(namespace)
        bucket, counter = self._get_or_create(namespace, quota)

        # Atomic quota check + increment
        allowed, reason = counter.check_and_increment(quota.hourly_quota, quota.daily_quota)
        if not allowed:
            logger.warning("rate_limit: namespace=%s quota_exceeded reason=%s", namespace, reason)
            return RateLimitDecision(
                namespace=namespace,
                result=LimitResult.QUOTA_EXCEEDED,
                reason=reason,
                tokens_remaining=bucket.available,
                hourly_used=counter.hourly_count,
                daily_used=counter.daily_count,
            )

        # Token bucket check
        if not bucket.consume(tokens):
            logger.warning("rate_limit: namespace=%s rate_limited tokens_req=%d available=%.1f",
                           namespace, tokens, bucket.available)
            return RateLimitDecision(
                namespace=namespace,
                result=LimitResult.RATE_LIMITED,
                reason=f"rate limited (burst={quota.burst}, rate={quota.rate_per_second}/s)",
                tokens_remaining=bucket.available,
                hourly_used=counter.hourly_count,
                daily_used=counter.daily_count,
            )

        return RateLimitDecision(
            namespace=namespace,
            result=LimitResult.ALLOWED,
            reason="ok",
            tokens_remaining=bucket.available,
            hourly_used=counter.hourly_count,
            daily_used=counter.daily_count,
        )


def check_rate_limit_config(config: RateLimitConfig) -> tuple[str, List[str]]:
    """SOC2-ready config validator. Returns (status, issues)."""
    issues: List[str] = []

    # Validate defaults
    if config.default_rate_per_second <= 0:
        issues.append("FAIL: default_rate_per_second must be > 0")
    elif config.default_rate_per_second < 1:
        issues.append("WARN: default_rate_per_second < 1 — very low global rate")
    if config.default_burst < 1:
        issues.append("FAIL: default_burst must be >= 1")
    if config.default_hourly_quota < 0:
        issues.append("FAIL: default_hourly_quota must be >= 0")
    if config.default_daily_quota < 0:
        issues.append("FAIL: default_daily_quota must be >= 0")

    # Validate per-namespace
    for q in config.quotas:
        if q.rate_per_second <= 0:
            issues.append(f"FAIL: namespace '{q.namespace}' rate_per_second must be > 0")
        elif q.rate_per_second < 1:
            issues.append(f"WARN: namespace '{q.namespace}' rate_per_second < 1 — very low rate")
        if q.burst < q.rate_per_second:
            issues.append(f"WARN: namespace '{q.namespace}' burst < rate_per_second — burst exhausted quickly")

    if not config.quotas:
        issues.append("WARN: no per-namespace quotas defined — all namespaces use defaults")

    fails = [i for i in issues if i.startswith("FAIL")]
    warns = [i for i in issues if i.startswith("WARN")]
    status = "FAIL" if fails else ("WARN" if warns else "PASS")
    return status, issues


def _build_demo_config() -> RateLimitConfig:
    return RateLimitConfig(
        quotas=[
            NamespaceQuota("prod",    rate_per_second=500.0, burst=1000, hourly_quota=100000, daily_quota=2000000),
            NamespaceQuota("staging", rate_per_second=100.0, burst=200,  hourly_quota=10000,  daily_quota=100000),
            NamespaceQuota("*",       rate_per_second=50.0,  burst=100),
        ],
        default_rate_per_second=20.0,
        default_burst=40,
    )


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(
        prog="sketchlog-rate-check",
        description="Validate SketchLog rate-limit configuration.",
    )
    p.add_argument("--config", help="Path to JSON rate-limit config file")
    p.add_argument("--format", choices=["text", "json"], default="text")
    p.add_argument("--demo",   action="store_true", help="Run with demo config")
    args = p.parse_args(argv)

    try:
        if args.demo:
            config = _build_demo_config()
        elif args.config:
            with open(args.config, encoding="utf-8") as fh:
                raw = json.load(fh)
            quotas = [NamespaceQuota(**q) for q in raw.get("quotas", [])]
            config = RateLimitConfig(
                quotas=quotas,
                default_rate_per_second=float(raw.get("default_rate_per_second", 100.0)),
                default_burst=int(raw.get("default_burst", 200)),
                default_hourly_quota=int(raw.get("default_hourly_quota", 0)),
                default_daily_quota=int(raw.get("default_daily_quota", 0)),
            )
        else:
            print("ERROR: provide --config or --demo", file=sys.stderr)
            return 2
    except (ValueError, TypeError, KeyError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    status, issues = check_rate_limit_config(config)

    if args.format == "json":
        print(json.dumps({"status": status, "issues": issues,
                          "namespaces": len(config.quotas),
                          "default_rate": config.default_rate_per_second}, indent=2))
    else:
        print("SketchLog Rate Limit configuration check")
        print(f"  Namespaces : {len(config.quotas)}")
        print(f"  Default rate: {config.default_rate_per_second}/s  burst={config.default_burst}")
        for issue in issues:
            print(f"  {issue}")
        print(f"\nResult: {status}")

    return 0 if status == "PASS" else (1 if status == "WARN" else 2)


if __name__ == "__main__":
    sys.exit(main())
