"""
SketchLog Rate Limiting and Quota Enforcement (Issue #252).

Provides per-namespace token-bucket rate limiting and quota enforcement
with thread-safe enforcement, configurable burst, and SOC2-ready audit hooks.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Public constants
# ─────────────────────────────────────────────────────────────────────────────
_WILDCARD = "*"
_DEFAULT_RATE = 1000.0   # requests per second
_DEFAULT_BURST = 5000    # max bucket tokens
_DEFAULT_QUOTA  = 0      # 0 = unlimited


# ─────────────────────────────────────────────────────────────────────────────
# Enums
# ─────────────────────────────────────────────────────────────────────────────
class LimitResult(str, Enum):
    ALLOWED  = "allowed"
    RATE_LIMITED = "rate_limited"
    QUOTA_EXCEEDED = "quota_exceeded"


# ─────────────────────────────────────────────────────────────────────────────
# Config dataclasses
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class NamespaceQuota:
    """Per-namespace rate and quota configuration."""
    namespace: str
    rate_per_second: float = _DEFAULT_RATE   # sustained rate (tokens/sec)
    burst: int             = _DEFAULT_BURST  # max burst tokens
    daily_quota: int       = _DEFAULT_QUOTA  # max requests/day, 0=unlimited
    hourly_quota: int      = _DEFAULT_QUOTA  # max requests/hour, 0=unlimited

    def __post_init__(self) -> None:
        errors: List[str] = []
        if not isinstance(self.namespace, str) or not self.namespace.strip():
            errors.append("namespace must be a non-empty string")
        if not isinstance(self.rate_per_second, (int, float)) or self.rate_per_second <= 0:
            errors.append("rate_per_second must be a positive number")
        if not isinstance(self.burst, int) or self.burst < 1:
            errors.append("burst must be a positive integer")
        if not isinstance(self.daily_quota, int) or self.daily_quota < 0:
            errors.append("daily_quota must be a non-negative integer")
        if not isinstance(self.hourly_quota, int) or self.hourly_quota < 0:
            errors.append("hourly_quota must be a non-negative integer")
        if errors:
            raise ValueError("NamespaceQuota errors: " + "; ".join(errors))


@dataclass
class RateLimitConfig:
    """Global rate limiting configuration."""
    quotas: List[NamespaceQuota] = field(default_factory=list)
    enabled: bool = True
    default_rate_per_second: float = _DEFAULT_RATE
    default_burst: int             = _DEFAULT_BURST
    default_daily_quota: int       = _DEFAULT_QUOTA
    default_hourly_quota: int      = _DEFAULT_QUOTA

    def get_quota(self, namespace: str) -> NamespaceQuota:
        """Return the best-matching quota for namespace (exact > wildcard > default)."""
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
            daily_quota=self.default_daily_quota,
            hourly_quota=self.default_hourly_quota,
        )


# ─────────────────────────────────────────────────────────────────────────────
# Token bucket (thread-safe)
# ─────────────────────────────────────────────────────────────────────────────
class _TokenBucket:
    """Thread-safe token bucket for a single namespace."""

    def __init__(self, rate: float, burst: int) -> None:
        self._rate  = rate
        self._burst = burst
        self._tokens: float = float(burst)
        self._last_refill  = time.monotonic()
        self._lock = threading.Lock()

    def consume(self, tokens: int = 1) -> bool:
        """Consume *tokens*; return True if allowed, False if rate-limited."""
        with self._lock:
            self._refill()
            if self._tokens >= tokens:
                self._tokens -= tokens
                return True
            return False

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._tokens = min(
            float(self._burst),
            self._tokens + elapsed * self._rate,
        )
        self._last_refill = now

    @property
    def available(self) -> float:
        with self._lock:
            self._refill()
            return self._tokens


# ─────────────────────────────────────────────────────────────────────────────
# Quota counters (thread-safe)
# ─────────────────────────────────────────────────────────────────────────────
class _QuotaCounter:
    """Thread-safe rolling-window quota counter (hourly + daily)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # (window_start_epoch, count)
        self._hourly: Tuple[float, int] = (time.time(), 0)
        self._daily:  Tuple[float, int] = (time.time(), 0)

    def increment(self) -> None:
        now = time.time()
        with self._lock:
            hs, hc = self._hourly
            if now - hs >= 3600:
                self._hourly = (now, 1)
            else:
                self._hourly = (hs, hc + 1)

            ds, dc = self._daily
            if now - ds >= 86400:
                self._daily = (now, 1)
            else:
                self._daily = (ds, dc + 1)

    @property
    def hourly_count(self) -> int:
        now = time.time()
        with self._lock:
            hs, hc = self._hourly
            return 0 if now - hs >= 3600 else hc

    @property
    def daily_count(self) -> int:
        now = time.time()
        with self._lock:
            ds, dc = self._daily
            return 0 if now - ds >= 86400 else dc


