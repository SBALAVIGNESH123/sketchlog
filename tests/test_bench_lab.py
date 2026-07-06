"""Tests for python/sketchlog/bench_lab.py.

Run with:
    PYTHONPATH=python python -m pytest tests/test_bench_lab.py -q
"""

from __future__ import annotations

import json
import math
import os
import sys
import tempfile
from typing import List
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Path bootstrap — allows running from repo root via PYTHONPATH=python
# ---------------------------------------------------------------------------

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python"))

from sketchlog.bench_lab import (
    LabReport,
    ScenarioResult,
    _ALL_SCENARIOS,
    _SCENARIO_ALL,
    _bench_canary_comparison,
    _bench_freq_accuracy,
    _bench_freq_ingest,
    _bench_latency_ingest,
    _bench_latency_merge,
    _bench_latency_quantile,
    _bench_unique_accuracy,
    _bench_unique_ingest,
    _bench_serialized_size,
    _ddsketch_compact_bytes,
    _ddsketch_memory_bytes,
    _lognormal_latencies,
    _render_markdown,
    _rng,
    run_lab,
)
from sketchlog.core.ddsketch import DDSketch
from sketchlog.core.hll import HyperLogLog
from sketchlog.core.cms import CountMinSketch


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _quick_sketch(n: int = 1000) -> DDSketch:
    rng = _rng()
    s = DDSketch(relative_accuracy=0.01)
    s.add_batch(_lognormal_latencies(n, rng))
    return s


# ---------------------------------------------------------------------------
# Unit: internal helpers
# ---------------------------------------------------------------------------

class TestLognormalLatencies:
    def test_length(self) -> None:
        vals = _lognormal_latencies(500, _rng())
        assert len(vals) == 500

    def test_all_finite_positive(self) -> None:
        vals = _lognormal_latencies(1000, _rng())
        assert all(math.isfinite(v) and v > 0 for v in vals)

    def test_reproducible(self) -> None:
        a = _lognormal_latencies(200, _rng(seed=42))
        b = _lognormal_latencies(200, _rng(seed=42))
        assert a == b

    def test_different_seeds_differ(self) -> None:
        a = _lognormal_latencies(200, _rng(seed=1))
        b = _lognormal_latencies(200, _rng(seed=2))
        assert a != b


class TestDDSketchHelpers:
    def test_memory_bytes_positive(self) -> None:
        s = _quick_sketch(500)
        assert _ddsketch_memory_bytes(s) > 0

    def test_compact_bytes_positive(self) -> None:
        s = _quick_sketch(500)
        assert _ddsketch_compact_bytes(s) > 0

    def test_compact_bytes_is_valid_json(self) -> None:
        s = _quick_sketch(500)
        size = _ddsketch_compact_bytes(s)
        # Reconstruct by calling the same path used in the function
        d = {
            "pos": {str(k): v for k, v in s._positive.items()},
            "neg": {str(k): v for k, v in s._negative.items()},
            "zero": s._zero_count,
            "alpha": s._alpha,
        }
        text = json.dumps(d, separators=(",", ":"))
        assert len(text) == size
        assert json.loads(text) is not None

    def test_empty_sketch_memory(self) -> None:
        s = DDSketch(relative_accuracy=0.01)
        assert _ddsketch_memory_bytes(s) == 128  # base overhead only


# ---------------------------------------------------------------------------
# Scenario: latency_ingest
# ---------------------------------------------------------------------------

class TestLatencyIngest:
    def test_passes(self) -> None:
        r = _bench_latency_ingest()
        assert r.passed is True

    def test_has_required_keys(self) -> None:
        r = _bench_latency_ingest()
        for key in ("n_events", "throughput_eps", "memory_bytes",
                    "mean_s", "median_s", "stddev_s", "p95_s"):
            assert key in r.metrics, f"missing key: {key}"

    def test_throughput_positive(self) -> None:
        r = _bench_latency_ingest()
        assert r.metrics["throughput_eps"] > 0

    def test_memory_plausible(self) -> None:
        r = _bench_latency_ingest()
        # Between 256 B and 128 KiB for a 100 K lognormal ingest
        assert 256 <= r.metrics["memory_bytes"] <= 128 * 1024


# ---------------------------------------------------------------------------
# Scenario: latency_quantile
# ---------------------------------------------------------------------------

class TestLatencyQuantile:
    def test_passes(self) -> None:
        r = _bench_latency_quantile()
        assert r.passed is True

    def test_max_error_within_alpha(self) -> None:
        r = _bench_latency_quantile()
        assert r.metrics["max_relative_error"] <= 0.02

    def test_per_quantile_errors_present(self) -> None:
        r = _bench_latency_quantile()
        assert "per_quantile_errors" in r.metrics
        assert len(r.metrics["per_quantile_errors"]) == 6

    def test_query_latency_positive(self) -> None:
        r = _bench_latency_quantile()
        assert r.metrics["query_latency_mean_us"] > 0


