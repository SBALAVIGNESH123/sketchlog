# SketchLog

![Status](https://img.shields.io/badge/status-beta-orange)
![Python Version](https://img.shields.io/badge/python-3.9%2B-blue)
![License](https://img.shields.io/badge/license-MIT-green)

**Bounded-memory streaming metrics with explicit approximation guarantees.**

SketchLog allows you to ingest high-throughput event streams and extract accurate
percentiles and cardinalities in constant memory. It combines **DDSketch** for latencies,
**HyperLogLog** for unique items, and **Count-Min Sketch** for event frequencies.

Instead of storing arrays of events or exporting raw telemetry, SketchLog compresses
the statistical shape of your data in real time, making it ideal for continuous
monitoring, edge devices, and memory-constrained environments.

---

## Why SketchLog?

1. **Bounded Memory**: Event volume does not grow the configured HLL/CMS dimensions, and each DDSketch sign store is capped at 1,024 occupied buckets.
2. **Mergeable**: Compatible sketches can be combined without retaining raw observations.
3. **C++ Acceleration**: Wheels include the native backend on supported 64-bit platforms; the Python backend remains available for deterministic operation.
4. **Drift Detection**: Built-in statistical detection for when metrics meaningfully change over time.

---

## Installation

```bash
pip install sketchlog
```

Supported wheels include the C++ extension. Source installations compile it
with a suitable toolchain. See archived benchmark JSON for measured throughput
on its recorded environment.

---

## Quickstart

```python
from sketchlog import StreamLog

log = StreamLog()

# Ingest data (O(1) time, O(1) memory)
log.add_latency(42.5)
log.add_latency(11.2)
log.add_batch([15.0, 88.2, 42.1, 105.0])

# Track unique users (HyperLogLog)
log.add_unique("user_12345")
log.add_unique("user_99999")

# Track discrete events (Count-Min Sketch)
log.add_event("cache_miss")
log.add_event("db_query", count=5)

# Query instantly
print(f"p99 Latency:  {log.p99():.2f}ms")
print(f"Unique Users: {log.unique_count()}")
print(f"Cache Misses: {log.event_count('cache_miss')}")
```

## What this is not

SketchLog is a streaming metrics compression layer. It is deliberately not:

- **Not a tracing system.** No request paths, no correlation IDs, no causal chains. You cannot debug individual requests.
- **Not a time-series database.** No historical drill-down, no label indexing. You cannot query what happened last Tuesday at 3:42am ? that data is discarded by design.
- **Not an observability platform.** No raw log storage, no ad-hoc queries, no incident replay.
- **Not exact.** All results are probabilistic with bounded error. If you need exact percentiles, use numpy and accept the memory cost.

It sits between your event stream and your dashboards ? approximate answers
good enough for monitoring, alerting, and capacity planning, without the
infrastructure cost of storing every event.
