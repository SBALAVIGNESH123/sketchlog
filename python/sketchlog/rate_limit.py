"""Per-namespace rate limiting and quota enforcement for SketchLog."""
from __future__ import annotations

import argparse
import json
import logging
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, cast, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Public enums / constants
# ---------------------------------------------------------------------------

class LimitResult:
    ALLOWED        = "ALLOWED"
    RATE_LIMITED   = "RATE_LIMITED"
    QUOTA_EXCEEDED = "QUOTA_EXCEEDED"

_WILDCARD = "*"

# ---------------------------------------------------------------------------
# NamespaceQuota — frozen config for one namespace (or wildcard)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class NamespaceQuota:
    """Rate and quota settings for one namespace slot."""

    namespace:        str
    rate_per_second:  float          = 100.0
    burst:            int            = 200
    hourly_quota:     int            = 0      # 0 = unlimited
    daily_quota:      int            = 0      # 0 = unlimited

    def __post_init__(self) -> None:
        errors: List[str] = []
        if not isinstance(self.namespace, str) or not self.namespace.strip():
            errors.append("namespace must be a non-empty string")
        if self.rate_per_second <= 0:
            errors.append("rate_per_second must be > 0")
        if self.burst < 1:
            errors.append("burst must be >= 1")
        if self.hourly_quota < 0:
            errors.append("hourly_quota must be >= 0")
        if self.daily_quota < 0:
            errors.append("daily_quota must be >= 0")
        if self.hourly_quota > 0 and self.daily_quota > 0 and self.hourly_quota > self.daily_quota:
            errors.append("hourly_quota cannot exceed daily_quota")
        if errors:
            raise ValueError("NamespaceQuota errors: " + "; ".join(errors))

# ---------------------------------------------------------------------------
# RateLimitConfig — registry with exact > wildcard > default fallback
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RateLimitConfig:
    """Top-level rate-limit configuration."""

    quotas:                  Sequence[NamespaceQuota] = field(default_factory=list)
    default_rate_per_second: float                    = 100.0
    default_burst:           int                      = 200
    default_hourly_quota:    int                      = 0
    default_daily_quota:     int                      = 0

    def __post_init__(self) -> None:
        errors: List[str] = []
        if self.default_rate_per_second <= 0:
            errors.append("default_rate_per_second must be > 0")
        if self.default_burst < 1:
            errors.append("default_burst must be >= 1")
        if self.default_hourly_quota < 0:
            errors.append("default_hourly_quota must be >= 0")
        if self.default_daily_quota < 0:
            errors.append("default_daily_quota must be >= 0")
        if errors:
            raise ValueError("RateLimitConfig errors: " + "; ".join(errors))

    def get_quota(self, namespace: str) -> NamespaceQuota:
        """Return the most-specific quota for *namespace*."""
        exact    = None
        wildcard = None
        for q in self.quotas:
            if q.namespace == namespace:
                exact = q
                break
            if q.namespace == _WILDCARD:
                wildcard = q
        if exact:
            return exact
        if wildcard:
            return wildcard
        return NamespaceQuota(
            namespace        = namespace,
            rate_per_second  = self.default_rate_per_second,
            burst            = self.default_burst,
            hourly_quota     = self.default_hourly_quota,
            daily_quota      = self.default_daily_quota,
        )

# ---------------------------------------------------------------------------
# RateLimitDecision — structured enforcement result
# ---------------------------------------------------------------------------

@dataclass
class RateLimitDecision:
    namespace:        str
    result:           str
    reason:           str
    tokens_remaining: float
    hourly_used:      int
    daily_used:       int

    def to_dict(self) -> Dict[str, object]:
        return {
            "namespace":        self.namespace,
            "result":           self.result,
            "reason":           self.reason,
            "tokens_remaining": round(self.tokens_remaining, 3),
            "hourly_used":      self.hourly_used,
            "daily_used":       self.daily_used,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict())

# ---------------------------------------------------------------------------
# _TokenBucket — clock-injected, thread-safe token bucket
# ---------------------------------------------------------------------------

