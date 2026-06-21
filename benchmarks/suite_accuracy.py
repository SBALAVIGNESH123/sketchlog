import sys
import os
import argparse
import random
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python"))
import sketchlog

from benchmarks.harness import BenchmarkHarness

def main():
    parser = argparse.ArgumentParser(description="SketchLog Accuracy Benchmark Suite")
    parser.add_argument("--output", default="benchmark_accuracy.json", help="JSON output file")
    parser.add_argument("--items", type=int, default=100_000, help="Number of items to test")
    args = parser.parse_args()

    harness = BenchmarkHarness()

    print("Generating distributions...")

    # 1. Realistic Latency (Lognormal)
    np.random.seed(42)
    latency_values = np.random.lognormal(mean=2.0, sigma=1.0, size=args.items)

    log = sketchlog.StreamLog()
    for v in latency_values:
        log.add_latency(float(v))

    p99_actual = np.percentile(latency_values, 99)
    p99_sketch = log.p99()
    err_p99 = abs(p99_sketch - p99_actual) / p99_actual * 100

    harness.add_metric("latency_p99_error_percent", "percent", [err_p99])

    # 2. Adversarial Zipfian (Unique count test)
    zipf_values = np.random.zipf(a=1.5, size=args.items)
    unique_actual = len(set(zipf_values))

    log2 = sketchlog.StreamLog()
    for v in zipf_values:
        log2.add_unique(str(v))

    unique_sketch = log2.stats().unique_count
    err_unique = abs(unique_sketch - unique_actual) / unique_actual * 100 if unique_actual > 0 else 0.0

    harness.add_metric("zipf_unique_count_error_percent", "percent", [err_unique])

    # 3. Merge Skew
    # Merge 1 massive log with 100 tiny logs
    massive = sketchlog.StreamLog()
    tiny_logs = []

    random.seed(42)
    for i in range(10_000):
        massive.add_latency(float(i))

    for _ in range(100):
        t = sketchlog.StreamLog()
        for i in range(10):
            t.add_latency(random.uniform(0, 100))
        tiny_logs.append(t)

    for t in tiny_logs:
        massive.merge(t)

    # The merge should complete successfully and memory should remain bounded
    harness.add_metric("merge_skew_memory_kb", "kb", [massive.stats().memory_kb])

    harness.print_summary()
    harness.save(args.output)
    print(f"\nSaved results to {args.output}")

if __name__ == "__main__":
    main()