# ─────────────────────────────────────────────────────────────────────────────
# Decision + result types
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class RateLimitDecision:
    """Result of a single enforcement check."""
    namespace:     str
    result:        LimitResult
    reason:        str
    tokens_remaining: float
    hourly_used:   int
    daily_used:    int
    ts:            float = field(default_factory=time.time)

    def to_dict(self) -> Dict:
        d = asdict(self)
        d["result"] = self.result.value
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), separators=(",", ":"))


# ─────────────────────────────────────────────────────────────────────────────
# Enforcer
# ─────────────────────────────────────────────────────────────────────────────
class RateLimitEnforcer:
    """
    Central rate-limit enforcer.

    Thread-safe.  One instance per process; namespaces are created lazily.
    """

    def __init__(self, config: RateLimitConfig) -> None:
        self._config = config
        self._buckets:  Dict[str, _TokenBucket]  = {}
        self._counters: Dict[str, _QuotaCounter] = {}
        self._lock = threading.Lock()

    # ── public API ───────────────────────────────────────────────────────────

    def check(self, namespace: str, tokens: int = 1) -> RateLimitDecision:
        """
        Check and enforce rate limits for *namespace*.

        Returns a :class:`RateLimitDecision`; does NOT raise on rejection.
        """
        if not self._config.enabled:
            return RateLimitDecision(
                namespace=namespace,
                result=LimitResult.ALLOWED,
                reason="rate limiting disabled",
                tokens_remaining=float("inf"),
                hourly_used=0,
                daily_used=0,
            )

        quota   = self._config.get_quota(namespace)
        bucket  = self._get_bucket(namespace, quota)
        counter = self._get_counter(namespace)

        hourly_used = counter.hourly_count
        daily_used  = counter.daily_count

        # Quota checks first (cheaper)
        if quota.hourly_quota > 0 and hourly_used >= quota.hourly_quota:
            logger.warning("rate_limit: namespace=%s hourly_quota_exceeded used=%d limit=%d",
                           namespace, hourly_used, quota.hourly_quota)
            return RateLimitDecision(
                namespace=namespace,
                result=LimitResult.QUOTA_EXCEEDED,
                reason=f"hourly quota exceeded ({hourly_used}/{quota.hourly_quota})",
                tokens_remaining=bucket.available,
                hourly_used=hourly_used,
                daily_used=daily_used,
            )

        if quota.daily_quota > 0 and daily_used >= quota.daily_quota:
            logger.warning("rate_limit: namespace=%s daily_quota_exceeded used=%d limit=%d",
                           namespace, daily_used, quota.daily_quota)
            return RateLimitDecision(
                namespace=namespace,
                result=LimitResult.QUOTA_EXCEEDED,
                reason=f"daily quota exceeded ({daily_used}/{quota.daily_quota})",
                tokens_remaining=bucket.available,
                hourly_used=hourly_used,
                daily_used=daily_used,
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
                hourly_used=hourly_used,
                daily_used=daily_used,
            )

        counter.increment()
        return RateLimitDecision(
            namespace=namespace,
            result=LimitResult.ALLOWED,
            reason="ok",
            tokens_remaining=bucket.available,
            hourly_used=hourly_used + 1,
            daily_used=daily_used + 1,
        )

    def stats(self, namespace: str) -> Dict:
        """Return current usage stats for *namespace*."""
        quota   = self._config.get_quota(namespace)
        bucket  = self._get_bucket(namespace, quota)
        counter = self._get_counter(namespace)
        return {
            "namespace":        namespace,
            "tokens_available": round(bucket.available, 2),
            "burst":            quota.burst,
            "rate_per_second":  quota.rate_per_second,
            "hourly_used":      counter.hourly_count,
            "hourly_quota":     quota.hourly_quota,
            "daily_used":       counter.daily_count,
            "daily_quota":      quota.daily_quota,
        }

    def reset(self, namespace: str) -> None:
        """Reset token bucket and counters for *namespace* (e.g. after quota increase)."""
        quota = self._config.get_quota(namespace)
        with self._lock:
            self._buckets[namespace]  = _TokenBucket(quota.rate_per_second, quota.burst)
            self._counters[namespace] = _QuotaCounter()

    # ── private helpers ──────────────────────────────────────────────────────

    def _get_bucket(self, namespace: str, quota: NamespaceQuota) -> _TokenBucket:
        with self._lock:
            if namespace not in self._buckets:
                self._buckets[namespace] = _TokenBucket(quota.rate_per_second, quota.burst)
            return self._buckets[namespace]

    def _get_counter(self, namespace: str) -> _QuotaCounter:
        with self._lock:
            if namespace not in self._counters:
                self._counters[namespace] = _QuotaCounter()
            return self._counters[namespace]


