"""Tests for sketchlog.rate_limit — zero mock.patch, zero time.sleep."""
from __future__ import annotations

import json
import sys
import threading
import unittest
from io import StringIO
from typing import List

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
# Helpers
# ---------------------------------------------------------------------------

def _clock(t: float):
    """Return a simple clock lambda fixed at *t*."""
    return lambda: t


def _advancing_clock(values: List[float]):
    """Return a clock that returns successive values from *values*."""
    it = iter(values)
    return lambda: next(it)


# ---------------------------------------------------------------------------
# NamespaceQuota validation
# ---------------------------------------------------------------------------

class TestNamespaceQuotaValidation(unittest.TestCase):
    def test_valid(self):
        q = NamespaceQuota("prod", rate_per_second=100.0, burst=200)
        self.assertEqual(q.namespace, "prod")

    def test_empty_namespace(self):
        with self.assertRaises(ValueError):
            NamespaceQuota("")

    def test_zero_rate(self):
        with self.assertRaises(ValueError):
            NamespaceQuota("prod", rate_per_second=0.0)

    def test_negative_rate(self):
        with self.assertRaises(ValueError):
            NamespaceQuota("prod", rate_per_second=-1.0)

    def test_zero_burst(self):
        with self.assertRaises(ValueError):
            NamespaceQuota("prod", burst=0)

    def test_negative_hourly_quota(self):
        with self.assertRaises(ValueError):
            NamespaceQuota("prod", hourly_quota=-1)

    def test_negative_daily_quota(self):
        with self.assertRaises(ValueError):
            NamespaceQuota("prod", daily_quota=-1)

    def test_hourly_exceeds_daily(self):
        with self.assertRaises(ValueError):
            NamespaceQuota("prod", hourly_quota=1000, daily_quota=500)

    def test_wildcard_allowed(self):
        q = NamespaceQuota("*", rate_per_second=50.0, burst=100)
        self.assertEqual(q.namespace, "*")


# ---------------------------------------------------------------------------
# RateLimitConfig validation
# ---------------------------------------------------------------------------

class TestRateLimitConfigValidation(unittest.TestCase):
    def test_valid_defaults(self):
        c = RateLimitConfig()
        self.assertEqual(c.default_rate_per_second, 100.0)

    def test_zero_default_rate(self):
        with self.assertRaises(ValueError):
            RateLimitConfig(default_rate_per_second=0.0)

    def test_zero_default_burst(self):
        with self.assertRaises(ValueError):
            RateLimitConfig(default_burst=0)

    def test_namespace_resolution_exact(self):
        c = RateLimitConfig(quotas=[
            NamespaceQuota("prod", rate_per_second=500.0, burst=1000),
            NamespaceQuota("*",    rate_per_second=10.0,  burst=20),
        ])
        q = c.get_quota("prod")
        self.assertEqual(q.rate_per_second, 500.0)

    def test_namespace_resolution_wildcard(self):
        c = RateLimitConfig(quotas=[
            NamespaceQuota("*", rate_per_second=10.0, burst=20),
        ])
        q = c.get_quota("unknown")
        self.assertEqual(q.rate_per_second, 10.0)

    def test_namespace_resolution_default(self):
        c = RateLimitConfig(default_rate_per_second=7.0, default_burst=14)
        q = c.get_quota("anything")
        self.assertEqual(q.rate_per_second, 7.0)


# ---------------------------------------------------------------------------
# _TokenBucket — clock-injected tests
# ---------------------------------------------------------------------------

class TestTokenBucket(unittest.TestCase):
    def test_initial_tokens_equal_burst(self):
        bucket = _TokenBucket(100.0, 50, clock=_clock(0.0))
        self.assertAlmostEqual(bucket.available, 50.0, places=1)

    def test_consume_success(self):
        bucket = _TokenBucket(100.0, 50, clock=_clock(0.0))
        self.assertTrue(bucket.consume(1))

    def test_consume_fails_when_empty(self):
        bucket = _TokenBucket(100.0, 2, clock=_clock(0.0))
        bucket.consume(1)
        bucket.consume(1)
        self.assertFalse(bucket.consume(1))

    def test_refill_after_time(self):
        t = [0.0]
        def clock():
            return t[0]
        bucket = _TokenBucket(10.0, 10, clock=clock)
        # drain all tokens
        for _ in range(10):
            bucket.consume(1)
        self.assertAlmostEqual(bucket.available, 0.0, places=1)
        # advance 1 second -> should gain 10 tokens (capped at burst=10)
        t[0] = 1.0
        self.assertAlmostEqual(bucket.available, 10.0, places=1)

    def test_refill_capped_at_burst(self):
        t = [0.0]
        def clock():
            return t[0]
        bucket = _TokenBucket(10.0, 5, clock=clock)
        t[0] = 100.0  # huge elapsed time
        self.assertAlmostEqual(bucket.available, 5.0, places=1)

    def test_partial_consume(self):
        bucket = _TokenBucket(100.0, 10, clock=_clock(0.0))
        self.assertTrue(bucket.consume(5))
        self.assertAlmostEqual(bucket.available, 5.0, places=1)


