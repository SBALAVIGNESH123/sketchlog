# Benchmark Suite Results

Compares SketchLog, T-Digest, and Apache DataSketches (KLL) across multiple distributions and scales.

## 1M Events

| Distribution | Candidate | p50 Error | p95 Error | p99 Error | Memory (KB) | Throughput (ops/s) |
|--------------|-----------|-----------|-----------|-----------|-------------|--------------------|
| uniform | sketchlog | 0.30% | 0.41% | 0.50% | 520.0 | 2,226,228 |
| uniform | tdigest | 0.01% | 0.00% | 0.00% | 1208.0 | 27,711 |
| uniform | datasketches | 0.00% | 0.08% | 0.42% | 2724.0 | 905,358 |
| normal | sketchlog | 0.16% | 0.26% | 0.91% | 544.0 | 2,363,362 |
| normal | tdigest | 0.00% | 0.00% | 0.00% | 1036.0 | 31,939 |
| normal | datasketches | 0.21% | 0.37% | 0.44% | 2580.0 | 891,136 |
| lognormal | sketchlog | 0.89% | 0.75% | 0.60% | 544.0 | 2,361,030 |
| lognormal | tdigest | 0.00% | 0.01% | 0.01% | 1076.0 | 31,746 |
| lognormal | datasketches | 0.66% | 0.25% | 1.60% | 2668.0 | 885,762 |
| bimodal | sketchlog | 0.83% | 0.03% | 0.01% | 648.0 | 2,427,091 |
| bimodal | tdigest | 10.89% | 0.00% | 0.00% | 1284.0 | 29,106 |
| bimodal | datasketches | 31.67% | 0.21% | 0.26% | 2496.0 | 671,162 |
| zipf | sketchlog | 0.32% | 0.77% | 0.87% | 524.0 | 2,150,264 |
| zipf | tdigest | 23.44% | 0.28% | 1.60% | 412.0 | 92,760 |
| zipf | datasketches | 0.00% | 8.23% | 35.77% | 2548.0 | 4,870,624 |

## 10M Events

| Distribution | Candidate | p50 Error | p95 Error | p99 Error | Memory (KB) | Throughput (ops/s) |
|--------------|-----------|-----------|-----------|-----------|-------------|--------------------|
| uniform | sketchlog | 0.20% | 0.37% | 0.50% | 504.0 | 1,940,635 |
| uniform | tdigest | TIMEOUT | TIMEOUT | TIMEOUT | TIMEOUT | TIMEOUT |
| uniform | datasketches | 0.22% | 0.09% | 0.06% | 2492.0 | 883,384 |
| normal | sketchlog | 0.19% | 0.31% | 0.88% | 504.0 | 2,269,463 |
| normal | tdigest | TIMEOUT | TIMEOUT | TIMEOUT | TIMEOUT | TIMEOUT |
| normal | datasketches | 0.24% | 0.07% | 1.22% | 2556.0 | 933,295 |
| lognormal | sketchlog | 1.00% | 0.51% | 0.42% | 488.0 | 2,322,775 |
| lognormal | tdigest | TIMEOUT | TIMEOUT | TIMEOUT | TIMEOUT | TIMEOUT |
| lognormal | datasketches | 0.39% | 0.86% | 9.46% | 2600.0 | 935,358 |
| bimodal | sketchlog | 0.23% | 0.06% | 0.04% | 480.0 | 2,363,165 |
| bimodal | tdigest | TIMEOUT | TIMEOUT | TIMEOUT | TIMEOUT | TIMEOUT |
| bimodal | datasketches | 18.03% | 0.09% | 0.03% | 2544.0 | 940,815 |
| zipf | sketchlog | 0.32% | 0.52% | 0.02% | 492.0 | 2,324,748 |
| zipf | tdigest | TIMEOUT | TIMEOUT | TIMEOUT | TIMEOUT | TIMEOUT |
| zipf | datasketches | 0.00% | 18.80% | 48.88% | 2088.0 | 3,453,600 |
