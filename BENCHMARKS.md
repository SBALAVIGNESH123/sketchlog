# Benchmark Suite Results

Compares SketchLog, T-Digest, and Apache DataSketches (KLL) across multiple distributions and scales.

## 1M Events

| Distribution | Candidate | p50 Error | p95 Error | p99 Error | Memory (KB) | Throughput (ops/s) |
|--------------|-----------|-----------|-----------|-----------|-------------|--------------------|
| uniform | sketchlog | 0.30% | 0.41% | 0.50% | 488.0 | 2,365,332 |
| uniform | tdigest | 0.01% | 0.00% | 0.00% | 1032.0 | 31,355 |
| uniform | datasketches | 0.38% | 0.17% | 0.38% | 2592.0 | 937,979 |
| normal | sketchlog | 0.16% | 0.26% | 0.91% | 492.0 | 2,379,712 |
| normal | tdigest | 0.00% | 0.00% | 0.00% | 1060.0 | 30,998 |
| normal | datasketches | 0.21% | 1.01% | 2.29% | 2560.0 | 931,413 |
| lognormal | sketchlog | 0.89% | 0.75% | 0.60% | 508.0 | 2,409,509 |
| lognormal | tdigest | 0.00% | 0.00% | 0.01% | 1004.0 | 26,408 |
| lognormal | datasketches | 1.44% | 2.16% | 9.15% | 2624.0 | 1,104,966 |
| bimodal | sketchlog | 0.83% | 0.03% | 0.01% | 488.0 | 2,772,291 |
| bimodal | tdigest | 4.93% | 0.00% | 0.00% | 0.0 | 19,003 |
| bimodal | datasketches | 36.33% | 0.68% | 0.45% | 2672.0 | 847,816 |
| zipf | sketchlog | 0.32% | 0.77% | 0.87% | 648.0 | 2,089,295 |
| zipf | tdigest | 23.44% | 0.25% | 1.60% | 476.0 | 69,238 |
| zipf | datasketches | 0.00% | 11.69% | 80.18% | 2664.0 | 4,764,479 |

## 10M Events

| Distribution | Candidate | p50 Error | p95 Error | p99 Error | Memory (KB) | Throughput (ops/s) |
|--------------|-----------|-----------|-----------|-----------|-------------|--------------------|
| uniform | sketchlog | 0.20% | 0.37% | 0.50% | 544.0 | 1,629,334 |
| uniform | tdigest | 0.00% | 0.00% | 0.00% | 0.0 | 0 |
| uniform | datasketches | 0.20% | 0.25% | 0.15% | 2732.0 | 558,866 |
| normal | sketchlog | 0.19% | 0.31% | 0.88% | 560.0 | 1,948,801 |
| normal | tdigest | 0.00% | 0.00% | 0.00% | 0.0 | 0 |
| normal | datasketches | 0.37% | 0.37% | 1.86% | 2776.0 | 579,119 |
| lognormal | sketchlog | 1.00% | 0.51% | 0.42% | 544.0 | 1,882,397 |
| lognormal | tdigest | 0.00% | 0.00% | 0.00% | 0.0 | 0 |
| lognormal | datasketches | 0.28% | 0.03% | 2.44% | 2712.0 | 820,541 |
| bimodal | sketchlog | 0.23% | 0.06% | 0.04% | 580.0 | 2,140,046 |
| bimodal | tdigest | 0.00% | 0.00% | 0.00% | 0.0 | 0 |
| bimodal | datasketches | 18.56% | 0.28% | 0.58% | 2700.0 | 701,460 |
| zipf | sketchlog | 0.32% | 0.52% | 0.02% | 580.0 | 2,244,614 |
| zipf | tdigest | 0.00% | 0.00% | 0.00% | 0.0 | 0 |
| zipf | datasketches | 0.00% | 3.85% | 3.40% | 2656.0 | 5,607,872 |