# ---------------------------------------------------------------------------
# _QuotaCounter — clock-injected tests
# ---------------------------------------------------------------------------

class TestQuotaCounter(unittest.TestCase):
    def test_initial_counts_zero(self):
        c = _QuotaCounter(clock=_clock(0.0))
        self.assertEqual(c.hourly_count, 0)
        self.assertEqual(c.daily_count, 0)

    def test_check_and_increment_allowed(self):
        c = _QuotaCounter(clock=_clock(0.0))
        err = c.check_and_increment(10, 100)
        self.assertIsNone(err)
        self.assertEqual(c.hourly_count, 1)
        self.assertEqual(c.daily_count, 1)

    def test_hourly_quota_exceeded(self):
        c = _QuotaCounter(clock=_clock(0.0))
        for _ in range(5):
            c.check_and_increment(5, 0)
        err = c.check_and_increment(5, 0)
        self.assertIsNotNone(err)
        self.assertIn("hourly", err)

    def test_daily_quota_exceeded(self):
        c = _QuotaCounter(clock=_clock(0.0))
        for _ in range(3):
            c.check_and_increment(0, 3)
        err = c.check_and_increment(0, 3)
        self.assertIsNotNone(err)
        self.assertIn("daily", err)

    def test_decrement(self):
        c = _QuotaCounter(clock=_clock(0.0))
        c.check_and_increment(0, 0)
        self.assertEqual(c.hourly_count, 1)
        c.decrement()
        self.assertEqual(c.hourly_count, 0)

    def test_hourly_reset(self):
        t = [0.0]
        def clock():
            return t[0]
        c = _QuotaCounter(clock=clock)
        c.check_and_increment(0, 0)
        self.assertEqual(c.hourly_count, 1)
        t[0] = 3601.0
        self.assertEqual(c.hourly_count, 0)  # reset

    def test_daily_reset(self):
        t = [0.0]
        def clock():
            return t[0]
        c = _QuotaCounter(clock=clock)
        c.check_and_increment(0, 0)
        self.assertEqual(c.daily_count, 1)
        t[0] = 86401.0
        self.assertEqual(c.daily_count, 0)  # reset

    def test_unlimited_quotas(self):
        c = _QuotaCounter(clock=_clock(0.0))
        for _ in range(1000):
            err = c.check_and_increment(0, 0)
            self.assertIsNone(err)


# ---------------------------------------------------------------------------
# RateLimitEnforcer — end-to-end tests
# ---------------------------------------------------------------------------

class TestRateLimitEnforcer(unittest.TestCase):
    def _make_enforcer(self, rate=100.0, burst=10, hq=0, dq=0):
        config = RateLimitConfig(
            quotas=[NamespaceQuota("ns", rate_per_second=rate, burst=burst,
                                   hourly_quota=hq, daily_quota=dq)],
        )
        return RateLimitEnforcer(config, clock=_clock(0.0))

    def test_allowed(self):
        e = self._make_enforcer()
        d = e.check("ns")
        self.assertEqual(d.result, LimitResult.ALLOWED)

    def test_rate_limited_after_burst(self):
        e = self._make_enforcer(rate=100.0, burst=3)
        for _ in range(3):
            e.check("ns")
        d = e.check("ns")
        self.assertEqual(d.result, LimitResult.RATE_LIMITED)

    def test_quota_exceeded_hourly(self):
        e = self._make_enforcer(burst=1000, hq=2)
        e.check("ns")
        e.check("ns")
        d = e.check("ns")
        self.assertEqual(d.result, LimitResult.QUOTA_EXCEEDED)

    def test_quota_exceeded_daily(self):
        e = self._make_enforcer(burst=1000, dq=2)
        e.check("ns")
        e.check("ns")
        d = e.check("ns")
        self.assertEqual(d.result, LimitResult.QUOTA_EXCEEDED)

    def test_quota_rollback_on_rate_limit(self):
        """Rate-limited requests must NOT consume hourly/daily quota."""
        e = self._make_enforcer(rate=100.0, burst=2, hq=10)
        # Allow 2 (burst), then 3rd is rate-limited
        e.check("ns")
        e.check("ns")
        d = e.check("ns")
        self.assertEqual(d.result, LimitResult.RATE_LIMITED)
        # Quota should only reflect the 2 actually-served requests
        counter = e._counters["ns"]
        self.assertEqual(counter.hourly_count, 2)

    def test_wildcard_namespace(self):
        config = RateLimitConfig(
            quotas=[NamespaceQuota("*", rate_per_second=50.0, burst=5)],
        )
        e = RateLimitEnforcer(config, clock=_clock(0.0))
        d = e.check("any-ns")
        self.assertEqual(d.result, LimitResult.ALLOWED)

    def test_default_namespace_fallback(self):
        config = RateLimitConfig(default_rate_per_second=10.0, default_burst=5)
        e = RateLimitEnforcer(config, clock=_clock(0.0))
        d = e.check("unregistered")
        self.assertEqual(d.result, LimitResult.ALLOWED)

    def test_decision_to_dict(self):
        e = self._make_enforcer()
        d = e.check("ns")
        obj = d.to_dict()
        self.assertIn("result", obj)
        self.assertIn("tokens_remaining", obj)

    def test_decision_to_json(self):
        e = self._make_enforcer()
        d = e.check("ns")
        obj = json.loads(d.to_json())
        self.assertEqual(obj["result"], LimitResult.ALLOWED)


