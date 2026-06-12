"""
Stress, adversarial, and correctness tests for sketchlog.

Tests:
  1. Batch vs scalar equivalence
  2. Memory breakdown transparency
  3. Deterministic mode
  4. Merge correctness (multi-way, repeated)
  5. Adversarial inputs (extreme values, duplicates, skewed)
  6. Distribution robustness (uniform, normal, lognormal, bimodal, zipf)
  7. Long-running memory stability
"""

import random
import math
import time
from sketchlog import StreamLog

PASS = 0
FAIL = 0

def check(name, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name} -- {detail}")


# =========================================================================
print("=" * 66)
print("  STRESS & ADVERSARIAL TESTS")
print("=" * 66)
print()

# ── 1. Batch vs scalar equivalence ──────────────────────────────────────

print("1. Batch vs scalar equivalence")
random.seed(42)
values = [random.lognormvariate(2, 1) for _ in range(100_000)]

log_scalar = StreamLog()
for v in values:
    log_scalar.add_latency(v)

log_batch = StreamLog()
log_batch.add_batch(values)

check("event count match",
      log_scalar.total_events == log_batch.total_events,
      f"{log_scalar.total_events} vs {log_batch.total_events}")
check("p99 match",
      abs(log_scalar.p99() - log_batch.p99()) < 0.001,
      f"{log_scalar.p99()} vs {log_batch.p99()}")
check("p50 match",
      abs(log_scalar.p50() - log_batch.p50()) < 0.001,
      f"{log_scalar.p50()} vs {log_batch.p50()}")
check("memory match",
      log_scalar.memory_bytes() == log_batch.memory_bytes())

# Speed comparison
random.seed(99)
bench_values = [random.lognormvariate(2, 1) for _ in range(500_000)]

t0 = time.perf_counter()
log_s = StreamLog()
for v in bench_values:
    log_s.add_latency(v)
scalar_time = time.perf_counter() - t0

t0 = time.perf_counter()
log_b = StreamLog()
log_b.add_batch(bench_values)
batch_time = time.perf_counter() - t0

speedup = scalar_time / batch_time if batch_time > 0 else 1
print(f"  [INFO] Batch speedup: {speedup:.1f}x ({scalar_time:.3f}s vs {batch_time:.3f}s)")
print()

# ── 2. Memory breakdown ─────────────────────────────────────────────────

print("2. Memory breakdown transparency")
log = StreamLog()
random.seed(42)
for _ in range(50_000):
    log.add_latency(random.lognormvariate(2, 1))
for i in range(10_000):
    log.add_unique(str(i))
log.add_event("test", 5000)

bd = log.memory_breakdown()
check("has ddsketch_bytes", 'ddsketch_bytes' in bd)
check("has hyperloglog_bytes", 'hyperloglog_bytes' in bd)
check("has countmin_bytes", 'countmin_bytes' in bd)
check("total matches sum",
      bd['total_bytes'] == bd['ddsketch_bytes'] + bd['hyperloglog_bytes'] + bd['countmin_bytes'],
      f"total={bd['total_bytes']}, sum={bd['ddsketch_bytes'] + bd['hyperloglog_bytes'] + bd['countmin_bytes']}")
check("total matches memory_bytes()",
      bd['total_bytes'] == log.memory_bytes())
check("ddsketch has buckets", bd['ddsketch_buckets'] > 0)
check("hll has registers", bd['hyperloglog_registers'] == 1024)
check("cms has cells", bd['countmin_cells'] == 2048 * 5)

print(f"  [INFO] DDSketch: {bd['ddsketch_kb']} KB ({bd['ddsketch_buckets']} buckets)")
print(f"  [INFO] HLL:      {bd['hyperloglog_kb']} KB ({bd['hyperloglog_registers']} registers)")
print(f"  [INFO] CMS:      {bd['countmin_kb']} KB ({bd['countmin_cells']} cells)")
print(f"  [INFO] Total:    {bd['total_kb']} KB")
print()

# ── 3. Deterministic mode ───────────────────────────────────────────────

print("3. Deterministic mode")
log_a = StreamLog(deterministic=True)
log_b = StreamLog(deterministic=True)
random.seed(42)
vals = [random.lognormvariate(2, 1) for _ in range(10_000)]
for v in vals:
    log_a.add_latency(v)
    log_b.add_latency(v)

check("deterministic p99 identical",
      log_a.p99() == log_b.p99(),
      f"{log_a.p99()} vs {log_b.p99()}")
check("deterministic p50 identical",
      log_a.p50() == log_b.p50())
check("deterministic memory identical",
      log_a.memory_bytes() == log_b.memory_bytes())
print()

# ── 4. Merge correctness ────────────────────────────────────────────────

print("4. Merge correctness")

# 4a: merge equivalence (combined == individual shards merged)
random.seed(42)
all_values = [random.lognormvariate(2, 1) for _ in range(100_000)]
shard_size = 20_000

log_full = StreamLog()
for v in all_values:
    log_full.add_latency(v)

shards = []
for i in range(5):
    s = StreamLog()
    for v in all_values[i*shard_size : (i+1)*shard_size]:
        s.add_latency(v)
    shards.append(s)

log_merged = shards[0]
for s in shards[1:]:
    log_merged.merge(s)