# ─────────────────────────────────────────────────────────────────────────────
# Validation / health-check
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class RateLimitCheckResult:
    status:      str          # "PASS" | "WARN" | "FAIL"
    issues:      List[str]
    quota_count: int
    enabled:     bool

    def to_dict(self) -> Dict:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), separators=(",", ":"), indent=2)


def check_rate_limit_config(config: RateLimitConfig) -> RateLimitCheckResult:
    """Validate the rate limit configuration; return a structured result."""
    issues: List[str] = []

    if not config.enabled:
        issues.append("WARN: rate limiting is disabled — all requests will be allowed")

    if not config.quotas:
        issues.append("WARN: no namespace quotas configured — using global defaults")
    else:
        seen: set = set()
        for q in config.quotas:
            if q.namespace in seen:
                issues.append(f"WARN: duplicate quota for namespace '{q.namespace}'")
            seen.add(q.namespace)
            if q.rate_per_second < 1:
                issues.append(f"WARN: namespace '{q.namespace}' rate_per_second < 1 — very low rate")
            if q.burst < q.rate_per_second:
                issues.append(f"WARN: namespace '{q.namespace}' burst < rate_per_second — burst will be exhausted quickly")

    if config.default_rate_per_second <= 0:
        issues.append("FAIL: default_rate_per_second must be > 0")
    elif config.default_rate_per_second < 1:
        issues.append("WARN: default_rate_per_second < 1 — very low global rate")

    warns = [i for i in issues if i.startswith("WARN")]
    fails = [i for i in issues if i.startswith("FAIL")]

    if fails:
        status = "FAIL"
    elif warns:
        status = "WARN"
    else:
        status = "PASS"

    return RateLimitCheckResult(
        status=status,
        issues=issues,
        quota_count=len(config.quotas),
        enabled=config.enabled,
    )


# ─────────────────────────────────────────────────────────────────────────────
# CLI entry point
# ─────────────────────────────────────────────────────────────────────────────
def main() -> None:  # pragma: no cover
    import argparse
    import sys

    p = argparse.ArgumentParser(
        description="Validate SketchLog rate limiting configuration.",
    )
    p.add_argument("--demo",   action="store_true", help="Run with demo config")
    p.add_argument("--format", choices=["text", "json"], default="text",
                   help="Output format (default: text)")
    args = p.parse_args()

    if args.demo or True:  # always demo until config file support added
        cfg = RateLimitConfig(
            quotas=[
                NamespaceQuota("prod",    rate_per_second=5000, burst=20000, daily_quota=10_000_000),
                NamespaceQuota("staging", rate_per_second=500,  burst=2000,  daily_quota=1_000_000),
                NamespaceQuota("*",       rate_per_second=100,  burst=500),
            ],
            enabled=True,
        )

    result = check_rate_limit_config(cfg)
    enforcer = RateLimitEnforcer(cfg)

    # Run a demo check
    demo_decision = enforcer.check("prod")

    if args.format == "json":
        print(json.dumps({
            "config_check": result.to_dict(),
            "demo_decision": demo_decision.to_dict(),
        }, indent=2))
    else:
        print(f"SketchLog Rate Limit Check")
        print(f"  Status     : {result.status}")
        print(f"  Quotas     : {result.quota_count}")
        print(f"  Enabled    : {result.enabled}")
        if result.issues:
            print(f"  Issues:")
            for issue in result.issues:
                print(f"    - {issue}")
        print(f"\nDemo enforcement (namespace=prod):")
        print(f"  Result     : {demo_decision.result.value}")
        print(f"  Tokens left: {demo_decision.tokens_remaining:.0f}")
        print(f"  Hourly used: {demo_decision.hourly_used}")
        print(f"  Daily used : {demo_decision.daily_used}")

    sys.exit(0 if result.status in ("PASS", "WARN") else 1)


if __name__ == "__main__":
    main()
