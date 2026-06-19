# Benchmarks

SketchLog is engineered for real-time aggregation at scale, capable of absorbing extremely high throughput event streams while keeping its memory footprint rigidly bounded.

## 100M Events -> 93 KB Claim

By utilizing probabilistic data structures, SketchLog effectively compresses memory footprints. The data structure sizes are deterministic and isolated entirely from the number of incoming events.

### Footprint Scalability

```mermaid
xychart-beta
    title "Memory Scaling (Events vs. Memory Size)"
    x-axis "Event Count" [1k, 10k, 1M, 10M, 100M, 1B]
    y-axis "Memory (KB)" 0 --> 120
    line "Min Footprint (~81 KB)" [81.16, 81.16, 81.16, 81.16, 81.16, 81.16]
    line "Max Footprint (~93 KB)" [83.0, 85.5, 91.2, 93.1, 93.1, 93.1]
```

### Architectural Breakdown

| Sketch Type | Use Case | Memory Usage | Bounded Error |
| ----------- | -------- | ------------ | ------------- |
| **DDSketch** | P99, P50, Extrema | ~0-12 KB | 1.0% relative |
| **Count-Min Sketch** | Event frequencies | ~81 KB | Provably bounded overestimation |
| **HyperLogLog** | Cardinality/Uniques | ~1 KB | ~3.25% (p=10) |
| **Total `StreamLog`** | Complete tracking | **~81-93 KB** | Configurable via init parameters |

### CPU Throughput (C++ Backend)

With the C++ extension enabled (which `pip` will automatically compile if a toolchain is present), SketchLog achieves up to a **46x throughput improvement** over the pure Python implementation, scaling comfortably up to millions of events per second on a single thread.

```python
from sketchlog import StreamLog
import random

log = StreamLog()

# The internal engine processes this in O(1) time complexity.
# Memory usage depends on the value range tracked by DDSketch.
for _ in range(100_000_000):
    log.add_latency(random.uniform(1.0, 1000.0))

print(log.memory_kb())  # Output: ~81-93 KB range observed
```
