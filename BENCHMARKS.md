# Reproducible benchmarks

Public performance and accuracy statements are registered in
[`benchmarks/claims.json`](benchmarks/claims.json). Each claim names its input
distribution, backend, reproducing command, and required gate.

Run the same required checks used by CI:

```bash
python benchmarks/suite_throughput.py \
  --output benchmark_throughput.json --items 500000 --iterations 5
python benchmarks/suite_accuracy.py \
  --output benchmark_accuracy.json --items 50000
python benchmarks/check_regressions.py \
  --throughput benchmark_throughput.json \
  --accuracy benchmark_accuracy.json \
  --items 500000
```

The JSON artifacts contain every raw timing sample, mean, median, standard
deviation, p95, Python version, operating system, architecture, processor
description, and timestamp. CI archives these files instead of copying
machine-specific results into documentation.

Memory statements refer to `StreamLog.memory_bytes()`, not process RSS. The
default configuration has a 1,024-bucket limit for each DDSketch sign store and
the server plans for at most 130 KiB per resident stream. Event-count-independent
memory does not mean configuration-independent memory: HLL precision, Count-Min
dimensions, and DDSketch bucket capacity determine the envelope.

Throughput varies with hardware, operating system, compiler, Python version,
input distribution, and scalar versus batch ingestion. Treat archived workflow
artifacts as measurements of their recorded environment, not universal speed
guarantees.