# ---------------------------------------------------------------------------
# Scenario: latency_merge
# ---------------------------------------------------------------------------

class TestLatencyMerge:
    def test_passes(self) -> None:
        r = _bench_latency_merge()
        assert r.passed is True

    def test_shards_count(self) -> None:
        r = _bench_latency_merge()
        assert r.metrics["shards"] == 16

    def test_merges_per_sec_positive(self) -> None:
        r = _bench_latency_merge()
        assert r.metrics["merges_per_sec"] > 0


# ---------------------------------------------------------------------------
# Scenario: unique_ingest
# ---------------------------------------------------------------------------

class TestUniqueIngest:
    def test_passes(self) -> None:
        r = _bench_unique_ingest()
        assert r.passed is True

    def test_memory_plausible(self) -> None:
        r = _bench_unique_ingest()
        # precision=12 -> 2^12 = 4096 registers + 32 B overhead = 4128 B
        assert r.metrics["memory_bytes"] == 32 + (1 << 12)

    def test_throughput_positive(self) -> None:
        r = _bench_unique_ingest()
        assert r.metrics["throughput_eps"] > 0


# ---------------------------------------------------------------------------
# Scenario: unique_accuracy
# ---------------------------------------------------------------------------

class TestUniqueAccuracy:
    def test_passes(self) -> None:
        r = _bench_unique_accuracy()
        assert r.passed is True

    def test_max_error_within_guard(self) -> None:
        r = _bench_unique_accuracy()
        assert r.metrics["max_relative_error"] <= 0.05

    def test_five_cardinalities(self) -> None:
        r = _bench_unique_accuracy()
        assert len(r.metrics["cardinalities_tested"]) == 5
        assert len(r.metrics["relative_errors"]) == 5


# ---------------------------------------------------------------------------
# Scenario: freq_ingest
# ---------------------------------------------------------------------------

class TestFreqIngest:
    def test_passes(self) -> None:
        r = _bench_freq_ingest()
        assert r.passed is True

    def test_memory_matches_formula(self) -> None:
        r = _bench_freq_ingest()
        # CountMinSketch(width=2048, depth=5): 64 + 2048*5*8 = 81984
        assert r.metrics["memory_bytes"] == 64 + 2048 * 5 * 8

    def test_throughput_positive(self) -> None:
        r = _bench_freq_ingest()
        assert r.metrics["throughput_eps"] > 0


# ---------------------------------------------------------------------------
# Scenario: freq_accuracy
# ---------------------------------------------------------------------------

class TestFreqAccuracy:
    def test_passes(self) -> None:
        r = _bench_freq_accuracy()
        assert r.passed is True

    def test_no_undercount_violations(self) -> None:
        r = _bench_freq_accuracy()
        assert r.metrics["undercount_violations"] == 0

    def test_overestimate_non_negative(self) -> None:
        r = _bench_freq_accuracy()
        assert r.metrics["mean_overestimate_fraction"] >= 0.0


# ---------------------------------------------------------------------------
# Scenario: serialized_size
# ---------------------------------------------------------------------------

class TestSerializedSize:
    def test_passes(self) -> None:
        r = _bench_serialized_size()
        assert r.passed is True

    def test_all_sketches_present(self) -> None:
        r = _bench_serialized_size()
        for key in ("ddsketch", "hll", "cms"):
            assert key in r.metrics

    def test_compression_ratios_above_one(self) -> None:
        r = _bench_serialized_size()
        for key in ("ddsketch", "hll", "cms"):
            ratio = r.metrics[key]["compression_ratio"]
            assert ratio > 1.0, f"{key} compression ratio not > 1: {ratio}"

    def test_sketch_bytes_less_than_raw(self) -> None:
        r = _bench_serialized_size()
        for key in ("ddsketch", "hll", "cms"):
            assert r.metrics[key]["sketch_bytes"] < r.metrics[key]["raw_bytes"]


# ---------------------------------------------------------------------------
# Scenario: canary_comparison
# ---------------------------------------------------------------------------

class TestCanaryComparison:
    def test_passes(self) -> None:
        r = _bench_canary_comparison()
        assert r.passed is True

    def test_regression_detected(self) -> None:
        r = _bench_canary_comparison()
        assert r.metrics["regression_detected"] is True

    def test_regression_ratio_ge_1_8(self) -> None:
        r = _bench_canary_comparison()
        assert r.metrics["regression_ratio"] >= 1.8

    def test_canary_p99_higher_than_baseline(self) -> None:
        r = _bench_canary_comparison()
        assert r.metrics["canary_p99_ms"] > r.metrics["baseline_p99_ms"]


# ---------------------------------------------------------------------------
# run_lab integration
# ---------------------------------------------------------------------------

