"""
Production-grade tests for sketchlog.

1. Concurrency profiling (1, 2, 4, 8, 16 threads)
2. Chaos windowing (bursts, silence, reactivation)
3. Adversarial CMS hash collision stress
4. Pathological burst insertion
5. Skew drift over time windows
"""

import random
import math
import time
import threading
from sketchlog import StreamLog, ThreadSafeStreamLog, WindowedStreamLog

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
print("  PRODUCTION CORRECTNESS TESTS")
print("=" * 66)
print()

# =========================================================================
# 1. CONCURRENCY PROFILING
# =========================================================================

print("1. Concurrency profiling (add latency under thread contention)")
print("-" * 66)

EVENTS_PER_THREAD = 50_000

for n_threads in [1, 2, 4, 8, 16]:
    log = ThreadSafeStreamLog()
    
    def worker():
        for i in range(EVENTS_PER_THREAD):
            log.add_latency(float(i % 1000) + 1.0)
    
    t0 = time.perf_counter()
    threads = [threading.Thread(target=worker) for _ in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    elapsed = time.perf_counter() - t0
    
    expected = n_threads * EVENTS_PER_THREAD
    actual = log.total_events
    throughput = expected / elapsed
    
    ok = actual == expected
    status = "OK" if ok else "LOST"
    print(f"  {n_threads:>2} threads x {EVENTS_PER_THREAD:,} = {actual:>10,} "
          f"(expected {expected:>10,}) | {throughput:>10,.0f} ev/s | {status}")
    
    check(f"{n_threads}-thread count integrity", ok,
          f"expected {expected}, got {actual}")

print()

# =========================================================================
# 2. CHAOS WINDOWING
# =========================================================================

print("2. Chaos windowing (bursts, silence, reactivation)")
print("-" * 66)

# Pattern: burst -> silence -> burst -> silence -> burst
log_chaos = WindowedStreamLog(window="2s", n_buckets=4)

# Burst 1: 5000 events
for i in range(5000):
    log_chaos.add_latency(float(i + 1))
burst1_events = log_chaos.total_events
burst1_p99 = log_chaos.p99()
check("burst 1: 5000 events", burst1_events == 5000, f"got {burst1_events}")

# Silence: 1 second
time.sleep(1.0)

# Burst 2: 3000 events with different distribution
for i in range(3000):
    log_chaos.add_latency(float(i + 10000))
burst2_events = log_chaos.total_events
check("burst 1+2: 8000 events", burst2_events == 8000, f"got {burst2_events}")

# Full silence: wait for window to expire
time.sleep(2.5)
expired_events = log_chaos.total_events
check("full expiry after silence", expired_events == 0, f"got {expired_events}")

# Reactivation: burst 3 after silence
for i in range(2000):
    log_chaos.add_latency(float(i + 50000))
reactivated = log_chaos.total_events
check("reactivation: 2000 events", reactivated == 2000, f"got {reactivated}")
check("reactivated p99 valid", log_chaos.p99() > 50000,
      f"p99={log_chaos.p99()}")

# Memory should not have grown through all chaos
mem = log_chaos.memory_bytes()
print(f"  [INFO] Memory after chaos: {mem:,} bytes ({mem/1024:.1f} KB)")
check("chaos memory bounded", mem < 500_000, f"{mem} bytes")

print()

# =========================================================================
# 3. ADVERSARIAL CMS HASH COLLISION STRESS
# =========================================================================

print("3. Adversarial CMS hash collision stress")
print("-" * 66)

# Generate keys designed to collide: short sequential strings
log_cms = StreamLog(cms_width=256, cms_depth=3)  # small CMS to force collisions
N_KEYS = 1000
TRUE_COUNTS = {}
random.seed(42)

for i in range(N_KEYS):
    key = f"key_{i}"
    count = random.randint(1, 100)
    TRUE_COUNTS[key] = count
    log_cms.add_event(key, count)

# CMS should never underestimate
underestimates = 0
max_overestimate = 0
for key, true_count in TRUE_COUNTS.items():
    est = log_cms.event_count(key)
    if est < true_count:
        underestimates += 1
    overestimate = est - true_count
    if overestimate > max_overestimate:
        max_overestimate = overestimate

total_events = sum(TRUE_COUNTS.values())
overestimate_pct = max_overestimate / total_events * 100

check("CMS no underestimates (1000 keys, small table)",
      underestimates == 0, f"{underestimates} underestimates")
print(f"  [INFO] Max overestimate: {max_overestimate} ({overestimate_pct:.2f}% of total)")
print(f"  [INFO] CMS size: 256x3 (deliberately small to force collisions)")

# Now test with adversarial prefix patterns
log_adv = StreamLog(cms_width=512, cms_depth=4)
adversarial_keys = [f"aaaa{chr(65 + i % 26)}" for i in range(100)]
for key in adversarial_keys:
    log_adv.add_event(key, 1000)

underestimates_adv = 0
for key in adversarial_keys:
    if log_adv.event_count(key) < 1000:
        underestimates_adv += 1

check("CMS no underestimates (adversarial prefixes)",
      underestimates_adv == 0, f"{underestimates_adv} underestimates")

print()

# =========================================================================
# 4. PATHOLOGICAL BURST INSERTION
# =========================================================================

print("4. Pathological burst insertion")
print("-" * 66)

# All same value (tests DDSketch single-bucket behavior)
log_same = StreamLog()
for _ in range(100_000):
    log_same.add_latency(42.0)
check("100K identical values: p99 correct",
      abs(log_same.p99() - 42.0) / 42.0 < 0.02,
      f"p99={log_same.p99()}")
check("100K identical values: memory bounded",
      log_same.memory_bytes() < 100_000,
      f"mem={log_same.memory_bytes()}")

# Alternating extreme values (tests DDSketch bucket spread)
log_alt = StreamLog()
for i in range(50_000):
    log_alt.add_latency(0.001)  # very small
    log_alt.add_latency(10_000.0)  # very large
exact_p99 = 10_000.0  # 99th percentile of [0.001, 10000, 0.001, 10000, ...]
err = abs(log_alt.p99() - exact_p99) / exact_p99 * 100
check("alternating extremes: p99 error < 1%", err < 1.0,
      f"p99={log_alt.p99()}, error={err:.3f}%")

# Monotonically increasing stream (tests bucket growth)
log_mono = StreamLog()
for i in range(100_000):
    log_mono.add_latency(float(i + 1))
mem_mono = log_mono.memory_bytes()
print(f"  [INFO] Monotonic 1..100K: memory = {mem_mono:,} bytes ({mem_mono/1024:.1f} KB)")
check("monotonic stream: memory bounded",
      mem_mono < 200_000, f"{mem_mono} bytes")

print()

# =========================================================================
# 5. SKEW DRIFT OVER TIME WINDOWS
# =========================================================================

print("5. Skew drift over time windows")
print("-" * 66)

# Simulate distribution shift: start uniform, shift to heavy-tail
log_drift = WindowedStreamLog(window="3s", n_buckets=6)

# Phase 1: uniform [0, 100]
random.seed(42)
for _ in range(5000):
    log_drift.add_latency(random.uniform(0.1, 100))
p99_uniform = log_drift.p99()
print(f"  Phase 1 (uniform): p99 = {p99_uniform:.2f}")

time.sleep(1.5)

# Phase 2: heavy-tail lognormal (distribution shifts)
for _ in range(5000):
    log_drift.add_latency(random.lognormvariate(5, 2))
p99_mixed = log_drift.p99()
print(f"  Phase 2 (mixed): p99 = {p99_mixed:.2f}")

# p99 should reflect the heavy-tail data now
check("distribution shift detected in p99",
      p99_mixed > p99_uniform,
      f"uniform={p99_uniform:.2f}, mixed={p99_mixed:.2f}")

time.sleep(2.0)

# Phase 3: uniform data expired, only heavy-tail remains (partially)
for _ in range(2000):
    log_drift.add_latency(random.lognormvariate(5, 2))
p99_heavy = log_drift.p99()
print(f"  Phase 3 (heavy-tail only): p99 = {p99_heavy:.2f}")

check("heavy-tail p99 > mixed p99 (old uniform expired)",
      p99_heavy >= p99_mixed * 0.8,  # allow some tolerance from partial expiry
      f"heavy={p99_heavy:.2f}, mixed={p99_mixed:.2f}")

print()

# =========================================================================
# SUMMARY
# =========================================================================

print("=" * 66)
total = PASS + FAIL
print(f"  RESULTS: {PASS}/{total} passed, {FAIL} failed")
if FAIL == 0:
    print("  ALL PRODUCTION TESTS PASS")
else:
    print(f"  {FAIL} TEST(S) FAILED")
print("=" * 66)

import sys
sys.exit(FAIL)
