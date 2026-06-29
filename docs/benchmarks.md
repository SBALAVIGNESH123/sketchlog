# Benchmarks

`benchmarks/claims.json` maps every current public benchmark claim to a
reproducing command, backend, distribution, and required threshold.

```bash
python benchmarks/suite_throughput.py \
  --output benchmark_throughput.json --items 500000 --iterations 5
python benchmarks/suite_accuracy.py \
  --output benchmark_accuracy.json --items 50000
python benchmarks/check_regressions.py \
  --throughput benchmark_throughput.json \
  --accuracy benchmark_accuracy.json --items 500000
```

The required workflow fails when p95 scalar throughput drops below its
conservative shared-runner floor, p99 mapping error exceeds 1%, HLL error
exceeds the test tolerance, or skewed-merge memory exceeds 130 KiB. Raw samples,
variance, p95, environment, and timestamp are archived as workflow artifacts.

Throughput measurements are not universal guarantees. Compiler, CPU, Python,
operating system, input distribution, configured dimensions, and scalar versus
batch ingestion all matter.
