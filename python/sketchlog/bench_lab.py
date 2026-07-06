"""Reproducible benchmark lab for SketchLog sketch primitives.

CLI: sketchlog-bench-lab [--scenarios all] [--output results.json] [--report report.md]

Scenarios
---------
latency_ingest      DDSketch add_batch() throughput and memory
latency_quantile    DDSketch quantile() latency and relative accuracy
latency_merge       DDSketch shard-merge throughput
unique_ingest       HyperLogLog add() throughput and memory
unique_accuracy     HyperLogLog cardinality error across five magnitudes
freq_ingest         CountMinSketch add() throughput and memory
freq_accuracy       CountMinSketch non-undercount invariant
serialized_size     Sketch bytes vs raw event bytes, compression ratio
canary_comparison   Simulated 2x latency regression detection via p99
"""

from __future__ import annotations

import argparse
import json
import math
import random
import statistics
import sys
import time
import platform
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from sketchlog.core.ddsketch import DDSketch
from sketchlog.core.hll import HyperLogLog
from sketchlog.core.cms import CountMinSketch

# ---------------------------------------------------------------------------
# Constants — all seeded for reproducibility
# ---------------------------------------------------------------------------

_SEED: int = 0xDEADBEEF
_LATENCY_N: int = 100_000
_MERGE_SHARDS: int = 16
_UNIQUE_N: int = 50_000
_FREQ_N: int = 100_000
_FREQ_VOCAB: int = 500
_WARMUP: int = 2
_ITERATIONS: int = 7
_RAW_BYTES_PER_EVENT: int = 256  # representative log-line size
_SCENARIO_ALL: str = "all"

_ALL_SCENARIOS: Tuple[str, ...] = (
    "latency_ingest",
    "latency_quantile",
    "latency_merge",
    "unique_ingest",
    "unique_accuracy",
    "freq_ingest",
    "freq_accuracy",
    "serialized_size",
    "canary_comparison",
)


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class ScenarioResult:
    name: str
    passed: bool
    metrics: Dict[str, Any] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)
    error: Optional[str] = None


@dataclass
class LabReport:
    schema_version: str = "1"
    generated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )
    environment: Dict[str, str] = field(default_factory=dict)
    scenarios: List[ScenarioResult] = field(default_factory=list)
    passed: int = 0
    failed: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "generated_at": self.generated_at,
            "environment": self.environment,
            "passed": self.passed,
            "failed": self.failed,
            "scenarios": [
                {
                    "name": r.name,
                    "passed": r.passed,
                    "metrics": r.metrics,
                    "notes": r.notes,
                    **({"error": r.error} if r.error else {}),
                }
                for r in self.scenarios
            ],
        }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _rng(seed: int = _SEED) -> random.Random:
    return random.Random(seed)


def _lognormal_latencies(n: int, rng: random.Random) -> List[float]:
    """Generate realistic latency samples (mean ~5 ms in log-space)."""
    log5 = math.log(5.0)
    gauss = rng.gauss
    return [math.exp(gauss(log5, 1.0)) for _ in range(n)]


def _measure(
    func: Callable[[], Any],
    warmup: int,
    iterations: int,
) -> Tuple[float, float, float, float]:
    """Return (mean_s, median_s, stddev_s, p95_s) wall-clock seconds."""
    for _ in range(warmup):
        func()
    samples: List[float] = []
    for _ in range(iterations):
        t0 = time.perf_counter()
        func()
        samples.append(time.perf_counter() - t0)
    samples.sort()
    mean = statistics.mean(samples)
    median = statistics.median(samples)
    stddev = statistics.stdev(samples) if len(samples) > 1 else 0.0
    idx95 = max(0, min(len(samples) - 1, int(math.ceil(0.95 * len(samples))) - 1))
    return mean, median, stddev, samples[idx95]


def _throughput(n: int, elapsed_s: float) -> float:
    return n / elapsed_s if elapsed_s > 0.0 else float("inf")


def _ddsketch_compact_bytes(sketch: DDSketch) -> int:
    """Compact JSON serialization size in bytes — proxy for wire size."""
    d = {
        "pos": {str(k): v for k, v in sketch._positive.items()},
        "neg": {str(k): v for k, v in sketch._negative.items()},
        "zero": sketch._zero_count,
        "alpha": sketch._alpha,
    }
    return len(json.dumps(d, separators=(",", ":")))