# ---------------------------------------------------------------------------
# Thread-safety
# ---------------------------------------------------------------------------

class TestThreadSafety(unittest.TestCase):
    def test_concurrent_checks(self):
        config = RateLimitConfig(
            quotas=[NamespaceQuota("ns", rate_per_second=10000.0, burst=10000)],
        )
        enforcer = RateLimitEnforcer(config, clock=_clock(0.0))
        results = []
        lock    = threading.Lock()

        def worker():
            d = enforcer.check("ns")
            with lock:
                results.append(d.result)

        threads = [threading.Thread(target=worker) for _ in range(50)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(results), 50)
        allowed = sum(1 for r in results if r == LimitResult.ALLOWED)
        self.assertGreater(allowed, 0)

    def test_quota_not_exceeded_under_concurrency(self):
        """Atomic check_and_increment must never overshoot the quota."""
        config = RateLimitConfig(
            quotas=[NamespaceQuota("ns", rate_per_second=10000.0,
                                   burst=10000, hourly_quota=10)],
        )
        enforcer = RateLimitEnforcer(config, clock=_clock(0.0))
        results  = []
        lock     = threading.Lock()

        def worker():
            d = enforcer.check("ns")
            with lock:
                results.append(d.result)

        threads = [threading.Thread(target=worker) for _ in range(50)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        allowed = sum(1 for r in results if r == LimitResult.ALLOWED)
        self.assertLessEqual(allowed, 10)


# ---------------------------------------------------------------------------
# check_rate_limit_config
# ---------------------------------------------------------------------------

class TestCheckRateLimitConfig(unittest.TestCase):
    def test_pass(self):
        config = RateLimitConfig(
            quotas=[NamespaceQuota("prod", rate_per_second=100.0, burst=200)],
        )
        report = check_rate_limit_config(config)
        self.assertEqual(report["result"], "PASS")

    def test_warn_low_rate(self):
        config = RateLimitConfig(
            quotas=[NamespaceQuota("ns", rate_per_second=0.5, burst=1)],
        )
        report = check_rate_limit_config(config)
        self.assertIn(report["result"], ["WARN", "FAIL"])

    def test_warn_burst_less_than_rate(self):
        config = RateLimitConfig(
            quotas=[NamespaceQuota("ns", rate_per_second=100.0, burst=1)],
        )
        report = check_rate_limit_config(config)
        self.assertIn(report["result"], ["WARN", "FAIL"])

    def test_empty_config_passes(self):
        config = RateLimitConfig()
        report = check_rate_limit_config(config)
        self.assertEqual(report["result"], "PASS")


# ---------------------------------------------------------------------------
# CLI tests
# ---------------------------------------------------------------------------

class TestCLI(unittest.TestCase):
    def test_demo_text(self):
        rc = main(["--demo", "--format", "text"])
        self.assertIn(rc, (0, 1, 2))

    def test_demo_json(self):
        captured = StringIO()
        orig, sys.stdout = sys.stdout, captured
        try:
            main(["--demo", "--format", "json"])
        finally:
            sys.stdout = orig
        obj = json.loads(captured.getvalue())
        self.assertIn("result", obj)

    def test_no_args_runs_demo(self):
        rc = main([])
        self.assertIn(rc, (0, 1, 2))


if __name__ == "__main__":
    unittest.main()