check("5-shard merge: event count",
      log_merged.total_events == log_full.total_events)
check("5-shard merge: p99 matches within 0.1%",
      abs(log_merged.p99() - log_full.p99()) / log_full.p99() < 0.001,
      f"merged={log_merged.p99():.4f}, full={log_full.p99():.4f}")
check("5-shard merge: p50 matches within 0.1%",
      abs(log_merged.p50() - log_full.p50()) / log_full.p50() < 0.001)

# 4b: repeated merge stability (merge same sketch 100 times)
base = StreamLog()
base.add_latency(100.0)
for _ in range(100):
    more = StreamLog()
    more.add_latency(100.0)
    base.merge(more)

check("100x repeated merge: count=101",
      base.total_events == 101,
      f"got {base.total_events}")
check("100x repeated merge: p99 stable",
      abs(base.p99() - 100.0) / 100.0 < 0.02,
      f"p99={base.p99()}")

# 4c: merge config mismatch rejection
try:
    a = StreamLog(relative_accuracy=0.01)
    b = StreamLog(relative_accuracy=0.05)
    a.merge(b)
    check("merge mismatch raises", False, "should have raised ValueError")
except ValueError:
    check("merge mismatch raises ValueError", True)

print()

# ── 5. Adversarial inputs ───────────────────────────────────────────────

print("5. Adversarial inputs")

# 5a: extreme values
log_ext = StreamLog()
log_ext.add_latency(1e-10)
log_ext.add_latency(1e10)
log_ext.add_latency(0.0)
check("extreme values: no crash", True)
check("extreme values: count=3", log_ext.total_events == 3)

# 5b: NaN / Inf rejection
log_nan = StreamLog()
log_nan.add_latency(float('nan'))
log_nan.add_latency(float('inf'))
log_nan.add_latency(float('-inf'))
log_nan.add_latency(42.0)
check("NaN/Inf rejected, valid kept",
      log_nan.total_events == 1,
      f"got {log_nan.total_events}")

# 5c: all-duplicate stream
log_dup = StreamLog()
for _ in range(100_000):
    log_dup.add_latency(42.0)
check("100K duplicates: p99 = 42",
      abs(log_dup.p99() - 42.0) / 42.0 < 0.02,
      f"p99={log_dup.p99()}")

# 5d: single value
log_one = StreamLog()
log_one.add_latency(7.0)
check("single value: p99 = 7.0",
      abs(log_one.p99() - 7.0) < 0.1,
      f"p99={log_one.p99()}")

# 5e: batch with NaN/Inf mixed in
log_mixed = StreamLog()
mixed = [1.0, float('nan'), 2.0, float('inf'), 3.0, float('-inf'), 4.0]
log_mixed.add_batch(mixed)
check("batch NaN/Inf filtering",
      log_mixed.total_events == 4,
      f"got {log_mixed.total_events}")

print()

# ── 6. Distribution robustness ──────────────────────────────────────────

print("6. Distribution robustness (p99 error across distributions)")
N = 200_000
distributions = {
    "uniform":    lambda: random.uniform(0, 1000),
    "normal":     lambda: max(0.001, random.gauss(100, 25)),
    "lognormal":  lambda: random.lognormvariate(2, 1),
    "bimodal":    lambda: random.gauss(50, 5) if random.random() < 0.7 else random.gauss(500, 20),
    "zipf-like":  lambda: 1.0 / (random.random() ** 0.5 + 0.001),
}

for name, gen in distributions.items():
    random.seed(42)
    raw = [gen() for _ in range(N)]
    log = StreamLog()
    log.add_batch(raw)
    
    sorted_raw = sorted(raw)
    exact_p99 = sorted_raw[int(0.99 * N) - 1]
    est_p99 = log.p99()
    
    if exact_p99 != 0:
        error = abs(est_p99 - exact_p99) / abs(exact_p99) * 100
    else:
        error = 0.0
    
    ok = error <= 1.5  # allow slightly more for extreme distributions
    check(f"{name:12s}: error={error:.3f}%", ok,
          f"exact={exact_p99:.2f}, est={est_p99:.2f}")

print()

# ── 7. Long-running memory stability ───────────────────────────────────

print("7. Memory stability (5M events, check growth)")
log_stable = StreamLog()
memory_at = {}
random.seed(42)

for i in range(5_000_000):
    log_stable.add_latency(random.lognormvariate(2, 1))
    if (i + 1) in (100_000, 1_000_000, 2_000_000, 5_000_000):
        memory_at[i + 1] = log_stable.memory_bytes()

for count, mem in sorted(memory_at.items()):
    print(f"  [INFO] {count:>10,} events -> {mem:>8,} bytes ({mem/1024:.1f} KB)")

ratio = memory_at[5_000_000] / memory_at[100_000]
check(f"memory growth ratio (5M/100K) = {ratio:.2f}x",
      ratio < 1.5, f"ratio={ratio:.2f}")

print()

# ── Summary ─────────────────────────────────────────────────────────────

print("=" * 66)
total = PASS + FAIL
print(f"  RESULTS: {PASS}/{total} passed, {FAIL} failed")
if FAIL == 0:
    print("  ALL STRESS TESTS PASS")
else:
    print(f"  {FAIL} TEST(S) FAILED")
print("=" * 66)

import sys
sys.exit(FAIL)
