# CI Performance Regression Action

The **SketchLog Regression Action** detects performance regressions in pull
requests using sketch-based canary analysis.  It compares baseline vs candidate
benchmark results and fails the job when configured p95/p99 latency, event-rate,
or SLO burn-rate thresholds are exceeded, then posts a Markdown summary directly
to the GitHub step summary panel.

---

## Quick Start

```yaml
- uses: SBALAVIGNESH123/sketchlog@v1
  with:
    baseline-file:  baseline.json
    candidate-file: candidate.json
    fail-on-p99-regression: "20"
```

---

## Minimal End-to-End Workflow

```yaml
name: Performance Regression Check

on: [pull_request]

jobs:
  regression:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Run baseline benchmarks
        run: |
          PYTHONPATH=python python -m sketchlog.bench_lab \
            --output baseline.json

      - name: Run candidate benchmarks
        run: |
          PYTHONPATH=python python -m sketchlog.bench_lab \
            --output candidate.json

      - name: SketchLog regression check
        uses: SBALAVIGNESH123/sketchlog@v1
        with:
          baseline-file:             baseline.json
          candidate-file:            candidate.json
          fail-on-p95-regression:    "15"
          fail-on-p99-regression:    "20"
          fail-on-event-rate-regression: "15"
          slo-burn-threshold:        "2.0"
```

---

## Inputs

| Input | Default | Description |
|---|---|---|
| `baseline-file` | `""` | Path to baseline benchmark JSON |
| `candidate-file` | `""` | Path to candidate benchmark JSON |
| `baseline-ref` | `main` | Baseline git ref (display only) |
| `candidate-ref` | `HEAD` | Candidate git ref (display only) |
| `fail-on-p95-regression` | `20` | Fail if p95 latency rises by more than N % |
| `fail-on-p99-regression` | `20` | Fail if p99 latency rises by more than N % |
| `fail-on-event-rate-regression` | `15` | Fail if event rate drops by more than N % |
| `slo-burn-threshold` | `2.0` | Fail if SLO burn ratio exceeds N× baseline |
| `output-file` | `sketchlog-regression-results.json` | Machine-readable JSON output path |
| `summary-file` | `sketchlog-regression-summary.md` | Markdown summary path |
| `python-path` | `python` | PYTHONPATH prefix for the sketchlog package |

Set any threshold to `"0"` to disable that check entirely.

---

## Outputs

| Output | Description |
|---|---|
| `result` | `PASS` or `FAIL` |
| `p95-regression-pct` | Measured p95 regression percentage |
| `p99-regression-pct` | Measured p99 regression percentage |
| `event-rate-regression-pct` | Measured event-rate regression percentage |
| `slo-burn-ratio` | Candidate / baseline SLO burn-rate ratio |
| `summary-path` | Absolute path to the Markdown summary |
| `output-path` | Absolute path to the JSON results file |

---

## Exit Codes

| Code | Meaning |
|---|---|
| `0` | All checks passed — `PASS` |
| `1` | At least one threshold exceeded — `FAIL`, or a runtime/IO error |
| `2` | Bad configuration (invalid threshold, missing required argument) |

---

## JSON Output Schema

```json
{
  "result": "PASS",
  "p95_regression_pct": 3.21,
  "p99_regression_pct": 8.47,
  "event_rate_regression_pct": -2.10,
  "slo_burn_ratio": 1.03,
  "checks": [
    { "name": "p95 latency",  "threshold": "+20.0%", "measured": "+3.21%",  "status": "PASS" },
    { "name": "p99 latency",  "threshold": "+20.0%", "measured": "+8.47%",  "status": "PASS" },
    { "name": "event rate",   "threshold": "-15.0%", "measured": "-2.10% improvement", "status": "PASS" },
    { "name": "SLO burn rate","threshold": "2.00x",  "measured": "1.0300x","status": "PASS" }
  ],
  "baseline":  { "p95_ms": 5.0, "p99_ms": 10.0, "event_rate_hz": 100000.0, "slo_burn_rate": 1.0 },
  "candidate": { "p95_ms": 5.16, "p99_ms": 10.85, "event_rate_hz": 97900.0, "slo_burn_rate": 1.03 }
}
```

---

## Python API

```python
from sketchlog.ci_regression import BenchResult, RegressionConfig, compare

baseline  = BenchResult(p95_ms=5.0,  p99_ms=10.0, event_rate_hz=100_000.0, slo_burn_rate=1.0)
candidate = BenchResult(p95_ms=5.5,  p99_ms=11.0, event_rate_hz=98_000.0,  slo_burn_rate=1.1)

config = RegressionConfig(fail_p95=20.0, fail_p99=20.0,
                          fail_event_rate=15.0, slo_burn_threshold=2.0)

result = compare(baseline, candidate, config)
print(result.result)            # PASS
print(result.p99_regression_pct)  # 10.0
```

---

## CLI Reference

```bash
# Demo mode — no benchmark files needed
sketchlog-regression-check --demo

# Full run with files
sketchlog-regression-check \
  --baseline-file  baseline.json \
  --candidate-file candidate.json \
  --fail-p99       20 \
  --output         results.json \
  --summary        summary.md

# Export a synthetic baseline for bootstrapping
sketchlog-regression-check --export-baseline --output baseline.json
```

---

## Benchmark File Format

The action accepts JSON files produced by `sketchlog-bench-lab --output` or
written manually.  Required fields at the root level:

| Field | Type | Description |
|---|---|---|
| `p95_ms` | float | p95 latency in milliseconds |
| `p99_ms` | float | p99 latency in milliseconds |
| `event_rate_hz` | float | Ingest throughput in events per second |
| `slo_burn_rate` | float | SLO burn-rate multiplier (1.0 = on track) |

The action also parses bench-lab scenario format automatically.

---

## How It Builds on SketchLog

- Uses the same **DDSketch** accuracy guarantees from `bench_lab.py`
- Integrates with `sketchlog-bench-lab` output directly
- The canary comparison mirrors the `canary_comparison` scenario in the
  benchmark lab (2× p99 regression detection)

---

## Caveats

1. **Benchmark variability** — Wall-clock benchmarks on shared CI runners vary
   by ±5–15 %. Use wider thresholds (20 %+) or pre-generated stable baselines.
2. **Single-run comparison** — Run benchmarks multiple times and average for
   higher confidence.
3. **Synthetic workload** — The bench-lab generates synthetic lognormal data;
   real production latency distributions may differ.
4. **SLO burn rate** — The `slo_burn_rate` field is a proxy; actual SLO burn
   requires production error-budget data.
5. **stdlib only** — The Python engine has no external runtime dependencies.
6. **Action version pinning** — Pin to a specific SHA in production workflows
   for supply-chain safety.
