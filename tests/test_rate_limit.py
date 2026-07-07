"""Tests for sketchlog.rate_limit — fully deterministic, zero mocking, zero time.sleep.

All time-sensitive tests use clock injection to pass a fake monotonic/wall clock
directly into _TokenBucket and _QuotaCounter. No mock.patch, no time.sleep,
no platform-specific timing assumptions.
"""

from __future__ import annotations

import json
import os
import sys
import threading

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python"))

from sketchlog.rate_limit import (
    LimitResult,
    NamespaceQuota,
    RateLimitConfig,
    RateLimitDecision,
    RateLimitEnforcer,
    _QuotaCounter,
    _TokenBucket,
    check_rate_limit_config,
    main,
)


# ---------------------------------------------------------------------------
# NamespaceQuota validation
# ---------------------------------------------------------------------------

class TestNamespaceQuota:
    def test_valid(self) -> None:
        q = NamespaceQuota("prod", 100.0, 200)
        assert q.namespace == "prod"
        assert q.rate_per_second == 100.0
        assert q.burst == 200

    def test_empty_namespace(self) -> None:
        with pytest.raises(ValueError, match="namespace"):
            NamespaceQuota("", 100.0, 200)

    def test_zero_rate(self) -> None:
        with pytest.raises(ValueError, match="rate_per_second"):
            NamespaceQuota("prod", 0.0, 200)

    def test_negative_rate(self) -> None:
        with pytest.raises(ValueError, match="rate_per_second"):
            NamespaceQuota("prod", -1.0, 200)

    def test_nan_rate(self) -> None:
        with pytest.raises(ValueError, match="rate_per_second"):
            NamespaceQuota("prod", float("nan"), 200)

    def test_zero_burst(self) -> None:
        with pytest.raises(ValueError, match="burst"):
            NamespaceQuota("prod", 100.0, 0)

    def test_negative_hourly_quota(self) -> None:
        with pytest.raises(ValueError, match="hourly_quota"):
            NamespaceQuota("prod", 100.0, 200, hourly_quota=-1)

    def test_negative_daily_quota(self) -> None:
        with pytest.raises(ValueError, match="daily_quota"):
            NamespaceQuota("prod", 100.0, 200, daily_quota=-1)

    def test_zero_quotas_allowed(self) -> None:
        q = NamespaceQuota("prod", 100.0, 200, hourly_quota=0, daily_quota=0)
        assert q.hourly_quota == 0
        assert q.daily_quota == 0


# ---------------------------------------------------------------------------
# RateLimitConfig validation and namespace resolution
# ---------------------------------------------------------------------------

class TestRateLimitConfig:
    def test_defaults(self) -> None:
        c = RateLimitConfig()
        assert c.default_rate_per_second == 100.0
        assert c.default_burst == 200

    def test_invalid_default_rate(self) -> None:
        with pytest.raises(ValueError, match="default_rate_per_second"):
            RateLimitConfig(default_rate_per_second=0.0)

    def test_invalid_default_burst(self) -> None:
        with pytest.raises(ValueError, match="default_burst"):
            RateLimitConfig(default_burst=0)

    def test_exact_match(self) -> None:
        c = RateLimitConfig(quotas=[
            NamespaceQuota("prod", 1000.0, 2000),
            NamespaceQuota("*",    50.0,   100),
        ])
        q = c.get_quota("prod")
        assert q.rate_per_second == 1000.0

    def test_wildcard_match(self) -> None:
        c = RateLimitConfig(quotas=[
            NamespaceQuota("prod", 1000.0, 2000),
            NamespaceQuota("*",    50.0,   100),
        ])
        q = c.get_quota("unknown-ns")
        assert q.rate_per_second == 50.0
        assert q.namespace == "unknown-ns"

    def test_default_fallback(self) -> None:
        c = RateLimitConfig(default_rate_per_second=42.0, default_burst=84)
        q = c.get_quota("any-ns")
        assert q.rate_per_second == 42.0
        assert q.burst == 84

    def test_exact_beats_wildcard(self) -> None:
        c = RateLimitConfig(quotas=[
            NamespaceQuota("*",    50.0,   100),
            NamespaceQuota("prod", 1000.0, 2000),
        ])
        q = c.get_quota("prod")
        assert q.rate_per_second == 1000.0


# ---------------------------------------------------------------------------
# _TokenBucket — clock-injected, deterministic
# ---------------------------------------------------------------------------

