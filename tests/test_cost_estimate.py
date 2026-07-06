"""
tests/test_cost_estimate.py
===========================
Production-grade tests for sketchlog.cost_estimate.

Run with:
    PYTHONPATH=python python -m pytest tests/test_cost_estimate.py -q
"""

from __future__ import annotations

import json
import math
import sys
from io import StringIO
from typing import Any

import pytest

from sketchlog.cost_estimate import (
    CostEstimateConfig,
    CostEstimateResult,
    _BYTES_PER_SKETCH_BUCKET,
    _COUNTER_STREAM_BYTES_PER_DAY,
    _HOURLY_WINDOWS_PER_DAY,
    _LATENCY_STREAM_FRACTION,
    _SKETCH_FIXED_OVERHEAD_BYTES,
    _fmt_bytes,
    estimate,
    main,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _config(**overrides: Any) -> CostEstimateConfig:
    """Return a valid baseline config, with optional field overrides."""
    defaults: dict[str, Any] = {
        "events_per_day": 1_000_000,
        "avg_event_bytes": 512,
        "retention_days": 30,
        "sketch_accuracy": 0.01,
        "stream_count": 50,
        "namespace_count": 5,
    }
    defaults.update(overrides)
    return CostEstimateConfig(**defaults)


# ---------------------------------------------------------------------------
# CostEstimateConfig validation
# ---------------------------------------------------------------------------

class TestCostEstimateConfigValidation:

    def test_valid_baseline_does_not_raise(self) -> None:
        _config()  # should not raise

    # events_per_day
    @pytest.mark.parametrize("value", [0, -1, -1_000_000])
    def test_events_per_day_nonpositive_raises(self, value: int) -> None:
        with pytest.raises(ValueError, match="events_per_day"):
            _config(events_per_day=value)

    def test_events_per_day_bool_raises(self) -> None:
        with pytest.raises(ValueError, match="events_per_day"):
            _config(events_per_day=True)

    def test_events_per_day_float_raises(self) -> None:
        with pytest.raises(ValueError, match="events_per_day"):
            _config(events_per_day=1.5)  # type: ignore[arg-type]

    # avg_event_bytes
    @pytest.mark.parametrize("value", [0, -1])
    def test_avg_event_bytes_nonpositive_raises(self, value: int) -> None:
        with pytest.raises(ValueError, match="avg_event_bytes"):
            _config(avg_event_bytes=value)

    def test_avg_event_bytes_bool_raises(self) -> None:
        with pytest.raises(ValueError, match="avg_event_bytes"):
            _config(avg_event_bytes=False)

    # retention_days
    @pytest.mark.parametrize("value", [0, -7])
    def test_retention_days_nonpositive_raises(self, value: int) -> None:
        with pytest.raises(ValueError, match="retention_days"):
            _config(retention_days=value)

    # sketch_accuracy
    @pytest.mark.parametrize("value", [0.0, 1.0, -0.5, 1.5, float("inf"), float("nan")])
    def test_sketch_accuracy_out_of_range_raises(self, value: float) -> None:
        with pytest.raises(ValueError, match="sketch_accuracy"):
            _config(sketch_accuracy=value)

    def test_sketch_accuracy_bool_raises(self) -> None:
        with pytest.raises(ValueError, match="sketch_accuracy"):
            _config(sketch_accuracy=True)  # type: ignore[arg-type]

    def test_sketch_accuracy_boundaries_valid(self) -> None:
        # just inside the valid range
        _config(sketch_accuracy=1e-9)
        _config(sketch_accuracy=0.9999)

    # stream_count
    @pytest.mark.parametrize("value", [0, -1])
    def test_stream_count_nonpositive_raises(self, value: int) -> None:
        with pytest.raises(ValueError, match="stream_count"):
            _config(stream_count=value)

    # namespace_count
    @pytest.mark.parametrize("value", [0, -1])
    def test_namespace_count_nonpositive_raises(self, value: int) -> None:
        with pytest.raises(ValueError, match="namespace_count"):
            _config(namespace_count=value)

    def test_multiple_errors_aggregated(self) -> None:
        """All validation errors should surface in one raise, not one at a time."""
        with pytest.raises(ValueError) as exc_info:
            CostEstimateConfig(
                events_per_day=-1,
                avg_event_bytes=-1,
                retention_days=-1,
                sketch_accuracy=2.0,
                stream_count=-1,
                namespace_count=-1,
            )
        msg = str(exc_info.value)
        assert "events_per_day" in msg
        assert "avg_event_bytes" in msg
        assert "retention_days" in msg
        assert "sketch_accuracy" in msg
        assert "stream_count" in msg
        assert "namespace_count" in msg


# ---------------------------------------------------------------------------
# estimate() correctness
# ---------------------------------------------------------------------------

class TestEstimateComputation:

    def test_raw_total_formula(self) -> None:
        cfg = _config(events_per_day=1_000, avg_event_bytes=100, retention_days=10)
        result = estimate(cfg)
        assert result.raw_total_bytes == 1_000 * 100 * 10

    def test_savings_nonnegative(self) -> None:
        """For any valid config, savings should be >= 0."""
        result = estimate(_config())
        assert result.savings_bytes >= 0
        assert result.savings_fraction >= 0.0

    def test_savings_fraction_in_unit_interval(self) -> None:
        result = estimate(_config())
        assert 0.0 <= result.savings_fraction <= 1.0

    def test_savings_bytes_consistent_with_raw_and_sketch(self) -> None:
        result = estimate(_config())
        expected = max(0, result.raw_total_bytes - result.sketch_total_bytes)
        assert result.savings_bytes == expected

    def test_sketch_buckets_match_model(self) -> None:
        """sketch_buckets = ceil(2 / epsilon)."""
        accuracy = 0.01
        cfg = _config(sketch_accuracy=accuracy)
        result = estimate(cfg)
        expected_buckets = math.ceil(2.0 / accuracy)
        assert result.sketch_buckets_per_stream == expected_buckets

    def test_sketch_buckets_minimum_one(self) -> None:
        """Even if epsilon were pathologically large, buckets must be >= 1."""
        cfg = _config(sketch_accuracy=0.9999)
        result = estimate(cfg)
        assert result.sketch_buckets_per_stream >= 1

    def test_latency_stream_count_partition(self) -> None:
        """latency + counter == total stream count."""
        result = estimate(_config(stream_count=50))
        assert result.latency_stream_count + result.counter_stream_count == 50

    def test_latency_stream_count_minimum_one(self) -> None:
        result = estimate(_config(stream_count=1))
        assert result.latency_stream_count >= 1

    def test_sketch_bytes_per_latency_stream_formula(self) -> None:
        accuracy = 0.01
        cfg = _config(sketch_accuracy=accuracy)
        result = estimate(cfg)
        buckets = math.ceil(2.0 / accuracy)
        expected = (
            (buckets * _BYTES_PER_SKETCH_BUCKET + _SKETCH_FIXED_OVERHEAD_BYTES)
            * _HOURLY_WINDOWS_PER_DAY
        )
        assert result.sketch_bytes_per_latency_stream_per_day == expected

    def test_high_accuracy_produces_larger_sketch(self) -> None:
        """Lower epsilon → more buckets → larger sketch."""
        r_coarse = estimate(_config(sketch_accuracy=0.1))
        r_fine = estimate(_config(sketch_accuracy=0.001))
        assert r_fine.sketch_buckets_per_stream > r_coarse.sketch_buckets_per_stream
        assert r_fine.sketch_total_bytes > r_coarse.sketch_total_bytes

    def test_more_streams_bigger_sketch_total(self) -> None:
        r_few = estimate(_config(stream_count=10))
        r_many = estimate(_config(stream_count=100))
        assert r_many.sketch_total_bytes > r_few.sketch_total_bytes

    def test_more_namespaces_bigger_sketch_total(self) -> None:
        r_few = estimate(_config(namespace_count=1))
        r_many = estimate(_config(namespace_count=20))
        assert r_many.sketch_total_bytes > r_few.sketch_total_bytes

    def test_longer_retention_bigger_sketch_total(self) -> None:
        r_short = estimate(_config(retention_days=7))
        r_long = estimate(_config(retention_days=365))
        assert r_long.sketch_total_bytes > r_short.sketch_total_bytes

    def test_caveats_nonempty(self) -> None:
        result = estimate(_config())
        assert len(result.caveats) > 0
        for caveat in result.caveats:
            assert isinstance(caveat, str)
            assert len(caveat) > 0

    def test_result_is_frozen(self) -> None:
        result = estimate(_config())
        with pytest.raises((dataclasses_frozen_error := AttributeError, TypeError)):
            result.savings_bytes = 0  # type: ignore[misc]

    @pytest.mark.parametrize("events_per_day,avg_bytes,retention", [
        (1, 1, 1),
        (1_000_000, 1_024, 365),
        (10_000_000, 2_048, 90),
        (500_000, 256, 7),
    ])
    def test_common_input_ranges_no_exception(
        self, events_per_day: int, avg_bytes: int, retention: int
    ) -> None:
        cfg = _config(
            events_per_day=events_per_day,
            avg_event_bytes=avg_bytes,
            retention_days=retention,
        )
        result = estimate(cfg)
        assert result.raw_total_bytes > 0
        assert result.sketch_total_bytes >= 0

    def test_very_small_accuracy_large_sketch(self) -> None:
        """Tiny epsilon → huge bucket count; sanity-check no overflow."""
        cfg = _config(sketch_accuracy=1e-6)
        result = estimate(cfg)
        assert result.sketch_buckets_per_stream == math.ceil(2.0 / 1e-6)
        assert math.isfinite(result.sketch_total_bytes)


# ---------------------------------------------------------------------------
# to_dict()
# ---------------------------------------------------------------------------

class TestToDictSchema:

    def test_top_level_keys(self) -> None:
        result = estimate(_config())
        d = result.to_dict()
        assert set(d.keys()) == {"inputs", "raw_telemetry", "sketchlog_summary", "savings", "caveats"}

    def test_inputs_keys(self) -> None:
        result = estimate(_config())
        assert set(result.to_dict()["inputs"].keys()) == {
            "events_per_day", "avg_event_bytes", "retention_days",
            "sketch_accuracy", "stream_count", "namespace_count",
        }

    def test_savings_keys(self) -> None:
        d = estimate(_config()).to_dict()["savings"]
        assert "bytes" in d
        assert "human" in d
        assert "percent" in d
        assert "fraction" in d

    def test_json_serialisable(self) -> None:
        result = estimate(_config())
        raw = json.dumps(result.to_dict())
        assert isinstance(raw, str)
        parsed = json.loads(raw)
        assert parsed["savings"]["percent"] >= 0.0

    def test_savings_percent_matches_fraction(self) -> None:
        result = estimate(_config())
        d = result.to_dict()
        assert abs(d["savings"]["fraction"] * 100.0 - d["savings"]["percent"]) < 1e-3

    def test_caveats_is_list_of_strings(self) -> None:
        result = estimate(_config())
        caveats = result.to_dict()["caveats"]
        assert isinstance(caveats, list)
        assert all(isinstance(c, str) for c in caveats)


# ---------------------------------------------------------------------------
# render_text()
# ---------------------------------------------------------------------------

class TestRenderText:

    def test_contains_savings_percent(self) -> None:
        result = estimate(_config())
        text = result.render_text()
        pct = result.savings_percent()
        assert f"{pct:.2f}" in text

    def test_contains_raw_and_sketch_labels(self) -> None:
        text = estimate(_config()).render_text()
        assert "Raw telemetry total" in text
        assert "SketchLog total" in text

    def test_contains_caveats_section(self) -> None:
        text = estimate(_config()).render_text()
        assert "Caveats" in text

    def test_contains_all_input_labels(self) -> None:
        text = estimate(_config()).render_text()
        for label in ("Events per day", "Avg event size", "Retention", "Streams", "Namespaces"):
            assert label in text

    def test_is_string(self) -> None:
        assert isinstance(estimate(_config()).render_text(), str)


# ---------------------------------------------------------------------------
# _fmt_bytes helper
# ---------------------------------------------------------------------------

class TestFmtBytes:

    @pytest.mark.parametrize("n,expected_unit", [
        (0, "B"),
        (512, "B"),
        (1023, "B"),
        (1024, "KiB"),
        (1024 ** 2, "MiB"),
        (1024 ** 3, "GiB"),
        (1024 ** 4, "TiB"),
        (1024 ** 5, "PiB"),
    ])
    def test_unit_thresholds(self, n: int, expected_unit: str) -> None:
        assert expected_unit in _fmt_bytes(n)

    def test_one_mib(self) -> None:
        assert "1.00 MiB" in _fmt_bytes(1024 * 1024)

    def test_one_gib(self) -> None:
        assert "1.00 GiB" in _fmt_bytes(1024 ** 3)


# ---------------------------------------------------------------------------
# CLI (main())
# ---------------------------------------------------------------------------

class TestCLI:

    _VALID_ARGS = [
        "--events-per-day", "1000000",
        "--avg-event-bytes", "512",
        "--retention-days", "30",
        "--sketch-accuracy", "0.01",
        "--streams", "50",
        "--namespaces", "5",
    ]

    def test_valid_args_exit_zero(self, capsys: pytest.CaptureFixture) -> None:
        rc = main(self._VALID_ARGS)
        assert rc == 0

    def test_text_output_printed(self, capsys: pytest.CaptureFixture) -> None:
        main(self._VALID_ARGS)
        out = capsys.readouterr().out
        assert "SketchLog" in out
        assert "Savings" in out

    def test_json_flag_produces_valid_json(self, capsys: pytest.CaptureFixture) -> None:
        main([*self._VALID_ARGS, "--json"])
        out = capsys.readouterr().out
        parsed = json.loads(out)
        assert "savings" in parsed
        assert parsed["savings"]["percent"] >= 0.0

    def test_json_output_no_text_header(self, capsys: pytest.CaptureFixture) -> None:
        main([*self._VALID_ARGS, "--json"])
        out = capsys.readouterr().out
        # Pure JSON — should not start with the banner line
        assert out.strip().startswith("{")

    def test_invalid_accuracy_returns_2(self, capsys: pytest.CaptureFixture) -> None:
        bad = [
            "--events-per-day", "1000000",
            "--avg-event-bytes", "512",
            "--retention-days", "30",
            "--sketch-accuracy", "1.5",  # out of range
            "--streams", "50",
            "--namespaces", "5",
        ]
        rc = main(bad)
        assert rc == 2
        err = capsys.readouterr().err
        assert "error" in err.lower()

    def test_invalid_events_per_day_returns_2(self, capsys: pytest.CaptureFixture) -> None:
        bad = [
            "--events-per-day", "0",
            "--avg-event-bytes", "512",
            "--retention-days", "30",
            "--sketch-accuracy", "0.01",
            "--streams", "50",
            "--namespaces", "5",
        ]
        rc = main(bad)
        assert rc == 2

    def test_help_exits_zero(self) -> None:
        with pytest.raises(SystemExit) as exc_info:
            main(["--help"])
        assert exc_info.value.code == 0

    @pytest.mark.parametrize("events,avg_bytes,retention,accuracy,streams,namespaces", [
        (1, 1, 1, 0.5, 1, 1),
        (10_000_000, 2_048, 365, 0.001, 500, 20),
        (500_000, 64, 90, 0.05, 10, 3),
    ])
    def test_wide_input_ranges_cli(
        self,
        events: int,
        avg_bytes: int,
        retention: int,
        accuracy: float,
        streams: int,
        namespaces: int,
        capsys: pytest.CaptureFixture,
    ) -> None:
        rc = main([
            "--events-per-day", str(events),
            "--avg-event-bytes", str(avg_bytes),
            "--retention-days", str(retention),
            "--sketch-accuracy", str(accuracy),
            "--streams", str(streams),
            "--namespaces", str(namespaces),
        ])
        assert rc == 0
