# SketchLog Cost and Footprint Estimator

The `sketchlog-cost-estimate` command estimates the storage and hot-memory
footprint of a SketchLog deployment before you run it in production. It compares
raw-event retention against compact SketchLog summaries, includes raw-store
compression, and models the operational overhead of memory, PostgreSQL, and
OmniKV storage profiles.

All calculations are offline. The command does not connect to a SketchLog
server, database, registry, or cloud account.

## Why this tool exists

SketchLog's core value is compact telemetry. Raw event streams can consume
hundreds of gigabytes per day at production scale, while DDSketch percentile
summaries and counter aggregates usually require only small bounded summaries
per stream window.

The estimator makes that tradeoff visible before deployment:

- raw uncompressed telemetry size;
- compressed raw-store baseline;
- compact SketchLog summary footprint;
- backend-adjusted footprint for memory, PostgreSQL, or OmniKV;
- estimated hot-path memory;
- ingest-rate planning numbers;
- machine-readable JSON for dashboards or capacity reports.

The same planning model is exposed in the Python API, CLI, and hosted
playground.

Hosted estimator:
[https://sbalavignesh123.github.io/sketchlog/demo/#cost-estimator](https://sbalavignesh123.github.io/sketchlog/demo/#cost-estimator)

## Installation

The estimator ships as part of the `sketchlog` Python package:

```bash
pip install sketchlog
sketchlog-cost-estimate --help
```

## CLI usage

```text
sketchlog-cost-estimate \
  --events-per-day N \
  --avg-event-bytes BYTES \
  --retention-days DAYS \
  --sketch-accuracy EPSILON \
  --streams N \
  --namespaces N \
  [--raw-compression-ratio RATIO] \
  [--storage-backend memory|postgres|omnikv] \
  [--json]
```

## Arguments

| Flag | Type | Required | Description |
| --- | --- | --- | --- |
| `--events-per-day` | int | yes | Total raw telemetry events ingested per day across all streams. |
| `--avg-event-bytes` | int | yes | Mean byte size of one raw event, such as a JSON log line. |
| `--retention-days` | int | yes | Number of days of data retained. |
| `--sketch-accuracy` | float | yes | Relative error guarantee, strictly between 0 and 1. `0.01` means 1 percent. |
| `--streams` | int | yes | Number of SketchLog streams per namespace. |
| `--namespaces` | int | yes | Number of namespaces or tenants. |
| `--raw-compression-ratio` | float | no | Raw-store compression ratio. `1.0` means uncompressed; `4.0` means a 4x compressed raw store. |
| `--storage-backend` | enum | no | Backend profile for SketchLog planning: `memory`, `postgres`, or `omnikv`. |
| `--json` | flag | no | Emit machine-readable JSON instead of the human-readable report. |

## Backend profiles

The backend profile is a planning multiplier applied to the compact SketchLog
summary size.

| Backend | Multiplier | Use it for | Notes |
| --- | ---: | --- | --- |
| `memory` | `1.00x` | demos, tests, short-lived evaluations | Volatile hot-path state. |
| `postgres` | `1.25x` | durable server deployments | Adds headroom for rows, indexes, WAL, and SQL metadata. |
| `omnikv` | `1.15x` | embedded or edge deployments | Adds headroom for key/value metadata and compaction. |

These are conservative planning estimates, not billing guarantees. Validate
important workloads with the storage proof and telemetry load proof commands.

## Example: PostgreSQL-backed service

```bash
sketchlog-cost-estimate \
  --events-per-day 10000000 \
  --avg-event-bytes 1024 \
  --retention-days 90 \
  --sketch-accuracy 0.005 \
  --streams 200 \
  --namespaces 10 \
  --raw-compression-ratio 4 \
  --storage-backend postgres
```

The report includes:

- raw telemetry total;
- compressed raw baseline;
- compact SketchLog total;
- backend-adjusted SketchLog total;
- savings versus compressed raw telemetry;
- ingest rate in events per second;
- hot-memory estimate;
- sketch model details and caveats.

## Example: JSON output

```bash
sketchlog-cost-estimate \
  --events-per-day 1000000 \
  --avg-event-bytes 512 \
  --retention-days 30 \
  --sketch-accuracy 0.01 \
  --streams 50 \
  --namespaces 5 \
  --raw-compression-ratio 4 \
  --storage-backend omnikv \
  --json
```

Important JSON sections:

```json
{
  "inputs": {
    "events_per_day": 1000000,
    "avg_event_bytes": 512,
    "retention_days": 30,
    "sketch_accuracy": 0.01,
    "stream_count": 50,
    "namespace_count": 5,
    "raw_compression_ratio": 4.0,
    "storage_backend": "omnikv"
  },
  "raw_telemetry": {
    "total_bytes": 15360000000,
    "compressed_bytes": 3840000000
  },
  "sketchlog_summary": {
    "total_bytes": 359616000,
    "backend_adjusted_bytes": 413558400,
    "storage_backend": "omnikv",
    "backend_overhead_multiplier": 1.15,
    "total_streams": 250
  },
  "operational_footprint": {
    "events_per_second": 11.5741,
    "hot_memory_bytes": 342400
  }
}
```

## Programmatic API

```python
from sketchlog.cost_estimate import CostEstimateConfig, estimate

config = CostEstimateConfig(
    events_per_day=1_000_000,
    avg_event_bytes=512,
    retention_days=30,
    sketch_accuracy=0.01,
    stream_count=50,
    namespace_count=5,
    raw_compression_ratio=4.0,
    storage_backend="omnikv",
)

result = estimate(config)

print(result.savings_percent())
print(result.backend_adjusted_sketch_total_bytes)
print(result.hot_memory_bytes)
print(result.render_text())
print(result.to_dict())
```

`CostEstimateConfig` and `CostEstimateResult` are frozen dataclasses. Inputs are
validated in `__post_init__`; invalid values raise `ValueError` with a
descriptive message.

## Model details

### Raw telemetry

```text
raw_total = events_per_day * avg_event_bytes * retention_days
compressed_raw_total = round_half_up(raw_total / raw_compression_ratio)
```

`round_half_up` is an explicit positive-half rounding policy shared by the
Python estimator and browser playground, so `.5` byte totals are rounded up in
both runtimes.

### DDSketch latency streams

The estimator uses the same planning model as the browser playground:

```text
sketch_buckets = ceil(2 / epsilon)
bytes_per_latency_stream_per_day =
  (sketch_buckets * 16 + 128) * 24
```

The constants represent:

- 16 bytes per DDSketch bucket;
- 128 bytes fixed metadata per sketch window;
- 24 hourly windows per stream per day.

### Counter streams

The model assumes 60 percent latency/quantile streams and 40 percent
event/counter streams. Counter streams are modeled as 64 bytes per stream per
day for running totals and metadata.

### Backend-adjusted SketchLog footprint

```text
backend_adjusted = round_half_up(compact_sketch_total * backend_multiplier)
```

This is the number to use when comparing SketchLog against a compressed raw
store for high-level planning.

### Hot-memory footprint

Hot memory is modeled separately from persisted storage:

```text
hot_memory =
  namespaces * (
    latency_streams * (sketch_buckets * 8 + 512)
    + counter_streams * 256
  )
```

This estimates active in-process sketch state. It is not a replacement for
load testing, but it gives operators a reasonable first sizing signal.

## Caveats

1. The estimator is for planning, not billing.
2. Real compression depends on event shape, labels, and payload repetition.
3. The 60/40 latency/counter stream split is a default model, not a universal
   truth.
4. Backend multipliers are conservative headroom values.
5. Hot memory and persisted storage answer different capacity questions.
6. Validate serious deployments with `scripts/storage_proof.py` and
   `scripts/telemetry_load_proof.py`.

## Validation

Run the estimator tests:

```bash
PYTHONPATH=python python -m pytest tests/test_cost_estimate.py -q
```

Run the hosted playground tests:

```bash
PYTHONPATH=python python -m pytest tests/test_demo.py -q
```
