# Benchmark Lab

The SketchLog Benchmark Lab measures the core sketch primitives under
reproducible, deterministic conditions so infrastructure teams can validate
compression, accuracy, and throughput claims on their own hardware.

## Quick start

```bash
# Run all scenarios and write machine-readable results
PYTHONPATH=python python -m sketchlog.bench_lab \
    --output results.json \
    --report report.md

# Run a specific subset
PYTHONPATH=python python -m sketchlog.bench_lab \
    --scenarios latency_ingest latency_quantile canary_comparison \
    --output results.json
```

Or via the installed entry point:

```bash
sketchlog-bench-lab --output results.json --report report.md
```

## CLI reference

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--scenarios` | `SCENARIO …` | `all` | One or more scenario names, or `all` |
| `--output` | `FILE` | — | Write JSON results to FILE |
| `--report` | `FILE` | — | Write Markdown report to FILE |
| `--quiet` | flag | off | Suppress per-scenario progress lines |

Exit code is `0` when all scenarios pass, `1` when any scenario fails.

## Scenarios

| Name | What it measures |
|------|-----------------|
| `latency_ingest` | `DDSketch.add_batch()` throughput and memory over 100 K lognormal samples |
| `latency_quantile` | `DDSketch.quantile()` latency and relative accuracy at p50–p99.9 |
| `latency_merge` | Merge throughput: 16 DDSketch shards combined into one |
| `unique_ingest` | `HyperLogLog.add()` throughput and memory over 50 K distinct integers |
| `unique_accuracy` | HyperLogLog cardinality error across five magnitudes (100 → 50 K) |
| `freq_ingest` | `CountMinSketch.add()` throughput and memory over 100 K events |
| `freq_accuracy` | Non-undercount invariant: `estimate(key) >= true_count` for all keys |
| `serialized_size` | Sketch bytes vs raw event bytes, compression ratio for all three types |
| `canary_comparison` | Detect a simulated 2× latency regression via DDSketch p99 comparison |

All scenarios use a fixed random seed (`0xDEADBEEF`) so results are
reproducible across runs on the same hardware.

## JSON output schema

```json
{
  "schema_version": "1",
  "generated_at": "2026-07-06T10:00:00Z",
  "environment": {
    "os": "Linux",
    "os_release": "5.15.0",
    "machine": "x86_64",
    "python": "3.12.3",
    "processor": "Intel(R) Xeon(R)"
  },
  "passed": 9,
  "failed": 0,
  "scenarios": [
    {
      "name": "latency_ingest",
      "passed": true,
      "metrics": {
        "n_events": 100000,
        "mean_s": 0.043,
        "median_s": 0.042,
        "stddev_s": 0.001,
        "p95_s": 0.045,
        "throughput_eps": 2325000,
        "memory_bytes": 2512,
        "bins_positive": 149,
        "bins_negative": 0
      },
      "notes": ["DDSketch.add_batch(100,000) lognormal latencies, relative_accuracy=0.01"]
    }
  ]
}
```

## Worked example — interpreting latency results

```text
latency_ingest
  throughput_eps : 2,325,000   events/sec ingested by DDSketch.add_batch()
  memory_bytes   : 2,512       bytes for 100 K lognormal events at α=0.01

latency_quantile
  max_relative_error : 0.0038  well within the 1 % α guarantee
  query_latency_mean : 1.2 µs  per full p50/p75/p90/p95/p99/p99.9 sweep

serialized_size (ddsketch)
  raw_bytes        : 25,600,000   100 K × 256 B raw events
  sketch_bytes     : 2,512        compact JSON
  compression_ratio: 10,191×

canary_comparison
  baseline_p99_ms  : 14.3 ms
  canary_p99_ms    : 28.7 ms
  regression_ratio : 2.007        2× regression successfully detected
```

## Accuracy guarantees

| Sketch | Metric | Guarantee |
|--------|--------|-----------|
| DDSketch `α=0.01` | Relative quantile error | ≤ 1 % per quantile |
| HyperLogLog `precision=12` | Cardinality relative error | ≤ 5 % (typical < 1 %) |
| CountMinSketch `w=2048, d=5` | Frequency estimate | ≥ true count (never undercounts) |

## Reproducing locally

```bash
# Clone and install
git clone https://github.com/SBALAVIGNESH123/sketchlog
cd sketchlog
pip install -e ".[test]"

# Run full lab
PYTHONPATH=python python -m sketchlog.bench_lab \
    --output results.json --report report.md

# Run only accuracy scenarios
PYTHONPATH=python python -m sketchlog.bench_lab \
    --scenarios latency_quantile unique_accuracy freq_accuracy

# Run tests
PYTHONPATH=python python -m pytest tests/test_bench_lab.py -q
```

## Caveats

1. **Warm-up** — each scenario runs `_WARMUP=2` discard iterations before
   measuring. Cold-start numbers will differ.
2. **Wall-clock timing** — results include Python interpreter and GC overhead.
   Relative comparisons across runs on the same host are more meaningful than
   absolute cross-machine comparisons.
3. **Synthetic workload** — latency values are lognormal (mean ~5 ms). Real
   workloads with heavy tails, bimodal distributions, or near-zero values may
   produce different bucket counts and memory footprints.
4. **Serialization proxy** — `serialized_size` uses compact JSON as a wire-size
   proxy. A binary codec (e.g. MessagePack) would reduce byte counts further.
5. **Single-threaded** — all scenarios run single-threaded. Concurrent ingest
   throughput is exercised separately in `tests/test_load.py`.
6. **Fixed seed** — reproducibility is guaranteed only for a given Python
   version and platform. Cross-platform RNG output may differ for
   floating-point edge cases.
