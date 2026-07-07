"""Tests for sketchlog.rate_limit."""
from __future__ import annotations
import json, sys, os, time, threading, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python"))
import pytest
from sketchlog.rate_limit import (
    NamespaceQuota, RateLimitConfig, _TokenBucket, _QuotaCounter,
    RateLimitDecision, RateLimitEnforcer, LimitResult,
    check_rate_limit_config, _build_demo_config, main,
)


class TestNamespaceQuota:
    def test_valid(self):
        q = NamespaceQuota("prod", 100.0, 200)
        assert q.namespace == "prod"

    def test_empty_namespace(self):
        with pytest.raises(ValueError, match="namespace"):
            NamespaceQuota("", 100.0, 200)

    def test_zero_rate(self):
        with pytest.raises(ValueError, match="rate_per_second"):
            NamespaceQuota("ns", 0.0, 200)

    def test_negative_rate(self):
        with pytest.raises(ValueError, match="rate_per_second"):
            NamespaceQuota("ns", -1.0, 200)

    def test_nan_rate(self):
        with pytest.raises(ValueError, match="rate_per_second"):
            NamespaceQuota("ns", float("nan"), 200)

    def test_zero_burst(self):
        with pytest.raises(ValueError, match="burst"):
            NamespaceQuota("ns", 100.0, 0)

    def test_negative_hourly_quota(self):
        with pytest.raises(ValueError, match="hourly_quota"):
            NamespaceQuota("ns", 100.0, 200, hourly_quota=-1)

    def test_negative_daily_quota(self):
        with pytest.raises(ValueError, match="daily_quota"):
            NamespaceQuota("ns", 100.0, 200, daily_quota=-1)

    def test_wildcard(self):
        q = NamespaceQuota("*", 50.0, 100)
        assert q.namespace == "*"


class TestRateLimitConfig:
    def test_default(self):
        c = RateLimitConfig()
        assert c.default_rate_per_second == 100.0

    def test_invalid_default_rate(self):
        with pytest.raises(ValueError):
            RateLimitConfig(default_rate_per_second=0)

    def test_invalid_default_burst(self):
        with pytest.raises(ValueError):
            RateLimitConfig(default_burst=0)

    def test_get_quota_exact(self):
        q = NamespaceQuota("prod", 500.0, 1000)
        c = RateLimitConfig(quotas=[q])
        assert c.get_quota("prod").rate_per_second == 500.0

    def test_get_quota_wildcard(self):
        q = NamespaceQuota("*", 50.0, 100)
        c = RateLimitConfig(quotas=[q])
        assert c.get_quota("anything").rate_per_second == 50.0

    def test_get_quota_default(self):
        c = RateLimitConfig(default_rate_per_second=25.0, default_burst=50)
        q = c.get_quota("unknown")
        assert q.rate_per_second == 25.0

    def test_exact_beats_wildcard(self):
        quotas = [
            NamespaceQuota("prod", 500.0, 1000),
            NamespaceQuota("*",    50.0,  100),
        ]
        c = RateLimitConfig(quotas=quotas)
        assert c.get_quota("prod").rate_per_second == 500.0
        assert c.get_quota("staging").rate_per_second == 50.0


class TestTokenBucket:
    def test_consume_allowed(self):
        b = _TokenBucket(100.0, 10)
        assert b.consume(1) is True

    def test_consume_too_many(self):
        b = _TokenBucket(100.0, 5)
        assert b.consume(10) is False

    def test_refills_over_time(self):
        b = _TokenBucket(1000.0, 2)
        b.consume(2)
        time.sleep(0.01)
        assert b.consume(1) is True

    def test_available(self):
        b = _TokenBucket(100.0, 10)
        assert b.available == 10.0

    def test_thread_safety(self):
        b = _TokenBucket(1000.0, 100)
        results = []
        def consume():
            results.append(b.consume(1))
        threads = [threading.Thread(target=consume) for _ in range(50)]
        for t in threads: t.start()
        for t in threads: t.join()
        assert results.count(True) <= 100


