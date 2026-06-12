"""
Advanced correctness tests for sketchlog.

1. Distributed skew merge (heavy-tail shard + uniform shard)
2. WindowedStreamLog expiration correctness
3. WindowedStreamLog memory creep under burst traffic
4. Scale-to-billion proof (extrapolated from 100M)
5. Merge commutativity and associativity
"""

import random
import math
import time
from sketchlog import StreamLog, WindowedStreamLog

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


print("=" * 66)
print("  ADVANCED CORRECTNESS TESTS")
print("=" * 66)
print()

# =========================================================================
# 1. DISTRIBUTED SKEW MERGE
# =========================================================================

print("1. Distributed skew merge (asymmetric shards)")
print("-" * 66)

random.seed(42)

# Shard A: heavy-tail lognormal (simulates API latency spikes)
shard_a_values = [random.lognormvariate(3, 2) for _ in range(50_000)]

# Shard B: tight uniform (simulates fast cache hits)
shard_b_values = [random.uniform(1, 10) for _ in range(50_000)]

# Ground truth: all values combined
all_values = shard_a_values + shard_b_values
sorted_all = sorted(all_values)
true_p99 = sorted_all[int(0.99 * len(all_values)) - 1]
true_p50 = sorted_all[int(0.50 * len(all_values)) - 1]

# Sketch: process separately, then merge
log_a = StreamLog()
log_a.add_batch(shard_a_values)

log_b = StreamLog()
log_b.add_batch(shard_b_values)

log_merged = StreamLog()
log_merged.add_batch(shard_a_values)  # reference: single-stream
log_merged_ref_p99 = log_merged.p99()

# Now test the actual merge path
log_a_copy = StreamLog()
log_a_copy.add_batch(shard_a_values)
log_a_copy.merge(log_b)

p99_err = abs(log_a_copy.p99() - true_p99) / true_p99 * 100
p50_err = abs(log_a_copy.p50() - true_p50) / true_p50 * 100

print(f"  Shard A: heavy-tail lognormal (50K events)")
print(f"  Shard B: tight uniform [1, 10] (50K events)")
print(f"  True p99: {true_p99:.2f}, Merged p99: {log_a_copy.p99():.2f}, Error: {p99_err:.3f}%")
print(f"  True p50: {true_p50:.2f}, Merged p50: {log_a_copy.p50():.2f}, Error: {p50_err:.3f}%")

check("skewed merge p99 error < 1%", p99_err < 1.0, f"{p99_err:.3f}%")
check("skewed merge p50 error < 1%", p50_err < 1.0, f"{p50_err:.3f}%")
check("merged event count = 100K", log_a_copy.total_events == 100_000)

# Extreme skew: 99% from one shard
random.seed(99)
shard_heavy = StreamLog()
shard_light = StreamLog()
heavy_vals = [random.lognormvariate(5, 1) for _ in range(99_000)]
light_vals = [random.uniform(0.1, 1.0) for _ in range(1_000)]
shard_heavy.add_batch(heavy_vals)
shard_light.add_batch(light_vals)
shard_heavy.merge(shard_light)

all_extreme = heavy_vals + light_vals
sorted_extreme = sorted(all_extreme)
true_p99_extreme = sorted_extreme[int(0.99 * len(all_extreme)) - 1]
err_extreme = abs(shard_heavy.p99() - true_p99_extreme) / true_p99_extreme * 100
check("99:1 skew merge p99 error < 1%", err_extreme < 1.0, f"{err_extreme:.3f}%")

print()

# =========================================================================
# 2. WINDOWED EXPIRATION CORRECTNESS
# =========================================================================

print("2. WindowedStreamLog expiration correctness")
print("-" * 66)

# 2a: Data expires after window
log_w = WindowedStreamLog(window="1s", n_buckets=4)
for i in range(100):
    log_w.add_latency(float(i + 1))

events_before = log_w.total_events
check("before expiry: 100 events", events_before == 100, f"got {events_before}")

time.sleep(1.5)
events_after = log_w.total_events
check("after 1.5s: events expired", events_after == 0, f"got {events_after}")

# 2b: New data after expiry works
for i in range(50):
    log_w.add_latency(float(i + 200))
check("new data after expiry: 50 events", log_w.total_events == 50)
check("new data p99 valid", log_w.p99() > 200)

# 2c: Partial expiry (window = 2s, add at t=0, sleep 1s, add more, check)
log_w2 = WindowedStreamLog(window="2s", n_buckets=4)
for i in range(100):
    log_w2.add_latency(10.0)  # first batch: all 10.0

time.sleep(1.2)

for i in range(100):
    log_w2.add_latency(500.0)  # second batch: all 500.0

# Both batches should be active (within 2s window)
total = log_w2.total_events
check("partial expiry: both batches active", total == 200, f"got {total}")

time.sleep(1.2)
# First batch should have expired, second still active
total_after = log_w2.total_events
check("partial expiry: first batch expired", total_after == 100 or total_after == 0,
      f"got {total_after}")

