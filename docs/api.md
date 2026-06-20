# API Reference

## StreamLog

The core compression structure.

```python
log = StreamLog(relative_accuracy=0.01, hll_precision=10,
                cms_width=2048, cms_depth=5)

log.add_latency(value)              log.add_batch(values)
log.add_unique("user_123")          log.add_event("endpoint")
log.p50() / p95() / p99() / p999()  log.percentile(0.99)
log.unique_count()                  log.event_count("endpoint")
log.memory_kb()                     log.memory_breakdown()
log.total_events                    log.stats()
log.merge(other_log)                log.reset()
```

## Thread Safety

```python
from sketchlog import ThreadSafeStreamLog
log = ThreadSafeStreamLog()   # safe from any thread, zero config
```

`WindowedStreamLog` and `DriftSketch` are also thread-safe by default.

## Persistence

All core structures can be saved to or loaded from disk, enabling durability and state transfer across processes or restarts.

```python
log.save("metrics.json")              # checkpoint to disk
log = StreamLog.load("metrics.json")  # restore exact state

payload = log.to_json()               # serialize for network transport
restored = StreamLog.from_json(payload)
```