class TestRunLab:
    def test_all_scenarios_run(self) -> None:
        report = run_lab([_SCENARIO_ALL], verbose=False)
        names = {r.name for r in report.scenarios}
        assert names == set(_ALL_SCENARIOS)

    def test_passed_plus_failed_equals_total(self) -> None:
        report = run_lab([_SCENARIO_ALL], verbose=False)
        assert report.passed + report.failed == len(report.scenarios)

    def test_all_pass(self) -> None:
        report = run_lab([_SCENARIO_ALL], verbose=False)
        failed = [r.name for r in report.scenarios if not r.passed]
        assert failed == [], f"Scenarios failed: {failed}"

    def test_single_scenario(self) -> None:
        report = run_lab(["latency_ingest"], verbose=False)
        assert len(report.scenarios) == 1
        assert report.scenarios[0].name == "latency_ingest"

    def test_unknown_scenario_marked_failed(self) -> None:
        report = run_lab(["nonexistent_scenario"], verbose=False)
        assert report.failed == 1
        assert report.scenarios[0].error is not None

    def test_environment_populated(self) -> None:
        report = run_lab(["latency_ingest"], verbose=False)
        assert "python" in report.environment
        assert "os" in report.environment

    def test_generated_at_is_iso8601(self) -> None:
        report = run_lab(["latency_ingest"], verbose=False)
        assert report.generated_at.endswith("Z")

    def test_subset_of_scenarios(self) -> None:
        subset = ["latency_ingest", "unique_accuracy", "canary_comparison"]
        report = run_lab(subset, verbose=False)
        assert len(report.scenarios) == 3


# ---------------------------------------------------------------------------
# LabReport.to_dict()
# ---------------------------------------------------------------------------

class TestLabReportToDict:
    def test_schema_version_present(self) -> None:
        report = run_lab(["latency_ingest"], verbose=False)
        d = report.to_dict()
        assert d["schema_version"] == "1"

    def test_json_serializable(self) -> None:
        report = run_lab(["latency_ingest", "unique_accuracy"], verbose=False)
        text = json.dumps(report.to_dict())
        assert json.loads(text) is not None

    def test_scenarios_list_correct_length(self) -> None:
        report = run_lab(["latency_ingest", "canary_comparison"], verbose=False)
        d = report.to_dict()
        assert len(d["scenarios"]) == 2

    def test_error_field_absent_when_no_error(self) -> None:
        report = run_lab(["latency_ingest"], verbose=False)
        d = report.to_dict()
        assert "error" not in d["scenarios"][0]

    def test_error_field_present_on_failure(self) -> None:
        report = run_lab(["nonexistent_scenario"], verbose=False)
        d = report.to_dict()
        assert "error" in d["scenarios"][0]


# ---------------------------------------------------------------------------
# _render_markdown
# ---------------------------------------------------------------------------

class TestRenderMarkdown:
    def test_contains_h1(self) -> None:
        report = run_lab(["latency_ingest"], verbose=False)
        md = _render_markdown(report)
        assert "# SketchLog Benchmark Lab Report" in md

    def test_contains_scenario_name(self) -> None:
        report = run_lab(["latency_ingest"], verbose=False)
        md = _render_markdown(report)
        assert "latency_ingest" in md

    def test_contains_pass_icon(self) -> None:
        report = run_lab(["latency_ingest"], verbose=False)
        md = _render_markdown(report)
        assert "\u2705" in md

    def test_contains_fail_icon_on_unknown(self) -> None:
        report = run_lab(["nonexistent_scenario"], verbose=False)
        md = _render_markdown(report)
        assert "\u274c" in md

    def test_environment_section(self) -> None:
        report = run_lab(["latency_ingest"], verbose=False)
        md = _render_markdown(report)
        assert "## Environment" in md

    def test_summary_line(self) -> None:
        report = run_lab(["latency_ingest"], verbose=False)
        md = _render_markdown(report)
        assert "passed" in md and "failed" in md


# ---------------------------------------------------------------------------
# CLI smoke tests (via run_lab, no subprocess needed)
# ---------------------------------------------------------------------------

class TestCLIOutput:
    def test_json_output_file(self, tmp_path: "pytest.TempPathFactory") -> None:
        out = tmp_path / "results.json"
        report = run_lab(["latency_ingest"], verbose=False)
        out.write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")
        data = json.loads(out.read_text(encoding="utf-8"))
        assert data["schema_version"] == "1"
        assert len(data["scenarios"]) == 1

    def test_markdown_output_file(self, tmp_path: "pytest.TempPathFactory") -> None:
        out = tmp_path / "report.md"
        report = run_lab(["latency_ingest"], verbose=False)
        out.write_text(_render_markdown(report), encoding="utf-8")
        text = out.read_text(encoding="utf-8")
        assert "SketchLog Benchmark Lab Report" in text

    def test_exit_zero_all_pass(self) -> None:
        report = run_lab([_SCENARIO_ALL], verbose=False)
        assert report.failed == 0
