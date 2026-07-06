"""Tests for sketchlog.ci_regression — CI performance regression engine."""
from __future__ import annotations

import json
import math
import os
import sys
import tempfile
from typing import Any, Dict

import pytest

from sketchlog.ci_regression import (
    BenchResult,
    RegressionConfig,
    RegressionResult,
    _generate_demo_bench,
    _load_bench_file,
    _pct_change,
    _safe_float,
    compare,
    main,
    render_markdown,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _cfg(**kw: Any) -> RegressionConfig:
    defaults: Dict[str, Any] = dict(
        fail_p95=20.0, fail_p99=20.0, fail_event_rate=15.0, slo_burn_threshold=2.0
    )
    defaults.update(kw)
    return RegressionConfig(**defaults)


def _bench(**kw: Any) -> BenchResult:
    defaults: Dict[str, Any] = dict(
        p95_ms=5.0, p99_ms=10.0, event_rate_hz=100_000.0, slo_burn_rate=1.0
    )
    defaults.update(kw)
    return BenchResult(**defaults)


# ---------------------------------------------------------------------------
# RegressionConfig validation
# ---------------------------------------------------------------------------

class TestRegressionConfig:
    def test_defaults_valid(self) -> None:
        cfg = RegressionConfig()
        assert cfg.fail_p95 == 20.0
        assert cfg.fail_p99 == 20.0
        assert cfg.fail_event_rate == 15.0
        assert cfg.slo_burn_threshold == 2.0

    def test_zero_disables_check(self) -> None:
        cfg = _cfg(fail_p95=0.0, fail_p99=0.0, fail_event_rate=0.0, slo_burn_threshold=0.0)
        assert cfg.fail_p95 == 0.0

    def test_rejects_negative_threshold(self) -> None:
        with pytest.raises(ValueError, match="fail_p95"):
            _cfg(fail_p95=-1.0)

    def test_rejects_nan(self) -> None:
        with pytest.raises(ValueError, match="fail_p99"):
            _cfg(fail_p99=float("nan"))

    def test_rejects_inf(self) -> None:
        with pytest.raises(ValueError, match="slo_burn_threshold"):
            _cfg(slo_burn_threshold=float("inf"))

    def test_rejects_bool_threshold(self) -> None:
        with pytest.raises(ValueError, match="fail_p95"):
            _cfg(fail_p95=True)  # type: ignore[arg-type]

    def test_multi_error_aggregation(self) -> None:
        with pytest.raises(ValueError) as exc_info:
            _cfg(fail_p95=-1.0, fail_p99=-2.0)
        msg = str(exc_info.value)
        assert "fail_p95" in msg
        assert "fail_p99" in msg


# ---------------------------------------------------------------------------
# BenchResult validation
# ---------------------------------------------------------------------------

class TestBenchResult:
    def test_valid(self) -> None:
        b = _bench()
        assert b.p95_ms == 5.0

    def test_rejects_nan_p99(self) -> None:
        with pytest.raises(ValueError, match="p99_ms"):
            _bench(p99_ms=float("nan"))

    def test_rejects_inf_rate(self) -> None:
        with pytest.raises(ValueError, match="event_rate_hz"):
            _bench(event_rate_hz=float("inf"))

    def test_zero_values_allowed(self) -> None:
        b = _bench(p95_ms=0.0, p99_ms=0.0, event_rate_hz=0.0)
        assert b.p95_ms == 0.0


# ---------------------------------------------------------------------------
# _safe_float
# ---------------------------------------------------------------------------

class TestSafeFloat:
    def test_valid(self) -> None:
        assert _safe_float(3.14) == pytest.approx(3.14)

    def test_none_returns_default(self) -> None:
        assert _safe_float(None, 99.0) == 99.0

    def test_nan_returns_default(self) -> None:
        assert _safe_float(float("nan"), 7.0) == 7.0

    def test_inf_returns_default(self) -> None:
        assert _safe_float(float("inf"), 0.0) == 0.0

    def test_string_number(self) -> None:
        assert _safe_float("42.5") == pytest.approx(42.5)

    def test_non_numeric_string(self) -> None:
        assert _safe_float("bad", 1.0) == 1.0


# ---------------------------------------------------------------------------
# _pct_change
# ---------------------------------------------------------------------------

class TestPctChange:
    def test_no_change(self) -> None:
        assert _pct_change(10.0, 10.0) == pytest.approx(0.0)

    def test_positive_regression(self) -> None:
        assert _pct_change(10.0, 12.0) == pytest.approx(20.0)

    def test_improvement(self) -> None:
        assert _pct_change(10.0, 8.0) == pytest.approx(-20.0)

    def test_zero_baseline(self) -> None:
        assert _pct_change(0.0, 5.0) == 0.0


# ---------------------------------------------------------------------------
# compare() — pass cases
# ---------------------------------------------------------------------------

class TestComparePass:
    def test_identical_benches_pass(self) -> None:
        b = _bench()
        result = compare(b, b, _cfg())
        assert result.result == "PASS"

    def test_small_regression_pass(self) -> None:
        baseline = _bench(p99_ms=10.0)
        candidate = _bench(p99_ms=11.0)   # 10% regression, threshold 20%
        result = compare(baseline, candidate, _cfg())
        assert result.result == "PASS"
        assert result.p99_regression_pct == pytest.approx(10.0)

    def test_improvement_always_pass(self) -> None:
        baseline = _bench(p99_ms=10.0, event_rate_hz=100_000.0)
        candidate = _bench(p99_ms=8.0, event_rate_hz=120_000.0)
        result = compare(baseline, candidate, _cfg())
        assert result.result == "PASS"
        assert result.p99_regression_pct < 0
        assert result.event_rate_regression_pct < 0


# ---------------------------------------------------------------------------
# compare() — fail cases
# ---------------------------------------------------------------------------

class TestCompareFail:
    def test_p95_regression_fail(self) -> None:
        baseline = _bench(p95_ms=5.0)
        candidate = _bench(p95_ms=7.0)   # 40% regression, threshold 20%
        result = compare(baseline, candidate, _cfg(fail_p95=20.0))
        assert result.result == "FAIL"
        p95_chk = next(c for c in result.checks if c["name"] == "p95 latency")
        assert p95_chk["status"] == "FAIL"

    def test_p99_regression_fail(self) -> None:
        baseline = _bench(p99_ms=10.0)
        candidate = _bench(p99_ms=15.0)  # 50% regression
        result = compare(baseline, candidate, _cfg(fail_p99=20.0))
        assert result.result == "FAIL"

    def test_event_rate_drop_fail(self) -> None:
        baseline = _bench(event_rate_hz=100_000.0)
        candidate = _bench(event_rate_hz=70_000.0)  # 30% drop, threshold 15%
        result = compare(baseline, candidate, _cfg(fail_event_rate=15.0))
        assert result.result == "FAIL"

    def test_slo_burn_fail(self) -> None:
        baseline = _bench(slo_burn_rate=1.0)
        candidate = _bench(slo_burn_rate=3.0)   # 3x burn, threshold 2x
        result = compare(baseline, candidate, _cfg(slo_burn_threshold=2.0))
        assert result.result == "FAIL"
        burn_chk = next(c for c in result.checks if c["name"] == "SLO burn rate")
        assert burn_chk["status"] == "FAIL"
        assert result.slo_burn_ratio == pytest.approx(3.0)

    def test_disabled_check_never_fails(self) -> None:
        baseline = _bench(p99_ms=1.0)
        candidate = _bench(p99_ms=100.0)  # massive regression but p99 disabled
        result = compare(baseline, candidate, _cfg(fail_p99=0.0, fail_p95=0.0,
                                                    fail_event_rate=0.0,
                                                    slo_burn_threshold=0.0))
        assert result.result == "PASS"
        assert result.checks == []


# ---------------------------------------------------------------------------
# to_dict() JSON serializability
# ---------------------------------------------------------------------------

class TestToDict:
    def test_json_serializable(self) -> None:
        b = _bench()
        result = compare(b, b, _cfg())
        d = result.to_dict()
        # Must not raise
        raw = json.dumps(d)
        loaded = json.loads(raw)
        assert loaded["result"] == "PASS"

    def test_schema_fields(self) -> None:
        b = _bench()
        result = compare(b, b, _cfg())
        d = result.to_dict()
        for key in ["result", "p95_regression_pct", "p99_regression_pct",
                    "event_rate_regression_pct", "slo_burn_ratio",
                    "checks", "baseline", "candidate"]:
            assert key in d

    def test_baseline_candidate_nested(self) -> None:
        b = _bench()
        result = compare(b, b, _cfg())
        d = result.to_dict()
        assert "p95_ms" in d["baseline"]
        assert "p99_ms" in d["candidate"]


# ---------------------------------------------------------------------------
# render_markdown
# ---------------------------------------------------------------------------

class TestRenderMarkdown:
    def test_contains_result(self) -> None:
        b = _bench()
        result = compare(b, b, _cfg())
        md = render_markdown(result)
        assert "PASS" in md

    def test_fail_contains_fail(self) -> None:
        baseline = _bench(p99_ms=10.0)
        candidate = _bench(p99_ms=20.0)
        result = compare(baseline, candidate, _cfg(fail_p99=20.0))
        md = render_markdown(result)
        assert "FAIL" in md

    def test_contains_table_header(self) -> None:
        b = _bench()
        md = render_markdown(compare(b, b, _cfg()))
        assert "| Metric |" in md

    def test_contains_raw_numbers(self) -> None:
        b = _bench(p95_ms=3.14)
        md = render_markdown(compare(b, b, _cfg()))
        assert "3.14" in md


# ---------------------------------------------------------------------------
# _generate_demo_bench determinism
# ---------------------------------------------------------------------------

class TestDemoBench:
    def test_deterministic(self) -> None:
        a = _generate_demo_bench(seed_offset=0)
        b = _generate_demo_bench(seed_offset=0)
        assert a.p95_ms == b.p95_ms
        assert a.p99_ms == b.p99_ms

    def test_different_seeds_differ(self) -> None:
        a = _generate_demo_bench(seed_offset=0)
        b = _generate_demo_bench(seed_offset=999)
        assert a.p99_ms != b.p99_ms

    def test_demo_bench_valid(self) -> None:
        b = _generate_demo_bench()
        assert math.isfinite(b.p95_ms)
        assert math.isfinite(b.p99_ms)
        assert b.event_rate_hz > 0

    def test_demo_bench_json_serializable(self) -> None:
        b = _generate_demo_bench()
        d = {"p95": b.p95_ms, "p99": b.p99_ms, "rate": b.event_rate_hz}
        json.dumps(d)  # must not raise


# ---------------------------------------------------------------------------
# _load_bench_file
# ---------------------------------------------------------------------------

class TestLoadBenchFile:
    def test_empty_path_raises(self) -> None:
        with pytest.raises(ValueError, match="empty"):
            _load_bench_file("")

    def test_missing_file_raises(self) -> None:
        with pytest.raises(RuntimeError, match="Cannot load"):
            _load_bench_file("/nonexistent/path/bench.json")

    def test_invalid_json_raises(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write("not json {{")
            name = f.name
        try:
            with pytest.raises(RuntimeError, match="Cannot load"):
                _load_bench_file(name)
        finally:
            os.unlink(name)

    def test_non_dict_root_raises(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump([1, 2, 3], f)
            name = f.name
        try:
            with pytest.raises(RuntimeError, match="must contain a JSON object"):
                _load_bench_file(name)
        finally:
            os.unlink(name)

    def test_explicit_p95_p99_loaded(self) -> None:
        data = {"p95_ms": 4.5, "p99_ms": 9.1, "event_rate_hz": 95000.0}
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(data, f)
            name = f.name
        try:
            b = _load_bench_file(name)
            assert b.p95_ms == pytest.approx(4.5)
            assert b.p99_ms == pytest.approx(9.1)
            assert b.event_rate_hz == pytest.approx(95000.0)
        finally:
            os.unlink(name)

    def test_bench_lab_scenario_format(self) -> None:
        data = {
            "scenarios": {
                "latency_ingest": {"metrics": {"events_per_sec": 110_000.0}},
                "latency_quantile": {"metrics": {"p99_rel_err": 0.008}},
            }
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(data, f)
            name = f.name
        try:
            b = _load_bench_file(name)
            assert b.event_rate_hz == pytest.approx(110_000.0)
        finally:
            os.unlink(name)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

class TestCLI:
    def test_demo_passes(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            out = os.path.join(td, "results.json")
            summary = os.path.join(td, "summary.md")
            rc = main(["--demo", "--output", out, "--summary", summary])
            assert rc in (0, 1)  # demo may PASS or FAIL depending on values
            assert os.path.exists(out)
            assert os.path.exists(summary)

    def test_demo_produces_valid_json(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            out = os.path.join(td, "results.json")
            main(["--demo", "--output", out, "--summary", os.path.join(td, "s.md")])
            with open(out) as f:
                d = json.load(f)
            assert "result" in d
            assert d["result"] in ("PASS", "FAIL")

    def test_no_files_no_demo_exits_2(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            rc = main([
                "--output", os.path.join(td, "r.json"),
                "--summary", os.path.join(td, "s.md"),
            ])
            assert rc == 2

    def test_bad_threshold_exits_2(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            rc = main([
                "--demo",
                "--fail-p95", "-5.0",
                "--output", os.path.join(td, "r.json"),
                "--summary", os.path.join(td, "s.md"),
            ])
            assert rc == 2

    def test_explicit_files_pass(self) -> None:
        data = {"p95_ms": 5.0, "p99_ms": 10.0, "event_rate_hz": 100_000.0}
        with tempfile.TemporaryDirectory() as td:
            bf = os.path.join(td, "base.json")
            cf = os.path.join(td, "cand.json")
            out = os.path.join(td, "r.json")
            for path in (bf, cf):
                with open(path, "w") as f:
                    json.dump(data, f)
            rc = main([
                "--baseline-file", bf,
                "--candidate-file", cf,
                "--output", out,
                "--summary", os.path.join(td, "s.md"),
            ])
            assert rc == 0

    def test_failing_files_exits_1(self) -> None:
        base = {"p95_ms": 5.0, "p99_ms": 10.0, "event_rate_hz": 100_000.0}
        cand = {"p95_ms": 8.0, "p99_ms": 20.0, "event_rate_hz": 60_000.0}
        with tempfile.TemporaryDirectory() as td:
            bf = os.path.join(td, "base.json")
            cf = os.path.join(td, "cand.json")
            for path, data in [(bf, base), (cf, cand)]:
                with open(path, "w") as f:
                    json.dump(data, f)
            rc = main([
                "--baseline-file", bf,
                "--candidate-file", cf,
                "--fail-p99", "20.0",
                "--output", os.path.join(td, "r.json"),
                "--summary", os.path.join(td, "s.md"),
            ])
            assert rc == 1

    def test_env_var_thresholds(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SKETCHLOG_FAIL_P95", "5.0")
        monkeypatch.setenv("SKETCHLOG_FAIL_P99", "5.0")
        # A 10% regression should fail with threshold 5
        base = {"p95_ms": 10.0, "p99_ms": 10.0, "event_rate_hz": 100_000.0}
        cand = {"p95_ms": 11.0, "p99_ms": 11.0, "event_rate_hz": 100_000.0}
        with tempfile.TemporaryDirectory() as td:
            bf = os.path.join(td, "base.json")
            cf = os.path.join(td, "cand.json")
            for path, data in [(bf, base), (cf, cand)]:
                with open(path, "w") as f:
                    json.dump(data, f)
            rc = main([
                "--baseline-file", bf,
                "--candidate-file", cf,
                "--output", os.path.join(td, "r.json"),
                "--summary", os.path.join(td, "s.md"),
            ])
            assert rc == 1

    def test_export_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            out = os.path.join(td, "baseline.json")
            rc = main([
                "--export-baseline",
                "--output", out,
                "--summary", os.path.join(td, "s.md"),
            ])
            assert rc == 0
            with open(out) as f:
                d = json.load(f)
            assert "result" in d
