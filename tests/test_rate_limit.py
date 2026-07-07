"""Tests for sketchlog.rate_limit (Issue #252)."""
from __future__ import annotations

import time
import threading
import pytest

from sketchlog.rate_limit import (
    LimitResult,
    NamespaceQuota,
    RateLimitConfig,
    RateLimitDecision,
    RateLimitEnforcer,
    check_rate_limit_config,
    _TokenBucket,
    _QuotaCounter,
)


# ─────────────────────────────────────────────────────────────────────────────
# NamespaceQuota validation
# ─────────────────────────────────────────────────────────────────────────────
class TestNamespaceQuota:
    def test_valid_quota(self):
        q = NamespaceQuota("prod", rate_per_second=1000, burst=5000)
        assert q.namespace == "prod"
        assert q.rate_per_second == 1000
        assert q.burst == 5000

    def test_default_values(self):
        q = NamespaceQuota("ns")
        assert q.daily_quota == 0
        assert q.hourly_quota == 0

    def test_empty_namespace_raises(self):
        with pytest.raises(ValueError, match="namespace"):
            NamespaceQuota("")

    def test_negative_rate_raises(self):
        with pytest.raises(ValueError, match="rate_per_second"):
            NamespaceQuota("ns", rate_per_second=-1)

    def test_zero_burst_raises(self):
        with pytest.raises(ValueError, match="burst"):
            NamespaceQuota("ns", burst=0)

    def test_negative_daily_quota_raises(self):
        with pytest.raises(ValueError, match="daily_quota"):
            NamespaceQuota("ns", daily_quota=-1)

    def test_negative_hourly_quota_raises(self):
        with pytest.raises(ValueError, match="hourly_quota"):
            NamespaceQuota("ns", hourly_quota=-1)

    def test_whitespace_namespace_raises(self):
        with pytest.raises(ValueError, match="namespace"):
            NamespaceQuota("   ")


# ─────────────────────────────────────────────────────────────────────────────
# RateLimitConfig
# ─────────────────────────────────────────────────────────────────────────────
class TestRateLimitConfig:
    def test_get_quota_exact_match(self):
        cfg = RateLimitConfig(quotas=[
            NamespaceQuota("prod", rate_per_second=5000, burst=10000),
        ])
        q = cfg.get_quota("prod")
        assert q.rate_per_second == 5000

    def test_get_quota_wildcard_fallback(self):
        cfg = RateLimitConfig(quotas=[
            NamespaceQuota("*", rate_per_second=100, burst=500),
        ])
        q = cfg.get_quota("anything")
        assert q.rate_per_second == 100

    def test_get_quota_default_fallback(self):
        cfg = RateLimitConfig(quotas=[], default_rate_per_second=42)
        q = cfg.get_quota("ns")
        assert q.rate_per_second == 42

    def test_exact_takes_precedence_over_wildcard(self):
        cfg = RateLimitConfig(quotas=[
            NamespaceQuota("*",    rate_per_second=10,  burst=50),
            NamespaceQuota("prod", rate_per_second=999, burst=9999),
        ])
        q = cfg.get_quota("prod")
        assert q.rate_per_second == 999


# ─────────────────────────────────────────────────────────────────────────────
# _TokenBucket
# ─────────────────────────────────────────────────────────────────────────────
class TestTokenBucket:
    def test_full_bucket_allows(self):
        b = _TokenBucket(rate=100, burst=100)
        assert b.consume(1) is True

    def test_empty_bucket_rejects(self):
        b = _TokenBucket(rate=100, burst=5)
        for _ in range(5):
            b.consume(1)
        assert b.consume(1) is False

    def test_burst_limited(self):
        b = _TokenBucket(rate=1000, burst=3)
        assert b.consume(3) is True
        assert b.consume(1) is False

    def test_refill_over_time(self) -> None:
        """Token bucket refills over time — tested by mocking time.monotonic."""
        import unittest.mock as mock_mod
        bucket = _TokenBucket(rate_per_second=10.0, burst=10)
        # Drain all tokens
        for _ in range(10):
            assert bucket.consume(1)
        assert not bucket.consume(1), "bucket should be empty"

        # Advance time by 1 second via mock — should refill 10 tokens
        original_monotonic = bucket._last_refill
        with mock_mod.patch("time.monotonic", return_value=original_monotonic + 1.0):
            # Force refill by calling _refill directly
            bucket._refill()
            assert bucket.available >= 9.0, f"expected >= 9 tokens after 1s refill, got {bucket.available}"
        assert bucket.consume(1), "should be able to consume after refill"
    def test_available_property(self):
        b = _TokenBucket(rate=100, burst=50)
        b.consume(10)
        assert b.available < 50

    def test_thread_safety(self):
        b = _TokenBucket(rate=10000, burst=1000)
        results = []
        def worker():
            for _ in range(50):
                results.append(b.consume(1))
        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads: t.start()
        for t in threads: t.join()
        allowed = sum(1 for r in results if r)
        assert allowed <= 1000  # never exceed burst


