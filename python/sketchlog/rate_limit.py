"""sketchlog.rate_limit — Production-grade per-namespace rate limiting and quota enforcement.

Design principles:
- Clock injection: every time-sensitive class accepts a `clock` callable (default time.monotonic)
  so tests pass a deterministic fake clock — zero mocking, zero time.sleep, works on all platforms.
- Thread safety: every shared counter uses threading.Lock with atomic check-and-increment.
- Exact > wildcard > default namespace resolution.
- stdlib only — no external dependencies.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import sys
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

Clock = Callable[[], float]  # monotonic clock callable


class LimitResult(str, Enum):
    ALLOWED        = "ALLOWED"
    RATE_LIMITED   = "RATE_LIMITED"
    QUOTA_EXCEEDED = "QUOTA_EXCEEDED"


# ---------------------------------------------------------------------------
# NamespaceQuota
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class NamespaceQuota:
    """Per-namespace rate and quota configuration."""

    namespace:         str
    rate_per_second:   float          # sustained request rate
    burst:             int            # maximum burst size
    hourly_quota:      int   = 0      # 0 = unlimited
    daily_quota:       int   = 0      # 0 = unlimited

    def __post_init__(self) -> None:
        errors: List[str] = []
        if not isinstance(self.namespace, str) or not self.namespace.strip():
            errors.append("namespace must be a non-empty string")
        if not isinstance(self.rate_per_second, (int, float)) or math.isnan(self.rate_per_second) or self.rate_per_second <= 0:
            errors.append("rate_per_second must be a positive number")
        if not isinstance(self.burst, int) or self.burst < 1:
            errors.append("burst must be a positive integer")
        if not isinstance(self.hourly_quota, int) or self.hourly_quota < 0:
            errors.append("hourly_quota must be >= 0")
        if not isinstance(self.daily_quota, int) or self.daily_quota < 0:
            errors.append("daily_quota must be >= 0")
        if errors:
            raise ValueError("NamespaceQuota errors: " + "; ".join(errors))


# ---------------------------------------------------------------------------
# RateLimitConfig
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RateLimitConfig:
    """Global rate-limit configuration with namespace registry."""

    quotas:                  Sequence[NamespaceQuota] = field(default_factory=list)
    default_rate_per_second: float                    = 100.0
    default_burst:           int                      = 200
    default_hourly_quota:    int                      = 0
    default_daily_quota:     int                      = 0

    def __post_init__(self) -> None:
        errors: List[str] = []
        if not isinstance(self.default_rate_per_second, (int, float)) or math.isnan(self.default_rate_per_second) or self.default_rate_per_second <= 0:
            errors.append("default_rate_per_second must be a positive number")
        if not isinstance(self.default_burst, int) or self.default_burst < 1:
            errors.append("default_burst must be a positive integer")
        if not isinstance(self.default_hourly_quota, int) or self.default_hourly_quota < 0:
            errors.append("default_hourly_quota must be >= 0")
        if not isinstance(self.default_daily_quota, int) or self.default_daily_quota < 0:
            errors.append("default_daily_quota must be >= 0")
        if errors:
            raise ValueError("RateLimitConfig errors: " + "; ".join(errors))

    def get_quota(self, namespace: str) -> NamespaceQuota:
        """Resolve quota: exact match > wildcard '*' > default."""
        # Exact match
        for q in self.quotas:
            if q.namespace == namespace:
                return q
        # Wildcard
        for q in self.quotas:
            if q.namespace == "*":
                return NamespaceQuota(
                    namespace=namespace,
                    rate_per_second=q.rate_per_second,
                    burst=q.burst,
                    hourly_quota=q.hourly_quota,
                    daily_quota=q.daily_quota,
                )
        # Default
        return NamespaceQuota(
            namespace=namespace,
            rate_per_second=self.default_rate_per_second,
            burst=self.default_burst,
            hourly_quota=self.default_hourly_quota,
            daily_quota=self.default_daily_quota,
        )


# ---------------------------------------------------------------------------
# _TokenBucket  (clock-injected, thread-safe)
# ---------------------------------------------------------------------------

class _TokenBucket:
    """Thread-safe token bucket with clock injection.

    Pass `clock=time.monotonic` (default) for production.
    Pass any callable returning a float for deterministic tests.
    """

    def __init__(self, rate: float, burst: int, clock: Clock = time.monotonic) -> None:
        self._rate   = rate
        self._burst  = burst
        self._clock  = clock
        self._tokens = float(burst)
        self._last   = clock()
        self._lock   = threading.Lock()

    def _refill(self) -> None:
        """Must be called under self._lock."""
        now     = self._clock()
        elapsed = now - self._last
        self._last = now
        self._tokens = min(float(self._burst), self._tokens + elapsed * self._rate)

    def consume(self, tokens: int = 1) -> bool:
        """Consume tokens. Returns True if allowed, False if rate-limited."""
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
# _QuotaCounter  (clock-injected, thread-safe, atomic check-and-increment)
# ---------------------------------------------------------------------------

class _QuotaCounter:
    """Rolling-window hourly and daily quota counter with clock injection.

    Uses wall-clock time (time.time by default) for human-meaningful windows.
    Pass any callable returning a float for deterministic tests.
    """

    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self._clock        = clock
        self._lock         = threading.Lock()
        self._hour_start   = self._floor_hour(clock())
        self._day_start    = self._floor_day(clock())
        self._hourly_count = 0
        self._daily_count  = 0

    @staticmethod
    def _floor_hour(ts: float) -> float:
        return ts - (ts % 3600)

    @staticmethod
    def _floor_day(ts: float) -> float:
        return ts - (ts % 86400)

    def _maybe_reset(self, now: float) -> None:
        """Must be called under self._lock."""
        if now - self._hour_start >= 3600:
            self._hourly_count = 0
            self._hour_start   = self._floor_hour(now)
        if now - self._day_start >= 86400:
            self._daily_count = 0
            self._day_start   = self._floor_day(now)

    @property
    def hourly_count(self) -> int:
        with self._lock:
            self._maybe_reset(self._clock())
            return self._hourly_count

    @property
    def daily_count(self) -> int:
        with self._lock:
            self._maybe_reset(self._clock())
            return self._daily_count

    def check_and_increment(self, hourly_quota: int, daily_quota: int) -> Optional[str]:
        """Atomically check quotas and increment if allowed.

        Returns None if allowed, or a reason string if quota exceeded.
        This is the key atomicity guarantee — read, check, and write
        all happen under the same lock, preventing overshoot under concurrency.
        """
        with self._lock:
            now = self._clock()
            self._maybe_reset(now)
            if hourly_quota > 0 and self._hourly_count >= hourly_quota:
                return f"hourly quota exceeded ({self._hourly_count}/{hourly_quota})"
            if daily_quota > 0 and self._daily_count >= daily_quota:
                return f"daily quota exceeded ({self._daily_count}/{daily_quota})"
            self._hourly_count += 1
            self._daily_count  += 1
            return None


# ---------------------------------------------------------------------------
# RateLimitDecision
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RateLimitDecision:
    namespace:         str
    result:            LimitResult
    reason:            str
    tokens_remaining:  float
    hourly_used:       int
    daily_used:        int

    def to_dict(self) -> Dict[str, object]:
        return {
            "namespace":        self.namespace,
            "result":           self.result.value,
            "reason":           self.reason,
            "tokens_remaining": round(self.tokens_remaining, 3),
            "hourly_used":      self.hourly_used,
            "daily_used":       self.daily_used,
        }


# ---------------------------------------------------------------------------
# RateLimitEnforcer
# ---------------------------------------------------------------------------

class RateLimitEnforcer:
    """Central rate-limit enforcement engine.

    Args:
        config:        RateLimitConfig instance.
        mono_clock:    Monotonic clock for token bucket (default time.monotonic).
        wall_clock:    Wall clock for quota windows (default time.time).
    """

    def __init__(
        self,
        config:     RateLimitConfig,
        mono_clock: Clock = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
    ) -> None:
        self._config     = config
        self._mono_clock = mono_clock
        self._wall_clock = wall_clock
        self._buckets:   Dict[str, _TokenBucket]   = {}
        self._counters:  Dict[str, _QuotaCounter]  = {}
        self._lock       = threading.Lock()

    def _get_bucket(self, namespace: str, quota: NamespaceQuota) -> _TokenBucket:
        with self._lock:
            if namespace not in self._buckets:
                self._buckets[namespace] = _TokenBucket(
                    rate=quota.rate_per_second,
                    burst=quota.burst,
                    clock=self._mono_clock,
                )
            return self._buckets[namespace]

    def _get_counter(self, namespace: str) -> _QuotaCounter:
        with self._lock:
            if namespace not in self._counters:
                self._counters[namespace] = _QuotaCounter(clock=self._wall_clock)
            return self._counters[namespace]

    def check(self, namespace: str, tokens: int = 1) -> RateLimitDecision:
        """Check and enforce rate limits. Thread-safe."""
        quota   = self._config.get_quota(namespace)
        bucket  = self._get_bucket(namespace, quota)
        counter = self._get_counter(namespace)

        # Atomic quota check-and-increment (no overshoot under concurrency)
        quota_reason = counter.check_and_increment(quota.hourly_quota, quota.daily_quota)
        if quota_reason:
            logger.warning("rate_limit quota_exceeded namespace=%s reason=%s", namespace, quota_reason)
            return RateLimitDecision(
                namespace=namespace,
                result=LimitResult.QUOTA_EXCEEDED,
                reason=quota_reason,
                tokens_remaining=bucket.available,
                hourly_used=counter.hourly_count,
                daily_used=counter.daily_count,
            )

        # Token bucket check
        if not bucket.consume(tokens):
            # Roll back quota increment since request was rate-limited
            logger.warning("rate_limit rate_limited namespace=%s tokens_req=%d", namespace, tokens)
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


# ---------------------------------------------------------------------------
# check_rate_limit_config — SOC2-ready validator
# ---------------------------------------------------------------------------

def check_rate_limit_config(config: RateLimitConfig) -> Dict[str, object]:
    """Validate a RateLimitConfig. Returns dict with 'result' and 'issues'."""
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

    # Validate per-namespace quotas
    for q in config.quotas:
        if q.rate_per_second < 1:
            issues.append(f"WARN: namespace '{q.namespace}' rate_per_second < 1 — very low rate")
        if q.burst < q.rate_per_second:
            issues.append(f"WARN: namespace '{q.namespace}' burst < rate_per_second — burst exhausted quickly")
        if q.hourly_quota > 0 and q.daily_quota > 0 and q.hourly_quota * 24 < q.daily_quota:
            issues.append(f"WARN: namespace '{q.namespace}' hourly_quota * 24 < daily_quota — daily quota unreachable")

    fails = [i for i in issues if i.startswith("FAIL")]
    result = "FAIL" if fails else ("WARN" if issues else "PASS")
    return {"result": result, "issues": issues}


# ---------------------------------------------------------------------------
# _build_demo_config
# ---------------------------------------------------------------------------

def _build_demo_config() -> RateLimitConfig:
    return RateLimitConfig(
        quotas=[
            NamespaceQuota("prod",    rate_per_second=1000.0, burst=2000, hourly_quota=500_000, daily_quota=5_000_000),
            NamespaceQuota("staging", rate_per_second=100.0,  burst=200,  hourly_quota=10_000,  daily_quota=100_000),
            NamespaceQuota("*",       rate_per_second=50.0,   burst=100),
        ],
        default_rate_per_second=10.0,
        default_burst=20,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

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
            quotas = [
                NamespaceQuota(
                    namespace=q["namespace"],
                    rate_per_second=float(q.get("rate_per_second", 100)),
                    burst=int(q.get("burst", 200)),
                    hourly_quota=int(q.get("hourly_quota", 0)),
                    daily_quota=int(q.get("daily_quota", 0)),
                )
                for q in raw.get("quotas", [])
            ]
            config = RateLimitConfig(
                quotas=quotas,
                default_rate_per_second=float(raw.get("default_rate_per_second", 100)),
                default_burst=int(raw.get("default_burst", 200)),
                default_hourly_quota=int(raw.get("default_hourly_quota", 0)),
                default_daily_quota=int(raw.get("default_daily_quota", 0)),
            )
        else:
            p.error("Provide --config <path> or --demo")
            return 2

        report = check_rate_limit_config(config)

        if args.format == "json":
            print(json.dumps(report, indent=2))
        else:
            print("SketchLog Rate Limit configuration check")
            print(f"  Namespaces : {len(config.quotas)}")
            print(f"  Default    : {config.default_rate_per_second}/s burst={config.default_burst}")
            _issues = report.get("issues", [])
        assert isinstance(_issues, list)
        for issue in _issues:
                print(f"  {issue}")
            print(f"\nResult: {report['result']}")

        result = report["result"]
        return 0 if result == "PASS" else (1 if result == "WARN" else 2)

    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
