# SketchLog Cost Savings Calculator

The `sketchlog-cost-estimate` command estimates how much storage you save by
retaining SketchLog summaries instead of every raw telemetry event.  All
calculations are **offline** — no server connection is required.

---

## Why this tool exists

SketchLog's core value is compact telemetry.  Raw event streams can consume
hundreds of gigabytes per day at production scale, whereas DDSketch-based
quantile summaries and counter aggregates require only a few kilobytes per
stream per hour.  This tool makes that trade-off concrete and auditable.

---

## Installation

The calculator ships as part of the `sketchlog` Python package.  After
installing the package the CLI is available on your `PATH`:

```bash
pip install sketchlog
sketchlog-cost-estimate --help
```

---

## Usage

```text
sketchlog-cost-estimate \
  --events-per-day     N        \
  --avg-event-bytes    BYTES    \
  --retention-days     DAYS     \
  --sketch-accuracy    EPSILON  \
  --streams            N        \
  --namespaces         N        \
  [--json]
```

### Arguments

| Flag | Type | Required | Description |
|---|---|---|---|
| `--events-per-day` | int | ✅ | Total raw events ingested per day across all streams |
| `--avg-event-bytes` | int | ✅ | Mean byte size of one raw event (e.g. a JSON log line) |
| `--retention-days` | int | ✅ | How many days of data must be retained |
| `--sketch-accuracy` | float | ✅ | Relative error guarantee ε, strictly between 0 and 1 (e.g. `0.01` = 1 %) |
| `--streams` | int | ✅ | Number of SketchLog streams **per namespace** |
| `--namespaces` | int | ✅ | Number of SketchLog namespaces |
| `--json` | flag | ❌ | Emit machine-readable JSON instead of the human-readable report |

---

## Examples

### Small service (human-readable output)

```bash
sketchlog-cost-estimate \
  --events-per-day 500000 \
  --avg-event-bytes 256 \
  --retention-days 30 \
  --sketch-accuracy 0.01 \
  --streams 20 \
  --namespaces 2
```

```text
╔══════════════════════════════════════════════════╗
║      SketchLog Cost Savings Estimate             ║
╚══════════════════════════════════════════════════╝

  Inputs
    Events per day      :         500,000
    Avg event size      :      256.00 B
    Retention           :          30 days
    Sketch accuracy     :            0.01  (relative error)
    Streams (per ns)    :              20
    Namespaces          :               2

  Storage comparison
    Raw telemetry total :        3.58 GiB
    SketchLog total     :       54.87 MiB
    Savings             :        3.52 GiB  (98.50 %)

  Sketch model details
    Latency streams     :              12  (60 % of total)
    Counter streams     :               8  (40 % of total)
    Buckets / stream    :             200
    Bytes / lat. stream :       78.00 KiB  per day
    ...
```

### High-volume service (JSON output, for dashboards or scripts)

```bash
sketchlog-cost-estimate \
  --events-per-day 10000000 \
  --avg-event-bytes 1024 \
  --retention-days 90 \
  --sketch-accuracy 0.005 \
  --streams 200 \
  --namespaces 10 \
  --json
```

```json
{
  "inputs": {
    "events_per_day": 10000000,
    "avg_event_bytes": 1024,
    "retention_days": 90,
    "sketch_accuracy": 0.005,
    "stream_count": 200,
    "namespace_count": 10
  },
  "raw_telemetry": {
    "total_bytes": 921600000000,
    "human": "858.31 GiB"
  },
  "sketchlog_summary": {
    "total_bytes": 16925184000,
    "human": "15.76 GiB",
    "latency_streams": 120,
    "counter_streams": 80,
    "sketch_buckets_per_stream": 400,
    "sketch_bytes_per_latency_stream_per_day": 156672
  },
  "savings": {
    "bytes": 904674816000,
    "human": "842.55 GiB",
    "percent": 98.16,
    "fraction": 0.98163194
  },
  "caveats": ["..."]
}
```

---

## Interpreting the output

| Field | What it means |
|---|---|
| **Raw telemetry total** | `events_per_day × avg_event_bytes × retention_days`. This is the uncompressed baseline. |
| **SketchLog total** | Estimated bytes for all sketches + counter aggregates over the retention window. |
| **Savings** | `raw − sketch`. Expressed in bytes, IEC units, and as a percentage of raw. A negative value means SketchLog uses more storage than raw (only possible at very low event volumes). |
| **Sketch buckets / stream** | Derived from `⌈ 2 / ε ⌉` (DDSketch worst-case bucket count). |
| **Bytes / latency stream** | `(buckets × 16 + 128) × 24` — hourly sketches, 16 B/bucket, 128 B fixed overhead. |

### Stream type split

The model assumes **60 % latency/quantile streams** and **40 % event/counter
streams**.  Counter streams store only running totals (≈ 64 B/stream/day),
which is why they contribute minimally to the SketchLog total.  If your
workload has a very different split, the savings figure will still be in the
right order of magnitude.

`--streams` is a **per-namespace** count.  Total streams =
`streams × namespaces`.

### Sketch accuracy (ε)

ε is the *relative* error guarantee on any quantile query.  With `ε = 0.01`:

- A true p99 of 100 ms will be reported in the range 99–101 ms.
- A true p50 of 10 ms will be reported in the range 9.9–10.1 ms.

Smaller ε → more accurate → more buckets → slightly larger sketches.
Production deployments typically use `0.01` or `0.005`.

---

## Caveats and limitations

1. **Approximation only.**  Actual savings depend on your workload, event
   shape, and field cardinality.
2. **Raw compression not applied.**  The raw figure is *uncompressed*.
   Divide it by your compression ratio (e.g. 3–5× for gzip) for a fairer
   comparison against a compressed raw store.
3. **Memory vs. storage.**  This calculator estimates *storage* (cold-path).
   Hot-path memory savings will differ; sketch hot memory is typically
   proportional to `stream_count × buckets × 8 bytes`.
4. **Bucket model.**  Bucket counts follow `⌈ 2 / ε ⌉` (DDSketch worst-case
   range ratio).  Your sketch implementation may use a different model.
5. **Stream split.**  The 60 / 40 latency / counter split is configurable
   only in the Python API, not the CLI (follow-up work).
6. **No network calls.**  The calculator is fully offline and has no side
   effects.
7. **Negative savings.**  If SketchLog storage exceeds raw storage (very low
   event volume with many streams/namespaces), the savings value will be
   negative.  The tool reports this correctly rather than clamping to zero.

---

## Programmatic API

```python
from sketchlog.cost_estimate import CostEstimateConfig, estimate

config = CostEstimateConfig(
    events_per_day=1_000_000,
    avg_event_bytes=512,
    retention_days=30,
    sketch_accuracy=0.01,
    stream_count=50,     # per namespace
    namespace_count=5,
)
result = estimate(config)

print(result.savings_percent())       # e.g. 99.54
print(result.render_text())           # full human-readable report
print(result.to_dict())               # JSON-serialisable dict
```

`CostEstimateConfig` is a frozen dataclass; all fields are validated in
`__post_init__` and raise `ValueError` with a descriptive message on bad
input.  `CostEstimateResult` is also frozen — fields are safe to cache and
pass across threads.

---

## Running tests

```bash
PYTHONPATH=python python -m pytest tests/test_cost_estimate.py -q
```