# ─────────────────────────────────────────────────────────────────────────────
# _QuotaCounter
# ─────────────────────────────────────────────────────────────────────────────
class TestQuotaCounter:
    def test_increment_and_read(self):
        c = _QuotaCounter()
        c.increment()
        c.increment()
        assert c.hourly_count == 2
        assert c.daily_count == 2

    def test_starts_at_zero(self):
        c = _QuotaCounter()
        assert c.hourly_count == 0
        assert c.daily_count == 0

    def test_thread_safe_increment(self):
        c = _QuotaCounter()
        def worker():
            for _ in range(100):
                c.increment()
        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads: t.start()
        for t in threads: t.join()
        assert c.hourly_count == 1000
        assert c.daily_count == 1000


# ─────────────────────────────────────────────────────────────────────────────
# RateLimitEnforcer
# ─────────────────────────────────────────────────────────────────────────────
class TestRateLimitEnforcer:
    def _enforcer(self, **kw) -> RateLimitEnforcer:
        cfg = RateLimitConfig(
            quotas=[NamespaceQuota("ns", **kw)],
            enabled=True,
        )
        return RateLimitEnforcer(cfg)

    def test_allowed_when_under_limit(self):
        e = self._enforcer(rate_per_second=1000, burst=1000)
        d = e.check("ns")
        assert d.result == LimitResult.ALLOWED
        assert d.reason == "ok"

    def test_rate_limited_when_burst_exhausted(self):
        e = self._enforcer(rate_per_second=1, burst=2)
        e.check("ns")
        e.check("ns")
        d = e.check("ns")
        assert d.result == LimitResult.RATE_LIMITED

    def test_hourly_quota_enforced(self):
        e = self._enforcer(rate_per_second=10000, burst=10000, hourly_quota=2)
        e.check("ns")
        e.check("ns")
        d = e.check("ns")
        assert d.result == LimitResult.QUOTA_EXCEEDED
        assert "hourly" in d.reason

    def test_daily_quota_enforced(self):
        e = self._enforcer(rate_per_second=10000, burst=10000, daily_quota=2)
        e.check("ns")
        e.check("ns")
        d = e.check("ns")
        assert d.result == LimitResult.QUOTA_EXCEEDED
        assert "daily" in d.reason

    def test_disabled_allows_all(self):
        cfg = RateLimitConfig(enabled=False)
        e = RateLimitEnforcer(cfg)
        for _ in range(10000):
            d = e.check("any")
            assert d.result == LimitResult.ALLOWED

    def test_returns_decision_object(self):
        e = self._enforcer(rate_per_second=1000, burst=1000)
        d = e.check("ns")
        assert isinstance(d, RateLimitDecision)
        assert isinstance(d.tokens_remaining, float)
        assert isinstance(d.hourly_used, int)
        assert isinstance(d.daily_used, int)

    def test_decision_to_json(self):
        e = self._enforcer(rate_per_second=1000, burst=1000)
        d = e.check("ns")
        j = d.to_json()
        import json
        obj = json.loads(j)
        assert obj["namespace"] == "ns"
        assert obj["result"] == "allowed"

    def test_stats(self):
        e = self._enforcer(rate_per_second=1000, burst=500)
        e.check("ns")
        s = e.stats("ns")
        assert s["namespace"] == "ns"
        assert s["burst"] == 500
        assert s["hourly_used"] == 1

    def test_reset(self):
        e = self._enforcer(rate_per_second=1, burst=1)
        e.check("ns")
        e.check("ns")
        e.reset("ns")
        d = e.check("ns")
        assert d.result == LimitResult.ALLOWED

    def test_multiple_namespaces_independent(self):
        cfg = RateLimitConfig(quotas=[
            NamespaceQuota("a", rate_per_second=10000, burst=3, hourly_quota=3),
            NamespaceQuota("b", rate_per_second=10000, burst=10000),
        ])
        e = RateLimitEnforcer(cfg)
        for _ in range(3):
            e.check("a")
        d_a = e.check("a")
        d_b = e.check("b")
        assert d_a.result == LimitResult.QUOTA_EXCEEDED
        assert d_b.result == LimitResult.ALLOWED

    def test_wildcard_fallback_enforcer(self):
        cfg = RateLimitConfig(quotas=[
            NamespaceQuota("*", rate_per_second=10000, burst=5, hourly_quota=5),
        ])
        e = RateLimitEnforcer(cfg)
        for _ in range(5):
            e.check("random_ns")
        d = e.check("random_ns")
        # Either rate limited or quota exceeded
        assert d.result in (LimitResult.RATE_LIMITED, LimitResult.QUOTA_EXCEEDED)

    def test_thread_safe_enforcement(self):
        cfg = RateLimitConfig(quotas=[
            NamespaceQuota("ns", rate_per_second=100000, burst=500, hourly_quota=500),
        ])
        e = RateLimitEnforcer(cfg)
        results = []
        lock = threading.Lock()
        def worker():
            for _ in range(50):
                d = e.check("ns")
                with lock:
                    results.append(d.result)
        threads = [threading.Thread(target=worker) for _ in range(20)]
        for t in threads: t.start()
        for t in threads: t.join()
        allowed = sum(1 for r in results if r == LimitResult.ALLOWED)
        assert allowed <= 500  # never exceed quota


