# Architecture & Concepts

## Accuracy and Guarantees

Tested across 5 distributions (uniform, normal, lognormal, bimodal, and
Zipf-like) from 1K to 1M events. All results: p99 relative error under 1%,
cardinality error ~3%, stable regardless of scale. Asymmetric distributed
merges (99:1 shard skew) stayed at 0.66% error.

See [Guarantees](guarantees.md) for mathematical error bounds of each sketch,
merge algebra proofs (commutative monoid closure, associativity, identity),
bias characterization under edge cases, and windowed correctness properties.

## Distributed Merge

Each StreamLog instance is independent. When you're ready, merge them — the
result is mathematically identical regardless of merge order or shard distribution.

```python
log_a = StreamLog()   # Worker 1
log_b = StreamLog()   # Worker 2

log_a.merge(log_b)    # commutative, associative, deterministic
log_a.p99()           # combined p99 across both shards
```

This makes sketchlog safe for distributed ingestion pipelines. No coordination
protocol needed — each worker maintains its own StreamLog, and merges happen
whenever convenient. Configurations must match across instances (same alpha,
precision, CMS dimensions). If they don't, `merge()` raises a clear `ValueError`.

## Real-time Windows

In production, you usually care about the last 5 minutes, not all of history.
`WindowedStreamLog` handles this with a ring of sub-sketches that automatically
expire. Old data falls off the window; memory stays constant.

```python
from sketchlog import WindowedStreamLog

log = WindowedStreamLog(window="5m")
log.add_latency(42.0)
log.p99()   # p99 of the last 5 minutes only
```

The window is implemented as a ring buffer of independent StreamLog instances.
Each bucket covers `window / n_buckets` of time. When a bucket expires, its
sketch is dropped and a fresh one takes its place. Total memory is bounded by
`n_buckets * sketch_size` regardless of event throughput.

## Drift Detection

`DriftSketch` tracks multiple metric dimensions and detects when they change.
It maintains per-dimension StreamLogs with double-buffered windows — on window
rotation, the current window becomes the frozen previous snapshot and a fresh
window starts. `drift()` compares current vs previous; `correlations()` finds
dimensions that moved together.

```python
from sketchlog.drift import DriftSketch

ds = DriftSketch(window="5m")
ds.add("api_latency", 42.0)
ds.add("redis_latency", 8.0)
ds.add("error_rate", 0.02)

ds.drift()          # what changed vs last window?
ds.correlations()   # what moved together?
```

Example output from a simulated incident:

```
redis_latency    +595.9%   (10.3 -> 71.5)
error_rate       +582.1%   (0.03 -> 0.22)
api_latency      +348.2%   (61.0 -> 273.2)
cache_miss       stable

correlation(error_rate, redis_latency) = 0.99
correlation(api_latency, redis_latency) = 0.74
```

This is statistical co-movement detection — it answers "redis latency increased
by 596%" and "error_rate and redis moved together," but it does not answer
"redis caused the errors." Correlation is not causation. Memory cost is ~14 KB
per tracked dimension.

## C++ Acceleration

When the compiled C++ extension is available, sketchlog runs up to 46x faster
with identical results. The extension is built with pybind11 and accelerates
the hot path — `add_latency`, `add_batch`, `merge`, and `percentile` — while
keeping the Python API unchanged. All quantiles are bit-identical across backends.

| Mode | Throughput | Speedup |
|------|-----------|---------|
| Python scalar | 1.65M events/sec | 1x |
| C++ scalar | 3.17M events/sec | 1.9x |
| C++ batch (numpy) | 75.8M events/sec | 46x |

The C++ extension is optional. Pure Python works everywhere and produces
identical results. The extension accelerates when available:

```python
import sketchlog
print(sketchlog.HAS_CPP)  # True if C++ backend loaded
```

