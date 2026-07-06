"""sketchlog.ci_regression — CI performance regression check engine.

Compares baseline vs candidate benchmark results and fails when configured
thresholds are exceeded.  Used by the sketchlog-regression-check CLI and the
SketchLog GitHub Action (action.yml).

stdlib only — no external dependencies.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import math
import os
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_VERSION = "1.0.0"

_PASS = "PASS"
_FAIL = "FAIL"
_WARN = "WARN"

_DEFAULT_FAIL_P95: float = 20.0
_DEFAULT_FAIL_P99: float = 20.0
_DEFAULT_FAIL_EVENT_RATE: float = 15.0
_DEFAULT_SLO_BURN: float = 2.0


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RegressionConfig:
    """Validated configuration for one regression check run."""

    baseline_file: str = ""
    candidate_file: str = ""
    baseline_ref: str = "main"
    candidate_ref: str = "HEAD"
    fail_p95: float = _DEFAULT_FAIL_P95
    fail_p99: float = _DEFAULT_FAIL_P99
    fail_event_rate: float = _DEFAULT_FAIL_EVENT_RATE
    slo_burn_threshold: float = _DEFAULT_SLO_BURN
    output_file: str = "sketchlog-regression-results.json"
    summary_file: str = "sketchlog-regression-summary.md"

    def __post_init__(self) -> None:
        errors: List[str] = []

        for fname, val in [
            ("fail_p95", self.fail_p95),
            ("fail_p99", self.fail_p99),
            ("fail_event_rate", self.fail_event_rate),
            ("slo_burn_threshold", self.slo_burn_threshold),
        ]:
            if isinstance(val, bool):
                errors.append(f"{fname} must be a float, not bool")
            elif not math.isfinite(float(val)) or float(val) < 0:
                errors.append(f"{fname} must be a finite non-negative float; got {val!r}")

        if errors:
            raise ValueError("RegressionConfig errors: " + "; ".join(errors))


# ---------------------------------------------------------------------------
# Benchmark result schema
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class BenchResult:
    """Parsed benchmark result extracted from a bench-lab JSON file."""

    p95_ms: float = 0.0
    p99_ms: float = 0.0
    event_rate_hz: float = 0.0
    slo_burn_rate: float = 1.0   # 1.0 = burning at exactly the SLO rate
    source_file: str = ""

    def __post_init__(self) -> None:
        for fname, val in [
            ("p95_ms", self.p95_ms),
            ("p99_ms", self.p99_ms),
            ("event_rate_hz", self.event_rate_hz),
            ("slo_burn_rate", self.slo_burn_rate),
        ]:
            if not math.isfinite(float(val)):
                raise ValueError(f"BenchResult.{fname} must be finite; got {val!r}")


# ---------------------------------------------------------------------------
# Regression result
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RegressionResult:
    """Output of one regression check."""

    result: str                          # PASS / FAIL
    p95_regression_pct: float
    p99_regression_pct: float
    event_rate_regression_pct: float
    slo_burn_ratio: float
    checks: List[Dict[str, Any]]
    baseline: BenchResult
    candidate: BenchResult
    config: RegressionConfig

    def to_dict(self) -> Dict[str, Any]:
        return {
            "result": self.result,
            "p95_regression_pct": round(self.p95_regression_pct, 3),
            "p99_regression_pct": round(self.p99_regression_pct, 3),
            "event_rate_regression_pct": round(self.event_rate_regression_pct, 3),
            "slo_burn_ratio": round(self.slo_burn_ratio, 4),
            "checks": self.checks,
            "baseline": {
                "p95_ms": self.baseline.p95_ms,
                "p99_ms": self.baseline.p99_ms,
                "event_rate_hz": self.baseline.event_rate_hz,
                "slo_burn_rate": self.baseline.slo_burn_rate,
                "source_file": self.baseline.source_file,
            },
            "candidate": {
                "p95_ms": self.candidate.p95_ms,
                "p99_ms": self.candidate.p99_ms,
                "event_rate_hz": self.candidate.event_rate_hz,
                "slo_burn_rate": self.candidate.slo_burn_rate,
                "source_file": self.candidate.source_file,
            },
        }


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------

def _pct_arrow(pct: float) -> str:
    """Return a directional indicator for a regression percentage."""
    if pct > 0:
        return f"+{pct:.2f}% ⬆ regression"
    if pct < 0:
        return f"{pct:.2f}% ⬇ improvement"
    return "0.00% (no change)"


def render_markdown(result: RegressionResult) -> str:
    """Render a human-readable Markdown summary for GITHUB_STEP_SUMMARY."""
    icon = "✅" if result.result == _PASS else "❌"
    lines: List[str] = [
        f"## {icon} SketchLog Regression Check — {result.result}",
        "",
        "### Thresholds",
        f"| Metric | Threshold | Measured | Status |",
        f"|---|---|---|---|",
    ]

    for chk in result.checks:
        status_icon = "✅" if chk["status"] == _PASS else ("⚠️" if chk["status"] == _WARN else "❌")
        lines.append(
            f"| {chk['name']} "
            f"| {chk['threshold']} "
            f"| {chk['measured']} "
            f"| {status_icon} {chk['status']} |"
        )

    lines += [
        "",
        "### Raw Numbers",
        f"| Metric | Baseline | Candidate | Δ |",
        f"|---|---|---|---|",
        f"| p95 latency (ms) | {result.baseline.p95_ms:.3f} | {result.candidate.p95_ms:.3f} | {_pct_arrow(result.p95_regression_pct)} |",
        f"| p99 latency (ms) | {result.baseline.p99_ms:.3f} | {result.candidate.p99_ms:.3f} | {_pct_arrow(result.p99_regression_pct)} |",
        f"| Event rate (Hz)  | {result.baseline.event_rate_hz:.1f} | {result.candidate.event_rate_hz:.1f} | {_pct_arrow(result.event_rate_regression_pct)} |",
        f"| SLO burn ratio   | 1.000 (baseline) | {result.slo_burn_ratio:.4f}× | {'⬆ higher burn' if result.slo_burn_ratio > 1 else '⬇ lower burn'} |",
        "",
        "> *Generated by [SketchLog Regression Action](https://github.com/SBALAVIGNESH123/sketchlog)*",
        "",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Benchmark file loading
# ---------------------------------------------------------------------------

def _safe_float(val: Any, default: float = 0.0) -> float:
    """Convert a value to float; return default on failure."""
    try:
        v = float(val)
        return v if math.isfinite(v) else default
    except (TypeError, ValueError):
        return default


def _load_bench_file(path: str) -> BenchResult:
    """Load a bench-lab JSON file and extract regression-relevant metrics."""
    if not path:
        raise ValueError("Benchmark file path is empty")
    try:
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Cannot load benchmark file {path!r}: {exc}") from exc

    if not isinstance(raw, dict):
        raise RuntimeError(f"Benchmark file {path!r} must contain a JSON object at root")

    # Extract from bench-lab format: look inside scenarios[latency_ingest] etc.
    scenarios = raw.get("scenarios", {})
    if not isinstance(scenarios, dict):
        scenarios = {}

    # p95 / p99 from latency_quantile scenario
    lq = scenarios.get("latency_quantile", {})
    if not isinstance(lq, dict):
        lq = {}
    metrics_lq = lq.get("metrics", {})
    if not isinstance(metrics_lq, dict):
        metrics_lq = {}

    p95_ms = _safe_float(metrics_lq.get("p95_rel_err") or raw.get("p95_ms"), 0.0)
    p99_ms = _safe_float(metrics_lq.get("p99_rel_err") or raw.get("p99_ms"), 0.0)

    # Prefer explicit top-level p95_ms / p99_ms if present (from --export-baseline)
    if raw.get("p95_ms") is not None:
        p95_ms = _safe_float(raw["p95_ms"], 0.0)
    if raw.get("p99_ms") is not None:
        p99_ms = _safe_float(raw["p99_ms"], 0.0)

    # Event rate from latency_ingest
    li = scenarios.get("latency_ingest", {})
    if not isinstance(li, dict):
        li = {}
    metrics_li = li.get("metrics", {})
    if not isinstance(metrics_li, dict):
        metrics_li = {}
    event_rate_hz = _safe_float(
        metrics_li.get("events_per_sec") or raw.get("event_rate_hz"), 0.0
    )

    slo_burn_rate = _safe_float(raw.get("slo_burn_rate"), 1.0)

    return BenchResult(
        p95_ms=p95_ms,
        p99_ms=p99_ms,
        event_rate_hz=event_rate_hz,
        slo_burn_rate=slo_burn_rate,
        source_file=path,
    )


def _generate_demo_bench(seed_offset: int = 0) -> BenchResult:
    """Generate a deterministic synthetic BenchResult for demo/test use."""
    # Use a simple LCG so this is stdlib-only and reproducible
    s = (0xDEAD_BEEF + seed_offset) & 0xFFFF_FFFF
    def _next() -> float:
        nonlocal s
        s = (s * 1664525 + 1013904223) & 0xFFFF_FFFF
        return s / 0xFFFF_FFFF

    p99 = 8.0 + _next() * 4.0          # 8–12 ms
    p95 = p99 * (0.6 + _next() * 0.2)  # 60–80 % of p99
    rate = 80_000 + _next() * 40_000   # 80K–120K Hz
    burn = 0.8 + _next() * 0.4         # 0.8–1.2
    return BenchResult(
        p95_ms=round(p95, 3),
        p99_ms=round(p99, 3),
        event_rate_hz=round(rate, 1),
        slo_burn_rate=round(burn, 4),
        source_file="<demo>",
    )


# ---------------------------------------------------------------------------
# Core comparison engine
# ---------------------------------------------------------------------------

def _pct_change(baseline: float, candidate: float) -> float:
    """Return percentage change from baseline to candidate.
    Positive = regression (candidate is worse/higher latency or lower rate).
    For latency: positive means candidate is slower.
    For event_rate: caller should negate before storing.
    """
    if baseline == 0.0:
        return 0.0
    return ((candidate - baseline) / baseline) * 100.0


def compare(
    baseline: BenchResult,
    candidate: BenchResult,
    config: RegressionConfig,
) -> RegressionResult:
    """Compare baseline and candidate; return a RegressionResult."""

    p95_pct = _pct_change(baseline.p95_ms, candidate.p95_ms)
    p99_pct = _pct_change(baseline.p99_ms, candidate.p99_ms)
    # For event rate, a *decrease* is a regression so we negate
    rate_pct = -_pct_change(baseline.event_rate_hz, candidate.event_rate_hz)
    burn_ratio = (
        candidate.slo_burn_rate / baseline.slo_burn_rate
        if baseline.slo_burn_rate > 0
        else 1.0
    )

    checks: List[Dict[str, Any]] = []
    overall = _PASS

    # p95 check
    if config.fail_p95 > 0:
        p95_status = _FAIL if p95_pct > config.fail_p95 else _PASS
        if p95_status == _FAIL:
            overall = _FAIL
        checks.append({
            "name": "p95 latency",
            "threshold": f"+{config.fail_p95:.1f}%",
            "measured": f"+{p95_pct:.2f}%" if p95_pct >= 0 else f"{p95_pct:.2f}%",
            "status": p95_status,
        })

    # p99 check
    if config.fail_p99 > 0:
        p99_status = _FAIL if p99_pct > config.fail_p99 else _PASS
        if p99_status == _FAIL:
            overall = _FAIL
        checks.append({
            "name": "p99 latency",
            "threshold": f"+{config.fail_p99:.1f}%",
            "measured": f"+{p99_pct:.2f}%" if p99_pct >= 0 else f"{p99_pct:.2f}%",
            "status": p99_status,
        })

    # Event-rate check
    if config.fail_event_rate > 0:
        rate_status = _FAIL if rate_pct > config.fail_event_rate else _PASS
        if rate_status == _FAIL:
            overall = _FAIL
        checks.append({
            "name": "event rate",
            "threshold": f"-{config.fail_event_rate:.1f}%",
            "measured": f"+{rate_pct:.2f}% drop" if rate_pct >= 0 else f"{rate_pct:.2f}% improvement",
            "status": rate_status,
        })

    # SLO burn check
    if config.slo_burn_threshold > 0:
        burn_status = _FAIL if burn_ratio > config.slo_burn_threshold else _PASS
        if burn_status == _FAIL:
            overall = _FAIL
        checks.append({
            "name": "SLO burn rate",
            "threshold": f"{config.slo_burn_threshold:.2f}x",
            "measured": f"{burn_ratio:.4f}x",
            "status": burn_status,
        })

    return RegressionResult(
        result=overall,
        p95_regression_pct=p95_pct,
        p99_regression_pct=p99_pct,
        event_rate_regression_pct=rate_pct,
        slo_burn_ratio=burn_ratio,
        checks=checks,
        baseline=baseline,
        candidate=candidate,
        config=config,
    )


# ---------------------------------------------------------------------------
# File writers
# ---------------------------------------------------------------------------

def _write_json(result: RegressionResult, path: str) -> None:
    """Write machine-readable JSON results."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(result.to_dict(), fh, indent=2)


