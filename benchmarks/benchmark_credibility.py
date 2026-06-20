"""
Benchmark credibility suite for sketchlog.

1. p99 latency per add() (not just throughput — per-call stability)
2. Batch size scaling curve (how throughput scales with batch size)
3. Per-sketch memory profiling table
4. Multi-node merge under skew + delayed arrivals
5. Python vs C++ correctness parity verification at scale
"""

import sys
import time
import random
import numpy as np

sys.path.insert(0, "python")
import sketchlog

# Try C++ backend
try:
    import _sketchlog_cpp as cpp
    HAS_CPP = True
except ImportError:
    cpp = None
    HAS_CPP = False

print("=" * 70)
print("  SKETCHLOG BENCHMARK CREDIBILITY SUITE")
print(f"  C++ backend: {'available' if HAS_CPP else 'not available'}")
print("=" * 70)
print()

# =========================================================================
# 1. p99 LATENCY PER add() CALL
# =========================================================================

print("1. Per-call latency profiling (p99 latency of add_latency itself)")
print("-" * 70)

N_SAMPLES = 100_000
latencies_ns = []

log = sketchlog.StreamLog()
# Warmup
for i in range(1000):
    log.add_latency(float(i))

# Measure each call
for i in range(N_SAMPLES):
    t0 = time.perf_counter_ns()
    log.add_latency(float(i))
    latencies_ns.append(time.perf_counter_ns() - t0)

latencies_ns.sort()
p50_ns = latencies_ns[int(0.50 * N_SAMPLES)]
p95_ns = latencies_ns[int(0.95 * N_SAMPLES)]
p99_ns = latencies_ns[int(0.99 * N_SAMPLES)]
p999_ns = latencies_ns[int(0.999 * N_SAMPLES)]
avg_ns = sum(latencies_ns) / len(latencies_ns)

print(f"  Python add_latency() over {N_SAMPLES:,} calls:")
print(f"    avg:   {avg_ns:>8.0f} ns ({avg_ns/1000:.1f} us)")
print(f"    p50:   {p50_ns:>8,} ns")
print(f"    p95:   {p95_ns:>8,} ns")
print(f"    p99:   {p99_ns:>8,} ns")
print(f"    p99.9: {p999_ns:>8,} ns")

if HAS_CPP:
    latencies_cpp = []
    log_cpp = cpp.StreamLog()
    for i in range(1000):
        log_cpp.add_latency(float(i))
    for i in range(N_SAMPLES):
        t0 = time.perf_counter_ns()
        log_cpp.add_latency(float(i))
        latencies_cpp.append(time.perf_counter_ns() - t0)

    latencies_cpp.sort()
    p50_cpp = latencies_cpp[int(0.50 * N_SAMPLES)]
    p99_cpp = latencies_cpp[int(0.99 * N_SAMPLES)]
    avg_cpp = sum(latencies_cpp) / len(latencies_cpp)

    print(f"\n  C++ add_latency() over {N_SAMPLES:,} calls:")
    print(f"    avg:   {avg_cpp:>8.0f} ns ({avg_cpp/1000:.1f} us)")
    print(f"    p50:   {p50_cpp:>8,} ns")
    print(f"    p99:   {p99_cpp:>8,} ns")
    print(f"    speedup: {avg_ns/avg_cpp:.1f}x avg, {p99_ns/p99_cpp:.1f}x p99")

print()

# =========================================================================
# 2. BATCH SIZE SCALING CURVE
# =========================================================================

print("2. Batch size scaling curve (throughput vs batch size)")
print("-" * 70)

TOTAL = 1_000_000
random.seed(42)
all_values = [random.lognormvariate(2, 1) for _ in range(TOTAL)]

batch_sizes = [1, 10, 100, 1000, 10000, 100000, 1000000]
print(f"  {'Batch Size':>12}  {'Python ev/s':>14}  ", end="")
if HAS_CPP:
    print(f"{'C++ ev/s':>14}  {'C++ Speedup':>12}")
else:
    print()

for bs in batch_sizes:
    # Python batch
    log_py = sketchlog.StreamLog()
    t0 = time.perf_counter()
    for start in range(0, TOTAL, bs):
        chunk = all_values[start:start+bs]
        log_py.add_batch(chunk)
    py_time = time.perf_counter() - t0
    py_throughput = TOTAL / py_time

    cpp_info = ""
    if HAS_CPP:
        arr = np.array(all_values, dtype=np.float64)
        log_c = cpp.StreamLog()
        t0 = time.perf_counter()
        for start in range(0, TOTAL, bs):
            log_c.add_batch(arr[start:start+bs])
        cpp_time = time.perf_counter() - t0
        cpp_throughput = TOTAL / cpp_time
        speedup = cpp_throughput / py_throughput
        cpp_info = f"  {cpp_throughput:>14,.0f}  {speedup:>11.1f}x"

    print(f"  {bs:>12,}  {py_throughput:>14,.0f}{cpp_info}")

print()

# =========================================================================
# 3. PER-SKETCH MEMORY TABLE
# =========================================================================

print("3. Per-sketch memory profiling (at various event counts)")
print("-" * 70)