# ─────────────────────────────────────────────────────────────────────────────
# check_rate_limit_config
# ─────────────────────────────────────────────────────────────────────────────
class TestCheckRateLimitConfig:
    def test_valid_config_passes(self):
        cfg = RateLimitConfig(
            quotas=[NamespaceQuota("prod", rate_per_second=1000, burst=5000)],
            enabled=True,
        )
        r = check_rate_limit_config(cfg)
        assert r.status == "PASS"
        assert r.quota_count == 1
        assert r.enabled is True

    def test_disabled_warns(self):
        cfg = RateLimitConfig(enabled=False)
        r = check_rate_limit_config(cfg)
        assert r.status == "WARN"
        assert any("disabled" in i for i in r.issues)

    def test_no_quotas_warns(self):
        cfg = RateLimitConfig(quotas=[])
        r = check_rate_limit_config(cfg)
        assert r.status == "WARN"
        assert any("no namespace quotas" in i for i in r.issues)

    def test_duplicate_namespace_warns(self):
        cfg = RateLimitConfig(quotas=[
            NamespaceQuota("prod"),
            NamespaceQuota("prod"),
        ])
        r = check_rate_limit_config(cfg)
        assert r.status == "WARN"
        assert any("duplicate" in i for i in r.issues)

    def test_low_rate_warns(self):
        cfg = RateLimitConfig(quotas=[
            NamespaceQuota("ns", rate_per_second=0.1, burst=1),
        ])
        r = check_rate_limit_config(cfg)
        assert r.status == "WARN"

    def test_burst_less_than_rate_warns(self):
        cfg = RateLimitConfig(quotas=[
            NamespaceQuota("ns", rate_per_second=1000, burst=1),
        ])
        r = check_rate_limit_config(cfg)
        assert r.status == "WARN"

    def test_result_to_json(self):
        cfg = RateLimitConfig(
            quotas=[NamespaceQuota("ns")],
            enabled=True,
        )
        r = check_rate_limit_config(cfg)
        import json
        obj = json.loads(r.to_json())
        assert "status" in obj
        assert "issues" in obj