def _write_summary(result: RegressionResult, path: str) -> None:
    """Write Markdown summary."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(render_markdown(result))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="sketchlog-regression-check",
        description=(
            "SketchLog CI regression check — compares baseline vs candidate "
            "benchmark results and fails when thresholds are exceeded."
        ),
    )
    p.add_argument("--baseline-file", default="", metavar="PATH",
                   help="Path to baseline benchmark JSON file")
    p.add_argument("--candidate-file", default="", metavar="PATH",
                   help="Path to candidate benchmark JSON file")
    p.add_argument("--baseline-ref", default="main", metavar="REF",
                   help="Baseline git ref (used for display only when files provided)")
    p.add_argument("--candidate-ref", default="HEAD", metavar="REF",
                   help="Candidate git ref (used for display only when files provided)")
    p.add_argument("--fail-p95", type=float, default=_DEFAULT_FAIL_P95, metavar="PCT",
                   help="Fail threshold for p95 latency regression %% (0=disable)")
    p.add_argument("--fail-p99", type=float, default=_DEFAULT_FAIL_P99, metavar="PCT",
                   help="Fail threshold for p99 latency regression %% (0=disable)")
    p.add_argument("--fail-event-rate", type=float, default=_DEFAULT_FAIL_EVENT_RATE,
                   metavar="PCT",
                   help="Fail threshold for event-rate drop %% (0=disable)")
    p.add_argument("--slo-burn", type=float, default=_DEFAULT_SLO_BURN, metavar="RATIO",
                   help="Fail threshold for SLO burn-rate ratio (0=disable)")
    p.add_argument("--output", default="sketchlog-regression-results.json", metavar="PATH",
                   help="Path for machine-readable JSON output")
    p.add_argument("--summary", default="sketchlog-regression-summary.md", metavar="PATH",
                   help="Path for Markdown summary output")
    p.add_argument("--demo", action="store_true",
                   help="Run with synthetic demo data (no files required)")
    p.add_argument("--export-baseline", action="store_true",
                   help="Export a synthetic baseline file for bootstrapping")
    p.add_argument("--version", action="version", version=f"%(prog)s {_VERSION}")
    return p


def main(argv: Optional[List[str]] = None) -> int:  # noqa: C901
    parser = _build_parser()
    args = parser.parse_args(argv)

    # Read thresholds from environment (GitHub Action sets these)
    def _env_float(env_key: str, fallback: float) -> float:
        v = os.environ.get(env_key, "").strip()
        if v:
            try:
                return float(v)
            except ValueError:
                pass
        return fallback

    fail_p95 = _env_float("SKETCHLOG_FAIL_P95", args.fail_p95)
    fail_p99 = _env_float("SKETCHLOG_FAIL_P99", args.fail_p99)
    fail_event_rate = _env_float("SKETCHLOG_FAIL_EVENT_RATE", args.fail_event_rate)
    slo_burn = _env_float("SKETCHLOG_SLO_BURN", args.slo_burn)

    baseline_file = (os.environ.get("SKETCHLOG_BASELINE_FILE") or args.baseline_file).strip()
    candidate_file = (os.environ.get("SKETCHLOG_CANDIDATE_FILE") or args.candidate_file).strip()
    output_file = (os.environ.get("SKETCHLOG_OUTPUT_FILE") or args.output).strip()
    summary_file = (os.environ.get("SKETCHLOG_SUMMARY_FILE") or args.summary).strip()

    try:
        config = RegressionConfig(
            baseline_file=baseline_file,
            candidate_file=candidate_file,
            baseline_ref=args.baseline_ref,
            candidate_ref=args.candidate_ref,
            fail_p95=fail_p95,
            fail_p99=fail_p99,
            fail_event_rate=fail_event_rate,
            slo_burn_threshold=slo_burn,
            output_file=output_file,
            summary_file=summary_file,
        )
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    # Load data
    try:
        if args.demo:
            baseline = _generate_demo_bench(seed_offset=0)
            candidate = _generate_demo_bench(seed_offset=999)
        elif baseline_file and candidate_file:
            baseline = _load_bench_file(baseline_file)
            candidate = _load_bench_file(candidate_file)
        elif args.export_baseline:
            baseline = _generate_demo_bench(seed_offset=0)
            _write_json(
                RegressionResult(
                    result=_PASS, p95_regression_pct=0.0, p99_regression_pct=0.0,
                    event_rate_regression_pct=0.0, slo_burn_ratio=1.0, checks=[],
                    baseline=baseline, candidate=baseline, config=config,
                ),
                output_file,
            )
            print(f"Baseline exported to {output_file}", file=sys.stderr)
            return 0
        else:
            print(
                "ERROR: Provide --baseline-file and --candidate-file, or use --demo.",
                file=sys.stderr,
            )
            return 2
    except (RuntimeError, OSError) as exc:
        print(f"ERROR loading benchmark files: {exc}", file=sys.stderr)
        return 1

    result = compare(baseline, candidate, config)

    # Write outputs
    try:
        _write_json(result, output_file)
        _write_summary(result, summary_file)
    except OSError as exc:
        print(f"ERROR writing output files: {exc}", file=sys.stderr)
        return 1

    # Print summary to stdout
    print(render_markdown(result))
    print(f"\nResult: {result.result}", file=sys.stderr)
    print(f"JSON:   {output_file}", file=sys.stderr)
    print(f"Summary: {summary_file}", file=sys.stderr)

    return 0 if result.result == _PASS else 1


if __name__ == "__main__":
    sys.exit(main())