class TestQuotaCounter:
    def test_increments(self):
        c = _QuotaCounter()
        allowed, reason = c.check_and_increment(10, 100)
        assert allowed is True
        assert c.hourly_count == 1

    def test_hourly_limit(self):
        c = _QuotaCounter()
        for _ in range(5):
            c.check_and_increment(5, 0)
        allowed, reason = c.check_and_increment(5, 0)
        assert allowed is False
        assert "hourly" in reason

    def test_daily_limit(self):
        c = _QuotaCounter()
        for _ in range(3):
            c.check_and_increment(0, 3)
        allowed, reason = c.check_and_increment(0, 3)
        assert allowed is False
        assert "daily" in reason

    def test_unlimited(self):
        c = _QuotaCounter()
        for _ in range(1000):
            allowed, _ = c.check_and_increment(0, 0)
        assert allowed is True

    def test_atomic_thread_safety(self):
        c = _QuotaCounter()
        limit = 100
        results = []
        def check():
            allowed, _ = c.check_and_increment(limit, 0)
            results.append(allowed)
        threads = [threading.Thread(target=check) for _ in range(150)]
        for t in threads: t.start()
        for t in threads: t.join()
        assert results.count(True) == limit


class TestRateLimitEnforcer:
    def test_allows_normal(self):
        config = RateLimitConfig(default_rate_per_second=100.0, default_burst=200)
        e = RateLimitEnforcer(config)
        d = e.check("prod")
        assert d.result == LimitResult.ALLOWED

    def test_rate_limited(self):
        config = RateLimitConfig(
            quotas=[NamespaceQuota("ns", 1.0, 1)],
        )
        e = RateLimitEnforcer(config)
        e.check("ns")  # consume the 1 token
        d = e.check("ns")
        assert d.result == LimitResult.RATE_LIMITED

    def test_quota_exceeded(self):
        config = RateLimitConfig(
            quotas=[NamespaceQuota("ns", 1000.0, 2000, hourly_quota=2)],
        )
        e = RateLimitEnforcer(config)
        e.check("ns")
        e.check("ns")
        d = e.check("ns")
        assert d.result == LimitResult.QUOTA_EXCEEDED

    def test_decision_to_dict(self):
        config = RateLimitConfig()
        e = RateLimitEnforcer(config)
        d = e.check("prod")
        data = d.to_dict()
        assert "namespace" in data
        assert "result" in data
        assert data["result"] == "ALLOWED"

    def test_namespace_isolation(self):
        config = RateLimitConfig(
            quotas=[NamespaceQuota("tight", 1.0, 1)],
            default_rate_per_second=100.0, default_burst=200,
        )
        e = RateLimitEnforcer(config)
        e.check("tight")
        d_tight = e.check("tight")
        d_other = e.check("other")
        assert d_tight.result == LimitResult.RATE_LIMITED
        assert d_other.result == LimitResult.ALLOWED


class TestCheckRateLimitConfig:
    def test_valid_passes(self):
        config = RateLimitConfig(
            quotas=[NamespaceQuota("prod", 100.0, 200)],
        )
        status, issues = check_rate_limit_config(config)
        assert status == "PASS"

    def test_empty_quotas_warns(self):
        config = RateLimitConfig()
        status, issues = check_rate_limit_config(config)
        assert status == "WARN"
        assert any("no per-namespace" in i for i in issues)

    def test_low_rate_warns(self):
        config = RateLimitConfig(
            quotas=[NamespaceQuota("ns", 0.5, 1)],
        )
        status, issues = check_rate_limit_config(config)
        assert status == "WARN"

    def test_burst_less_than_rate_warns(self):
        config = RateLimitConfig(
            quotas=[NamespaceQuota("ns", 100.0, 50)],
        )
        status, issues = check_rate_limit_config(config)
        assert status == "WARN"


class TestDemo:
    def test_demo_config_valid(self):
        config = _build_demo_config()
        status, issues = check_rate_limit_config(config)
        assert status == "PASS"

    def test_demo_config_has_quotas(self):
        config = _build_demo_config()
        assert len(config.quotas) >= 2


class TestCLI:
    def test_demo_text(self):
        rc = main(["--demo"])
        assert rc == 0

    def test_demo_json(self):
        rc = main(["--demo", "--format", "json"])
        assert rc == 0

    def test_missing_args(self):
        rc = main([])
        assert rc == 2

    def test_config_file(self, tmp_path):
        cfg = {
            "quotas": [{"namespace": "prod", "rate_per_second": 100.0, "burst": 200}],
            "default_rate_per_second": 50.0,
            "default_burst": 100,
        }
        p = tmp_path / "cfg.json"
        p.write_text(json.dumps(cfg))
        rc = main(["--config", str(p)])
        assert rc == 0
