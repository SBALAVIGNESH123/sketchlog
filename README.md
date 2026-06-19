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

## ?? Documentation

The full documentation is available at [SketchLog Documentation Site](https://sbalavignesh123.github.io/sketchlog/) (or via `/docs` in the repository). 
It includes:
- **Architecture**: Details on distributed merges, drift detection, and memory footprint.
- **Guarantees**: Formal mathematical proofs of error bounds.
- **Integrations**: How to integrate with Prometheus, FastAPI, and OpenTelemetry.
- **API Reference**: Comprehensive listing of classes and methods.

---

## Installation

```bash
pip install sketchlog
```

If you have a C++ compiler installed (e.g., GCC, Clang, or MSVC), `pip` will automatically compile the native extension for a 46x speedup. If not, it gracefully falls back to the pure Python implementation.

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

- **Not a tracing system.** No request paths, no correlation IDs, no causal chains.
- **Not a time-series database.** No historical drill-down, no label indexing.
- **Not an observability platform.** No raw log storage, no ad-hoc queries.
- **Not exact.** All results are probabilistic with bounded error. If you need exact percentiles, use numpy.

---

MIT License