event_counts = [0, 1000, 10_000, 100_000, 1_000_000]
print(f"  {'Events':>10}  {'DDSketch':>10}  {'HLL':>8}  {'CMS':>8}  {'Total':>8}  {'Buckets':>8}")
print(f"  {'------':>10}  {'--------':>10}  {'---':>8}  {'---':>8}  {'-----':>8}  {'-------':>8}")

for n in event_counts:
    log = sketchlog.StreamLog()
    random.seed(42)
    for i in range(n):
        log.add_latency(random.lognormvariate(2, 1))
    bd = log.memory_breakdown()
    print(f"  {n:>10,}  {bd['ddsketch_kb']:>9.1f}K  {bd['hyperloglog_kb']:>7.1f}K"
          f"  {bd['countmin_kb']:>7.1f}K  {bd['total_kb']:>7.1f}K  {bd['ddsketch_buckets']:>8}")

print()

# =========================================================================
# 4. MULTI-NODE MERGE UNDER SKEW + DELAYED ARRIVALS
# =========================================================================

print("4. Multi-node merge: skew + delayed arrivals")
print("-" * 70)

random.seed(42)

# Simulate 4 nodes with different distributions and arrival times
node_configs = [
    ("node-0 (uniform)",     lambda: random.uniform(1, 100),          50_000),
    ("node-1 (heavy-tail)",  lambda: random.lognormvariate(4, 2),     50_000),
    ("node-2 (bimodal)",     lambda: random.gauss(50, 5) if random.random() < 0.8 else random.gauss(500, 20), 50_000),
    ("node-3 (sparse/late)", lambda: random.lognormvariate(3, 1),     5_000),   # delayed, fewer events
]

# Build ground truth
all_values = []
node_logs = []

for name, gen, count in node_configs:
    values = [gen() for _ in range(count)]
    all_values.extend(values)
    log = sketchlog.StreamLog()
    log.add_batch(values)
    node_logs.append((name, log, count))
    print(f"  {name:30s}: {count:>8,} events, p99={log.p99():>10.2f}")

# Ground truth
sorted_all = sorted(all_values)
true_p99 = sorted_all[int(0.99 * len(all_values)) - 1]
true_p50 = sorted_all[int(0.50 * len(all_values)) - 1]

# Merge in order
merged = sketchlog.StreamLog()
merged.add_batch(all_values[:0])  # empty init
for name, log, _ in node_logs:
    merged.merge(log)

p99_err = abs(merged.p99() - true_p99) / true_p99 * 100
p50_err = abs(merged.p50() - true_p50) / true_p50 * 100

print(f"\n  Merged 4 nodes ({merged.total_events:,} events):")
print(f"    True p99: {true_p99:>10.2f}  |  Merged p99: {merged.p99():>10.2f}  |  Error: {p99_err:.3f}%")
print(f"    True p50: {true_p50:>10.2f}  |  Merged p50: {merged.p50():>10.2f}  |  Error: {p50_err:.3f}%")

ok_p99 = p99_err < 1.0
ok_p50 = p50_err < 1.0
print(f"    p99 within 1%: {'YES' if ok_p99 else 'NO'}")
print(f"    p50 within 1%: {'YES' if ok_p50 else 'NO'}")

# Reverse merge order (commutativity)
merged_rev = sketchlog.StreamLog()
for name, log, _ in reversed(node_logs):
    merged_rev.merge(log)

print(f"    Reverse merge p99 identical: {merged.p99() == merged_rev.p99()}")

print()

# =========================================================================
# 5. CROSS-LANGUAGE PARITY AT SCALE
# =========================================================================

if HAS_CPP:
    print("5. Cross-language parity (Python vs C++ at 1M events)")
    print("-" * 70)

    random.seed(42)
    values = [random.lognormvariate(2, 1) for _ in range(1_000_000)]
    arr = np.array(values, dtype=np.float64)

    # Python
    log_py = sketchlog.StreamLog()
    log_py.add_batch(values)

    # C++
    log_cpp = cpp.StreamLog()
    log_cpp.add_batch(arr)

    quantiles = [0.50, 0.90, 0.95, 0.99, 0.999]
    print(f"  {'Quantile':>10}  {'Python':>12}  {'C++':>12}  {'Match':>8}")
    print(f"  {'--------':>10}  {'------':>12}  {'---':>12}  {'-----':>8}")

    all_match = True
    for q in quantiles:
        py_val = log_py.percentile(q)
        cpp_val = log_cpp.percentile(q)
        match = abs(py_val - cpp_val) < 0.01  # allow tiny floating point diff
        if not match:
            all_match = False
        label = f"p{int(q*100)}" if q < 0.999 else "p99.9"
        print(f"  {label:>10}  {py_val:>12.4f}  {cpp_val:>12.4f}  {'YES' if match else 'NO':>8}")

    print(f"\n  All quantiles match: {'YES' if all_match else 'NO'}")
    print(f"  Python memory: {log_py.memory_kb():.1f} KB")
    print(f"  C++ memory:    {log_cpp.memory_kb():.1f} KB")
else:
    print("5. Cross-language parity: SKIPPED (C++ not available)")

print()
print("=" * 70)
print("  BENCHMARK SUITE COMPLETE")
print("=" * 70)
