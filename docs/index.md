# SketchLog

![Status](https://img.shields.io/badge/status-beta-orange)
![Python Version](https://img.shields.io/badge/python-3.9%2B-blue)
![License](https://img.shields.io/badge/license-MIT-green)

**Streaming metrics compression engine. 100M events in 93 KB with bounded error.**

SketchLog allows you to ingest high-throughput event streams and extract accurate
percentiles and cardinalities in constant memory. It combines **DDSketch** for latencies,
**HyperLogLog** for unique items, and **Count-Min Sketch** for event frequencies.

Instead of storing arrays of events or exporting raw telemetry, SketchLog compresses
the statistical shape of your data in real time, making it ideal for continuous
monitoring, edge devices, and memory-constrained environments.

---

## Why SketchLog?

1. **Constant Memory**: Track 10 events or 10 billion events; the data structure stays ~93 KB.
2. **Mergeable**: Shard your data across 100 servers. Send the 93 KB sketches to a central node. Merge them. The result is mathematically identical to processing all events on one machine.
3. **C++ Acceleration**: Pure Python by default, but drops into a zero-copy C++ backend (pybind11) if compiled, reaching up to 75 million events per second.
4. **Drift Detection**: Built-in statistical detection for when metrics meaningfully change over time.

---

## Installation

```bash
pip install sketchlog
```

If you have a C++ compiler installed (e.g., GCC, Clang, or MSVC), `pip` will automatically compile the native extension for a 46x speedup. If not, it gracefully falls back to the pure Python implementation.

---

## Core Metrics Concepts

SketchLog provides three fundamental metric types, mapped to optimized probabilistic data structures:

- **Percentiles (`p50`, `p90`, `p99`, etc.)**: Powered by DDSketch. Perfect for tracking latency distributions. Provides guaranteed relative error bounds regardless of data scale.
- **Frequency (`event_count`)**: Powered by Count-Min Sketch. Ideal for high-throughput discrete events like cache hits/misses, HTTP status codes, or database operations.
- **Cardinality (`unique_count`)**: Powered by HyperLogLog. Used to count distinct items, like unique user IDs, IP addresses, or device identifiers, using minimal memory.

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