def _ddsketch_memory_bytes(sketch: DDSketch) -> int:
    """Approximate in-memory footprint: 16 B per bucket + 128 B base overhead."""
    return 128 + (len(sketch._positive) + len(sketch._negative)) * 16


def _env() -> Dict[str, str]:
    return {
        "os": platform.system(),
        "os_release": platform.release(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "processor": platform.processor() or "unknown",
    }


# ---------------------------------------------------------------------------
# Scenario implementations
# ---------------------------------------------------------------------------

def _bench_latency_ingest() -> ScenarioResult:
    rng = _rng()
    values = _lognormal_latencies(_LATENCY_N, rng)

    def run() -> None:
        s = DDSketch(relative_accuracy=0.01)
        s.add_batch(values)

    mean, median, stddev, p95 = _measure(run, _WARMUP, _ITERATIONS)

    # Measure memory after a single committed ingest
    sketch = DDSketch(relative_accuracy=0.01)
    sketch.add_batch(values)
    mem = _ddsketch_memory_bytes(sketch)

    return ScenarioResult(
        name="latency_ingest",
        passed=True,
        metrics={
            "n_events": _LATENCY_N,
            "mean_s": round(mean, 6),
            "median_s": round(median, 6),
            "stddev_s": round(stddev, 6),
            "p95_s": round(p95, 6),
            "throughput_eps": round(_throughput(_LATENCY_N, mean)),
            "memory_bytes": mem,
            "bins_positive": len(sketch._positive),
            "bins_negative": len(sketch._negative),
        },
        notes=[
            f"DDSketch.add_batch({_LATENCY_N:,}) lognormal latencies,"
            " relative_accuracy=0.01"
        ],
    )


def _bench_latency_quantile() -> ScenarioResult:
    rng = _rng()
    values = _lognormal_latencies(_LATENCY_N, rng)
    sketch = DDSketch(relative_accuracy=0.01)
    sketch.add_batch(values)

    qs = [0.5, 0.75, 0.90, 0.95, 0.99, 0.999]

    def run() -> None:
        for q in qs:
            sketch.quantile(q)

    mean, median, _stddev, p95 = _measure(run, _WARMUP, _ITERATIONS)

    sorted_vals = sorted(values)
    n = len(sorted_vals)
    per_q_errors: Dict[str, float] = {}
    for q in qs:
        est = sketch.quantile(q)
        idx = max(0, min(n - 1, int(q * n)))
        exact = sorted_vals[idx]
        rel_err = abs(est - exact) / exact if exact > 0.0 else 0.0
        label = f"p{int(round(q * 1000))}"
        per_q_errors[label] = round(rel_err, 6)

    max_err = max(per_q_errors.values()) if per_q_errors else 0.0
    passed = max_err <= 0.02  # 2× alpha guard

    return ScenarioResult(
        name="latency_quantile",
        passed=passed,
        metrics={
            "query_latency_mean_us": round(mean * 1e6, 3),
            "query_latency_p95_us": round(p95 * 1e6, 3),
            "max_relative_error": round(max_err, 6),
            "per_quantile_errors": per_q_errors,
        },
        notes=[
            f"DDSketch.quantile() accuracy over {_LATENCY_N:,} samples,"
            " relative_accuracy=0.01"
        ],
    )


def _bench_latency_merge() -> ScenarioResult:
    rng = _rng()
    shard_n = _LATENCY_N // _MERGE_SHARDS
    shards: List[DDSketch] = []
    for _ in range(_MERGE_SHARDS):
        s = DDSketch(relative_accuracy=0.01)
        s.add_batch(_lognormal_latencies(shard_n, rng))
        shards.append(s)

    def run() -> None:
        merged = DDSketch(relative_accuracy=0.01)
        for shard in shards:
            for idx, cnt in shard._positive.items():
                merged._positive[idx] = merged._positive.get(idx, 0) + cnt
            for idx, cnt in shard._negative.items():
                merged._negative[idx] = merged._negative.get(idx, 0) + cnt
            merged._zero_count += shard._zero_count
            merged._count += shard._count

    mean, median, _stddev, p95 = _measure(run, _WARMUP, _ITERATIONS)

    return ScenarioResult(
        name="latency_merge",
        passed=True,
        metrics={
            "shards": _MERGE_SHARDS,
            "events_per_shard": shard_n,
            "mean_s": round(mean, 6),
            "median_s": round(median, 6),
            "p95_s": round(p95, 6),
            "merges_per_sec": round(_throughput(_MERGE_SHARDS, mean), 1),
        },
        notes=[f"Merge {_MERGE_SHARDS} DDSketch shards into one"],
    )


def _bench_unique_ingest() -> ScenarioResult:
    items = list(range(_UNIQUE_N))  # deterministic, no RNG needed

    def run() -> None:
        h = HyperLogLog(precision=12)
        for item in items:
            h.add(item)

    mean, median, _stddev, p95 = _measure(run, _WARMUP, _ITERATIONS)

    hll = HyperLogLog(precision=12)
    for item in items:
        hll.add(item)

    return ScenarioResult(
        name="unique_ingest",
        passed=True,
        metrics={
            "n_items": _UNIQUE_N,
            "mean_s": round(mean, 6),
            "median_s": round(median, 6),
            "p95_s": round(p95, 6),
            "throughput_eps": round(_throughput(_UNIQUE_N, mean)),
            "memory_bytes": hll.memory_bytes(),
        },
        notes=[
            f"HyperLogLog.add() over {_UNIQUE_N:,} distinct integers,"
            " precision=12"
        ],
    )


def _bench_unique_accuracy() -> ScenarioResult:
    cardinalities = [100, 1_000, 5_000, 10_000, 50_000]
    errors: List[float] = []
    for true_card in cardinalities:
        hll = HyperLogLog(precision=12)
        for i in range(true_card):
            hll.add(i)
        est = hll.estimate()
        rel_err = abs(est - true_card) / true_card
        errors.append(rel_err)

    max_err = max(errors)
    mean_err = statistics.mean(errors)
    # HLL precision=12 theoretical std error ≈ 0.78 %
    passed = max_err <= 0.05  # 5 % guard

    return ScenarioResult(
        name="unique_accuracy",
        passed=passed,
        metrics={
            "cardinalities_tested": cardinalities,
            "relative_errors": [round(e, 6) for e in errors],
            "max_relative_error": round(max_err, 6),
            "mean_relative_error": round(mean_err, 6),
        },
        notes=["HyperLogLog precision=12 cardinality accuracy (integers 0..N-1)"],
    )


def _bench_freq_ingest() -> ScenarioResult:
    rng = _rng()
    keys = [str(rng.randint(0, _FREQ_VOCAB - 1)) for _ in range(_FREQ_N)]

    def run() -> None:
        c = CountMinSketch(width=2048, depth=5)
        for key in keys:
            c.add(key)

    mean, median, _stddev, p95 = _measure(run, _WARMUP, _ITERATIONS)

    cms = CountMinSketch(width=2048, depth=5)
    for key in keys:
        cms.add(key)

    return ScenarioResult(
        name="freq_ingest",
        passed=True,
        metrics={
            "n_events": _FREQ_N,
            "vocab_size": _FREQ_VOCAB,
            "mean_s": round(mean, 6),
            "median_s": round(median, 6),
            "p95_s": round(p95, 6),
            "throughput_eps": round(_throughput(_FREQ_N, mean)),
            "memory_bytes": cms.memory_bytes(),
        },
        notes=[
            f"CountMinSketch.add() over {_FREQ_N:,} events,"
            f" vocab={_FREQ_VOCAB}, width=2048, depth=5"
        ],
    )


def _bench_freq_accuracy() -> ScenarioResult:
    """Verify the CMS non-undercount invariant: estimate >= true_count always."""
    rng = _rng()
    true_counts: Dict[str, int] = {}
    cms = CountMinSketch(width=2048, depth=5)
    for _ in range(_FREQ_N):
        key = str(rng.randint(0, _FREQ_VOCAB - 1))
        cms.add(key)
        true_counts[key] = true_counts.get(key, 0) + 1

    violations = 0
    overestimates: List[float] = []
    for key, tc in true_counts.items():
        est = cms.estimate(key)
        if est < tc:
            violations += 1
        overestimates.append((est - tc) / tc if tc > 0 else 0.0)

    mean_overestimate = statistics.mean(overestimates) if overestimates else 0.0
    max_overestimate = max(overestimates) if overestimates else 0.0
    passed = violations == 0  # CMS must never undercount

    return ScenarioResult(
        name="freq_accuracy",
        passed=passed,
        metrics={
            "n_distinct_keys": len(true_counts),
            "undercount_violations": violations,
            "mean_overestimate_fraction": round(mean_overestimate, 4),
            "max_overestimate_fraction": round(max_overestimate, 4),
        },
        notes=[
            "CountMinSketch non-undercount invariant:"
            " estimate(key) >= true_count for all keys"
        ],
    )


def _bench_serialized_size() -> ScenarioResult:
    rng = _rng()

    # DDSketch
    dd = DDSketch(relative_accuracy=0.01)
    dd.add_batch(_lognormal_latencies(_LATENCY_N, rng))
    dd_sketch_bytes = _ddsketch_compact_bytes(dd)
    dd_raw_bytes = _LATENCY_N * _RAW_BYTES_PER_EVENT

    # HyperLogLog
    hll = HyperLogLog(precision=12)
    for i in range(_UNIQUE_N):
        hll.add(i)
    hll_sketch_bytes = hll.memory_bytes()
    hll_raw_bytes = _UNIQUE_N * 8  # 64-bit integer per unique item

    # CountMinSketch
    cms = CountMinSketch(width=2048, depth=5)
    for _ in range(_FREQ_N):
        cms.add(str(rng.randint(0, _FREQ_VOCAB - 1)))
    cms_sketch_bytes = cms.memory_bytes()
    cms_raw_bytes = _FREQ_N * 32  # ~32 B per event key

    def _ratio(raw: int, compressed: int) -> float:
        return raw / compressed if compressed > 0 else float("inf")

    return ScenarioResult(
        name="serialized_size",
        passed=True,
        metrics={
            "ddsketch": {
                "n_events": _LATENCY_N,
                "raw_bytes": dd_raw_bytes,
                "sketch_bytes": dd_sketch_bytes,
                "compression_ratio": round(_ratio(dd_raw_bytes, dd_sketch_bytes), 1),
            },
            "hll": {
                "n_items": _UNIQUE_N,
                "raw_bytes": hll_raw_bytes,
                "sketch_bytes": hll_sketch_bytes,
                "compression_ratio": round(_ratio(hll_raw_bytes, hll_sketch_bytes), 1),
            },
            "cms": {
                "n_events": _FREQ_N,
                "raw_bytes": cms_raw_bytes,
                "sketch_bytes": cms_sketch_bytes,
                "compression_ratio": round(_ratio(cms_raw_bytes, cms_sketch_bytes), 1),
            },
        },
        notes=["Serialized sketch bytes vs raw event bytes for all three sketch types"],
    )


def _bench_canary_comparison() -> ScenarioResult:
    """Detect a simulated 2× latency regression via DDSketch p99 comparison."""
    rng_base = _rng(seed=_SEED)
    rng_canary = _rng(seed=_SEED ^ 0xCAFEBABE)

    baseline_vals = _lognormal_latencies(10_000, rng_base)
    canary_vals = [v * 2.0 for v in _lognormal_latencies(10_000, rng_canary)]

    base_sketch = DDSketch(relative_accuracy=0.01)
    base_sketch.add_batch(baseline_vals)
    canary_sketch = DDSketch(relative_accuracy=0.01)
    canary_sketch.add_batch(canary_vals)

    base_p99 = base_sketch.quantile(0.99)
    canary_p99 = canary_sketch.quantile(0.99)
    regression_ratio = canary_p99 / base_p99 if base_p99 > 0.0 else float("inf")
    detected = regression_ratio >= 1.8  # 2× injection → expect ≥ 1.8×

    return ScenarioResult(
        name="canary_comparison",
        passed=detected,
        metrics={
            "baseline_p99_ms": round(base_p99, 3),
            "canary_p99_ms": round(canary_p99, 3),
            "regression_ratio": round(regression_ratio, 3),
            "regression_detected": detected,
        },
        notes=[
            "Simulated 2× latency regression detection via DDSketch p99 comparison"
        ],
    )


# ---------------------------------------------------------------------------
# Scenario registry
# ---------------------------------------------------------------------------

_SCENARIO_MAP: Dict[str, Callable[[], ScenarioResult]] = {
    "latency_ingest": _bench_latency_ingest,
    "latency_quantile": _bench_latency_quantile,
    "latency_merge": _bench_latency_merge,
    "unique_ingest": _bench_unique_ingest,
    "unique_accuracy": _bench_unique_accuracy,
    "freq_ingest": _bench_freq_ingest,
    "freq_accuracy": _bench_freq_accuracy,
    "serialized_size": _bench_serialized_size,
    "canary_comparison": _bench_canary_comparison,
}


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run_lab(
    scenarios: Sequence[str],
    *,
    verbose: bool = True,
) -> LabReport:
    """Run requested scenarios and return a :class:`LabReport`."""
    report = LabReport(environment=_env())
    names: List[str] = (
        list(_ALL_SCENARIOS) if _SCENARIO_ALL in scenarios else list(scenarios)
    )

    for name in names:
        func = _SCENARIO_MAP.get(name)
        if func is None:
            result = ScenarioResult(
                name=name,
                passed=False,
                error=f"Unknown scenario: {name!r}",
            )
        else:
            if verbose:
                print(f"  running {name} ...", flush=True)
            try:
                result = func()
            except Exception as exc:  # noqa: BLE001
                result = ScenarioResult(name=name, passed=False, error=str(exc))

        report.scenarios.append(result)
        if result.passed:
            report.passed += 1
        else:
            report.failed += 1

        if verbose:
            status = "PASS" if result.passed else "FAIL"
            print(f"    [{status}] {name}", flush=True)

    return report


# ---------------------------------------------------------------------------
# Markdown report renderer
# ---------------------------------------------------------------------------

def _render_markdown(report: LabReport) -> str:
    lines: List[str] = [
        "# SketchLog Benchmark Lab Report",
        "",
        f"Generated: {report.generated_at}",
        "",
        "## Environment",
        "",
    ]
    for k, v in report.environment.items():
        lines.append(f"- **{k}**: {v}")
    lines += [
        "",
        f"## Summary: {report.passed} passed / {report.failed} failed",
        "",
        "## Scenarios",
        "",
    ]
    for r in report.scenarios:
        status = "\u2705" if r.passed else "\u274c"
        lines.append(f"### {status} {r.name}")
        lines.append("")
        if r.notes:
            for note in r.notes:
                lines.append(f"> {note}")
            lines.append("")
        if r.error:
            lines.append(f"**Error:** `{r.error}`")
        else:
            lines.append("```json")
            lines.append(json.dumps(r.metrics, indent=2))
            lines.append("```")
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> None:  # pragma: no cover
    parser = argparse.ArgumentParser(
        prog="sketchlog-bench-lab",
        description="Reproducible benchmark lab for SketchLog sketch primitives.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Scenarios: " + ", ".join(_ALL_SCENARIOS),
    )
    parser.add_argument(
        "--scenarios",
        nargs="+",
        default=[_SCENARIO_ALL],
        choices=[_SCENARIO_ALL] + list(_ALL_SCENARIOS),
        metavar="SCENARIO",
        help=(
            "Scenarios to run. Pass 'all' (default) or one or more scenario names."
        ),
    )
    parser.add_argument(
        "--output",
        metavar="FILE",
        help="Write machine-readable JSON results to FILE.",
    )
    parser.add_argument(
        "--report",
        metavar="FILE",
        help="Write a Markdown report to FILE.",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress per-scenario progress output.",
    )
    args = parser.parse_args(argv)

    print("SketchLog Benchmark Lab", flush=True)
    report = run_lab(args.scenarios, verbose=not args.quiet)
    print(
        f"\nResults: {report.passed} passed, {report.failed} failed",
        flush=True,
    )

    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            json.dump(report.to_dict(), fh, indent=2)
        print(f"JSON results written to {args.output}", flush=True)

    if args.report:
        with open(args.report, "w", encoding="utf-8") as fh:
            fh.write(_render_markdown(report))
        print(f"Markdown report written to {args.report}", flush=True)

    if report.failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