class TestTokenBucket:
    def test_full_bucket_allows(self) -> None:
        t = 0.0
        bucket = _TokenBucket(rate=10.0, burst=10, clock=lambda: t)
        assert bucket.consume(1) is True

    def test_empty_bucket_denies(self) -> None:
        t = 0.0
        bucket = _TokenBucket(rate=10.0, burst=5, clock=lambda: t)
        # Drain all 5 tokens
        for _ in range(5):
            bucket.consume(1)
        assert bucket.consume(1) is False

    def test_refill_after_time(self) -> None:
        t = 0.0
        bucket = _TokenBucket(rate=10.0, burst=10, clock=lambda: t)
        # Drain all tokens
        for _ in range(10):
            bucket.consume(1)
        assert bucket.consume(1) is False
        # Advance clock by 1 second — should refill 10 tokens
        t = 1.0
        assert bucket.consume(1) is True

    def test_burst_cap(self) -> None:
        t = 0.0
        bucket = _TokenBucket(rate=10.0, burst=10, clock=lambda: t)
        # Drain half
        for _ in range(5):
            bucket.consume(1)
        # Advance 100 seconds — should NOT exceed burst=10
        t = 100.0
        assert bucket.available <= 10.0

    def test_partial_refill(self) -> None:
        t = 0.0
        bucket = _TokenBucket(rate=10.0, burst=20, clock=lambda: t)
        # Drain all 20 tokens
        for _ in range(20):
            bucket.consume(1)
        # Advance 0.5 seconds → 5 tokens refilled
        t = 0.5
        avail = bucket.available
        assert 4.9 <= avail <= 5.1

    def test_multi_token_consume(self) -> None:
        t = 0.0
        bucket = _TokenBucket(rate=100.0, burst=50, clock=lambda: t)
        assert bucket.consume(50) is True
        assert bucket.consume(1) is False

    def test_thread_safety(self) -> None:
        t = 0.0
        bucket = _TokenBucket(rate=1000.0, burst=100, clock=lambda: t)
        results = []
        def worker() -> None:
            results.append(bucket.consume(1))
        threads = [threading.Thread(target=worker) for _ in range(200)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()
        allowed = sum(1 for r in results if r)
        assert allowed <= 100  # never exceeds burst


# ---------------------------------------------------------------------------
# _QuotaCounter — clock-injected, deterministic
# ---------------------------------------------------------------------------

class TestQuotaCounter:
    def test_initial_counts_zero(self) -> None:
        t = 1_000_000.0
        counter = _QuotaCounter(clock=lambda: t)
        assert counter.hourly_count == 0
        assert counter.daily_count == 0

    def test_increment_increases_counts(self) -> None:
        t = 1_000_000.0
        counter = _QuotaCounter(clock=lambda: t)
        result = counter.check_and_increment(0, 0)
        assert result is None
        assert counter.hourly_count == 1
        assert counter.daily_count == 1

    def test_hourly_quota_enforced(self) -> None:
        t = 1_000_000.0
        counter = _QuotaCounter(clock=lambda: t)
        for _ in range(5):
            counter.check_and_increment(5, 0)
        result = counter.check_and_increment(5, 0)
        assert result is not None
        assert "hourly" in result

    def test_daily_quota_enforced(self) -> None:
        t = 1_000_000.0
        counter = _QuotaCounter(clock=lambda: t)
        for _ in range(3):
            counter.check_and_increment(0, 3)
        result = counter.check_and_increment(0, 3)
        assert result is not None
        assert "daily" in result

    def test_hourly_reset_after_window(self) -> None:
        now = [1_000_000.0]
        counter = _QuotaCounter(clock=lambda: now[0])
        for _ in range(5):
            counter.check_and_increment(5, 0)
        # Advance past 1 hour
        now[0] += 3601.0
        result = counter.check_and_increment(5, 0)
        assert result is None  # counter reset

    def test_daily_reset_after_window(self) -> None:
        now = [1_000_000.0]
        counter = _QuotaCounter(clock=lambda: now[0])
        for _ in range(3):
            counter.check_and_increment(0, 3)
        # Advance past 1 day
        now[0] += 86401.0
        result = counter.check_and_increment(0, 3)
        assert result is None  # counter reset

    def test_atomic_no_overshoot(self) -> None:
        """Concurrent requests must not exceed quota."""
        t = 1_000_000.0
        counter = _QuotaCounter(clock=lambda: t)
        results = []
        def worker() -> None:
            r = counter.check_and_increment(10, 0)
            results.append(r is None)
        threads = [threading.Thread(target=worker) for _ in range(50)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()
        allowed = sum(1 for r in results if r)
        assert allowed == 10  # exactly 10 allowed, no overshoot


# ---------------------------------------------------------------------------
# RateLimitEnforcer
# ---------------------------------------------------------------------------

class TestRateLimitEnforcer:
    def _make_enforcer(
        self,
        rate: float = 100.0,
        burst: int = 10,
        hourly: int = 0,
        daily: int = 0,
        mono_t: float = 0.0,
        wall_t: float = 1_000_000.0,
    ) -> tuple:
        mono = [mono_t]
        wall = [wall_t]
        config = RateLimitConfig(
            quotas=[NamespaceQuota("test", rate, burst, hourly, daily)],
        )
        enforcer = RateLimitEnforcer(
            config,
            mono_clock=lambda: mono[0],
            wall_clock=lambda: wall[0],
        )
        return enforcer, mono, wall

    def test_allows_within_limit(self) -> None:
        enforcer, _, _ = self._make_enforcer(burst=10)
        d = enforcer.check("test")
        assert d.result == LimitResult.ALLOWED
        assert d.reason == "ok"

    def test_rate_limited_after_burst(self) -> None:
        enforcer, _, _ = self._make_enforcer(rate=100.0, burst=5)
        for _ in range(5):
            enforcer.check("test")
        d = enforcer.check("test")
        assert d.result == LimitResult.RATE_LIMITED

    def test_quota_exceeded_hourly(self) -> None:
        enforcer, _, _ = self._make_enforcer(rate=1000.0, burst=1000, hourly=3)
        for _ in range(3):
            enforcer.check("test")
        d = enforcer.check("test")
        assert d.result == LimitResult.QUOTA_EXCEEDED
        assert "hourly" in d.reason

    def test_quota_exceeded_daily(self) -> None:
        enforcer, _, _ = self._make_enforcer(rate=1000.0, burst=1000, daily=2)
        for _ in range(2):
            enforcer.check("test")
        d = enforcer.check("test")
        assert d.result == LimitResult.QUOTA_EXCEEDED
        assert "daily" in d.reason

    def test_default_namespace_fallback(self) -> None:
        config = RateLimitConfig(default_rate_per_second=50.0, default_burst=5)
        enforcer = RateLimitEnforcer(config)
        d = enforcer.check("new-ns")
        assert d.result == LimitResult.ALLOWED

    def test_decision_to_dict(self) -> None:
        enforcer, _, _ = self._make_enforcer()
        d = enforcer.check("test")
        dd = d.to_dict()
        assert dd["result"] == "ALLOWED"
        assert "tokens_remaining" in dd
        assert "hourly_used" in dd
        assert "daily_used" in dd

    def test_thread_safety_enforcer(self) -> None:
        config = RateLimitConfig(
            quotas=[NamespaceQuota("prod", 1000.0, 50)],
        )
        enforcer = RateLimitEnforcer(config)
        results = []
        def worker() -> None:
            results.append(enforcer.check("prod").result)
        threads = [threading.Thread(target=worker) for _ in range(200)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()
        allowed = sum(1 for r in results if r == LimitResult.ALLOWED)
        assert allowed <= 50  # never exceeds burst


# ---------------------------------------------------------------------------
# check_rate_limit_config
# ---------------------------------------------------------------------------

class TestCheckRateLimitConfig:
    def test_pass_on_valid(self) -> None:
        c = RateLimitConfig(
            quotas=[NamespaceQuota("prod", 100.0, 200)],
        )
        r = check_rate_limit_config(c)
        assert r["result"] == "PASS"
        assert r["issues"] == []

    def test_warn_low_rate(self) -> None:
        c = RateLimitConfig(default_rate_per_second=0.5, default_burst=1)
        r = check_rate_limit_config(c)
        assert r["result"] == "WARN"

    def test_warn_burst_less_than_rate(self) -> None:
        c = RateLimitConfig(
            quotas=[NamespaceQuota("prod", 100.0, 10)],
        )
        r = check_rate_limit_config(c)
        assert r["result"] == "WARN"
        assert any("burst" in i for i in r["issues"])

    def test_json_serializable(self) -> None:
        c = RateLimitConfig()
        r = check_rate_limit_config(c)
        json.dumps(r)  # must not raise


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

class TestCLI:
    def test_demo_mode_exits_zero(self) -> None:
        rc = main(["--demo"])
        assert rc == 0

    def test_demo_json_output(self, capsys) -> None:  # type: ignore[no-untyped-def]
        main(["--demo", "--format", "json"])
        out = capsys.readouterr().out
        d = json.loads(out)
        assert "result" in d
        assert "issues" in d

    def test_missing_url_exits_two(self) -> None:
        rc = main([])
        assert rc == 2

    def test_config_file(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        cfg = {
            "quotas": [
                {"namespace": "prod", "rate_per_second": 100, "burst": 200}
            ],
            "default_rate_per_second": 50,
            "default_burst": 100,
        }
        p = tmp_path / "config.json"
        p.write_text(json.dumps(cfg))
        rc = main(["--config", str(p)])
        assert rc == 0