class _TokenBucket:
    """Token bucket with injectable clock for deterministic tests."""

    def __init__(
        self,
        rate_per_second: float,
        burst: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._rate      = rate_per_second
        self._burst     = burst
        self._tokens    = float(burst)
        self._last      = clock()
        self._clock     = clock
        self._lock      = threading.Lock()

    def _refill(self) -> None:
        now     = self._clock()
        elapsed = max(0.0, now - self._last)
        self._tokens = min(float(self._burst), self._tokens + elapsed * self._rate)
        self._last  = now

    def consume(self, tokens: int = 1) -> bool:
        with self._lock:
            self._refill()
            if self._tokens >= tokens:
                self._tokens -= tokens
                return True
            return False

    @property
    def available(self) -> float:
        with self._lock:
            self._refill()
            return self._tokens

# ---------------------------------------------------------------------------
# _QuotaCounter — clock-injected, thread-safe rolling window counter
# ---------------------------------------------------------------------------

class _QuotaCounter:
    """Rolling hourly/daily counter with atomic check-and-increment."""

    _HOUR = 3600.0
    _DAY  = 86400.0

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock        = clock
        self._lock         = threading.Lock()
        self._hourly_count = 0
        self._daily_count  = 0
        self._hour_start   = clock()
        self._day_start    = clock()

    def _reset_if_needed(self) -> None:
        now = self._clock()
        if now - self._hour_start >= self._HOUR:
            self._hourly_count = 0
            self._hour_start   = now
        if now - self._day_start >= self._DAY:
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

    def check_and_increment(
        self,
        hourly_quota: int,
        daily_quota: int,
    ) -> Optional[str]:
        """Atomically check limits and increment if allowed.

        Returns None if allowed, or an error string if a quota is exceeded.
        """
        with self._lock:
            self._reset_if_needed()
            if hourly_quota > 0 and self._hourly_count >= hourly_quota:
                return f"hourly quota exceeded ({self._hourly_count}/{hourly_quota})"
            if daily_quota > 0 and self._daily_count >= daily_quota:
                return f"daily quota exceeded ({self._daily_count}/{daily_quota})"
            self._hourly_count += 1
            self._daily_count  += 1
            return None

    def decrement(self) -> None:
        """Roll back one increment (called when token bucket rejects)."""
        with self._lock:
            if self._hourly_count > 0:
                self._hourly_count -= 1
            if self._daily_count > 0:
                self._daily_count  -= 1

# ---------------------------------------------------------------------------
# RateLimitEnforcer — central enforcement
# ---------------------------------------------------------------------------

class RateLimitEnforcer:
    """Thread-safe enforcer: quota check -> token bucket -> decision."""

    def __init__(
        self,
        config: RateLimitConfig,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._config  = config
        self._clock   = clock
        self._lock    = threading.Lock()
        self._buckets:  Dict[str, _TokenBucket]  = {}
        self._counters: Dict[str, _QuotaCounter] = {}

    def _get_bucket(self, namespace: str, quota: NamespaceQuota) -> _TokenBucket:
        if namespace not in self._buckets:
            self._buckets[namespace] = _TokenBucket(
                quota.rate_per_second, quota.burst, self._clock
            )
        return self._buckets[namespace]

    def _get_counter(self, namespace: str) -> _QuotaCounter:
        if namespace not in self._counters:
            self._counters[namespace] = _QuotaCounter(self._clock)
        return self._counters[namespace]

    def check(self, namespace: str, tokens: int = 1) -> RateLimitDecision:
        with self._lock:
            quota   = self._config.get_quota(namespace)
            bucket  = self._get_bucket(namespace, quota)
            counter = self._get_counter(namespace)

        # Atomic quota check + increment
        quota_err = counter.check_and_increment(quota.hourly_quota, quota.daily_quota)
        if quota_err:
            logger.warning("rate_limit quota_exceeded namespace=%s reason=%s", namespace, quota_err)
            return RateLimitDecision(
                namespace        = namespace,
                result           = LimitResult.QUOTA_EXCEEDED,
                reason           = quota_err,
                tokens_remaining = bucket.available,
                hourly_used      = counter.hourly_count,
                daily_used       = counter.daily_count,
            )

        # Token bucket check
        if not bucket.consume(tokens):
            # Roll back quota increment — request was not served
            counter.decrement()
            logger.warning("rate_limit rate_limited namespace=%s", namespace)
            return RateLimitDecision(
                namespace        = namespace,
                result           = LimitResult.RATE_LIMITED,
                reason           = f"rate limited (burst={quota.burst}, rate={quota.rate_per_second}/s)",
                tokens_remaining = bucket.available,
                hourly_used      = counter.hourly_count,
                daily_used       = counter.daily_count,
            )

        return RateLimitDecision(
            namespace        = namespace,
            result           = LimitResult.ALLOWED,
            reason           = "ok",
            tokens_remaining = bucket.available,
            hourly_used      = counter.hourly_count,
            daily_used       = counter.daily_count,
        )

# ---------------------------------------------------------------------------
# check_rate_limit_config — SOC2-ready validator
# ---------------------------------------------------------------------------

def check_rate_limit_config(config: RateLimitConfig) -> Dict[str, object]:
    """Validate a RateLimitConfig and return a report dict."""
    issues: List[str] = []

    # Validate default config
    if config.default_rate_per_second <= 0:
        issues.append("FAIL: default_rate_per_second must be > 0 — enforcer will crash at runtime")
    elif config.default_rate_per_second < 1:
        issues.append("WARN: default_rate_per_second < 1 — very low global rate")

    if config.default_burst < 1:
        issues.append("FAIL: default_burst must be >= 1 — enforcer will crash at runtime")

    if config.default_hourly_quota < 0:
        issues.append("FAIL: default_hourly_quota must be >= 0")
    if config.default_daily_quota < 0:
        issues.append("FAIL: default_daily_quota must be >= 0")

    # Validate per-namespace quotas
    for q in config.quotas:
        if q.rate_per_second <= 0:
            issues.append(f"FAIL: namespace '{q.namespace}' rate_per_second must be > 0")
        elif q.rate_per_second < 1:
            issues.append(f"WARN: namespace '{q.namespace}' rate_per_second < 1 — very low rate")
        if q.burst < q.rate_per_second:
            issues.append(
                f"WARN: namespace '{q.namespace}' burst ({q.burst}) < rate_per_second "
                f"({q.rate_per_second}) — burst exhausted in < 1 second"
            )
        if q.hourly_quota > 0 and q.daily_quota > 0 and q.hourly_quota * 24 < q.daily_quota:
            issues.append(
                f"WARN: namespace '{q.namespace}' daily_quota unreachable "
                f"(24 * hourly_quota={q.hourly_quota * 24} < daily_quota={q.daily_quota})"
            )

    fails = [i for i in issues if i.startswith("FAIL")]
    warns = [i for i in issues if i.startswith("WARN")]
    result = "FAIL" if fails else ("WARN" if warns else "PASS")

    return {
        "result":     result,
        "namespaces": len(list(config.quotas)),
        "issues":     issues,
    }

# ---------------------------------------------------------------------------
# CLI — sketchlog-rate-check
# ---------------------------------------------------------------------------

def _demo_config() -> RateLimitConfig:
    return RateLimitConfig(
        quotas=[
            NamespaceQuota("prod",    rate_per_second=1000.0, burst=2000,
                           hourly_quota=500_000, daily_quota=5_000_000),
            NamespaceQuota("staging", rate_per_second=100.0,  burst=200),
            NamespaceQuota(_WILDCARD, rate_per_second=50.0,   burst=100),
        ],
        default_rate_per_second=10.0,
        default_burst=20,
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
        if args.demo or not args.config:
            config = _demo_config()
        else:
            with open(args.config, encoding="utf-8") as fh:
                raw = json.load(fh)
            quotas = [NamespaceQuota(**q) for q in raw.get("quotas", [])]
            config = RateLimitConfig(
                quotas                  = quotas,
                default_rate_per_second = raw.get("default_rate_per_second", 100.0),
                default_burst           = raw.get("default_burst", 200),
                default_hourly_quota    = raw.get("default_hourly_quota", 0),
                default_daily_quota     = raw.get("default_daily_quota", 0),
            )

        report = check_rate_limit_config(config)

        if args.format == "json":
            print(json.dumps(report, indent=2))
        else:
            print("SketchLog rate-limit configuration check")
            print(f"  Namespaces : {report['namespaces']}")
            issues_list: List[str] = cast(List[str], report.get("issues") or [])
            if issues_list:
                for issue in issues_list:
                    print(f"  {issue}")
            print(f"\nResult: {report['result']}")

        result_str = str(report.get("result", "FAIL"))
        return 0 if result_str == "PASS" else (1 if result_str == "WARN" else 2)

    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