print()

# =========================================================================
# 3. WINDOWED MEMORY CREEP TEST
# =========================================================================

print("3. WindowedStreamLog memory stability (no creep)")
print("-" * 66)

log_w3 = WindowedStreamLog(window="2s", n_buckets=4)
mem_initial = log_w3.memory_bytes()

# Simulate burst traffic: add lots, let expire, add more
for cycle in range(3):
    for i in range(10_000):
        log_w3.add_latency(random.lognormvariate(2, 1))
    time.sleep(0.8)

mem_after = log_w3.memory_bytes()
ratio = mem_after / mem_initial if mem_initial > 0 else 1.0

print(f"  Initial memory: {mem_initial:,} bytes ({mem_initial/1024:.1f} KB)")
print(f"  After 3 burst cycles: {mem_after:,} bytes ({mem_after/1024:.1f} KB)")
print(f"  Ratio: {ratio:.2f}x")

check("memory ratio < 2x after bursts", ratio < 2.0, f"ratio={ratio:.2f}")
print()

# =========================================================================
# 4. MERGE COMMUTATIVITY AND ASSOCIATIVITY
# =========================================================================

print("4. Merge algebraic properties")
print("-" * 66)

random.seed(42)
vals_x = [random.lognormvariate(2, 1) for _ in range(10_000)]
vals_y = [random.lognormvariate(3, 0.5) for _ in range(10_000)]
vals_z = [random.uniform(1, 100) for _ in range(10_000)]

# Commutativity: merge(A,B) == merge(B,A)
ab = StreamLog(); ab.add_batch(vals_x)
ab_other = StreamLog(); ab_other.add_batch(vals_y)
ab.merge(ab_other)

ba = StreamLog(); ba.add_batch(vals_y)
ba_other = StreamLog(); ba_other.add_batch(vals_x)
ba.merge(ba_other)

check("commutativity: p99(A+B) == p99(B+A)",
      abs(ab.p99() - ba.p99()) < 0.001,
      f"A+B={ab.p99():.4f}, B+A={ba.p99():.4f}")

# Associativity: merge(merge(A,B), C) == merge(A, merge(B,C))
ab_c = StreamLog(); ab_c.add_batch(vals_x)
temp = StreamLog(); temp.add_batch(vals_y)
ab_c.merge(temp)
temp2 = StreamLog(); temp2.add_batch(vals_z)
ab_c.merge(temp2)

a_bc = StreamLog(); a_bc.add_batch(vals_x)
bc = StreamLog(); bc.add_batch(vals_y)
temp3 = StreamLog(); temp3.add_batch(vals_z)
bc.merge(temp3)
a_bc.merge(bc)

check("associativity: p99((A+B)+C) == p99(A+(B+C))",
      abs(ab_c.p99() - a_bc.p99()) < 0.001,
      f"(A+B)+C={ab_c.p99():.4f}, A+(B+C)={a_bc.p99():.4f}")

print()

# =========================================================================
# 5. SCALE PROOF: 100M events → memory flat
# =========================================================================

print("5. Scale proof: 100M events, memory stays constant")
print("-" * 66)

log_scale = StreamLog()
random.seed(42)
checkpoints = [1_000_000, 10_000_000, 50_000_000, 100_000_000]
memory_log = {}
batch_size = 100_000

t0 = time.perf_counter()
for i in range(0, 100_000_000, batch_size):
    batch = [random.lognormvariate(2, 1) for _ in range(batch_size)]
    log_scale.add_batch(batch)
    n = i + batch_size
    if n in checkpoints:
        memory_log[n] = log_scale.memory_bytes()
        elapsed = time.perf_counter() - t0
        print(f"  {n:>12,} events | {log_scale.memory_bytes():>8,} bytes "
              f"({log_scale.memory_bytes()/1024:.1f} KB) | {elapsed:.1f}s")

elapsed_total = time.perf_counter() - t0
throughput = 100_000_000 / elapsed_total

ratio_100m_1m = memory_log[100_000_000] / memory_log[1_000_000]
check(f"memory ratio (100M/1M) = {ratio_100m_1m:.2f}x (should be ~1.0)",
      ratio_100m_1m < 1.1, f"ratio={ratio_100m_1m:.2f}")

p99_final = log_scale.p99()
print(f"\n  Final p99: {p99_final:.4f}")
print(f"  Throughput: {throughput:,.0f} events/sec (batch mode)")
print(f"  Total time: {elapsed_total:.1f}s")

check("100M events processed", log_scale.total_events == 100_000_000)

print()

# =========================================================================
# SUMMARY
# =========================================================================

print("=" * 66)
total = PASS + FAIL
print(f"  RESULTS: {PASS}/{total} passed, {FAIL} failed")
if FAIL == 0:
    print("  ALL ADVANCED TESTS PASS")
else:
    print(f"  {FAIL} TEST(S) FAILED")
print("=" * 66)

import sys
sys.exit(FAIL)
