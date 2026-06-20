"""
Advanced correctness tests for sketchlog.

1. Distributed skew merge (heavy-tail shard + uniform shard)
2. WindowedStreamLog expiration correctness
3. WindowedStreamLog memory creep under burst traffic
4. Scale-to-billion proof (extrapolated from 100M)
5. Merge commutativity and associativity
"""

import random
import time
import pytest
from sketchlog import StreamLog, WindowedStreamLog

def wait_for_condition(cond, timeout=5.0):
    start = time.time()
    while time.time() - start < timeout:
        if cond():
            return True
        time.sleep(0.1)
    return False


def test_distributed_skew_merge():
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

    log_a_copy = StreamLog()
    log_a_copy.add_batch(shard_a_values)
    log_a_copy.merge(log_b)

    p99_err = abs(log_a_copy.p99() - true_p99) / true_p99 * 100
    p50_err = abs(log_a_copy.p50() - true_p50) / true_p50 * 100

    assert p99_err < 1.0, f"skewed merge p99 error too high: {p99_err:.3f}%"
    assert p50_err < 1.0, f"skewed merge p50 error too high: {p50_err:.3f}%"
    assert log_a_copy.total_events == 100_000

def test_extreme_skew_merge():
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

    assert err_extreme < 1.0, f"99:1 skew merge p99 error too high: {err_extreme:.3f}%"

def test_windowed_expiration_correctness():
    # 2a: Data expires after window
    log_w = WindowedStreamLog(window="1s", n_buckets=4)
    for i in range(100):
        log_w.add_latency(float(i + 1))

    assert log_w.total_events == 100
    assert wait_for_condition(lambda: log_w.total_events == 0, timeout=3.0)

    # 2b: New data after expiry works
    for i in range(50):
        log_w.add_latency(float(i + 200))

    assert log_w.total_events == 50
    assert log_w.p99() > 200

    # 2c: Partial expiry (window = 2s, add at t=0, sleep 1s, add more, check)
    log_w2 = WindowedStreamLog(window="2s", n_buckets=4)
    for i in range(100):
        log_w2.add_latency(10.0)

    time.sleep(1.2)

    for i in range(100):
        log_w2.add_latency(500.0)

    # Both batches should be active (within 2s window)
    assert log_w2.total_events == 200

    time.sleep(1.2)

    # First batch should have expired, second still active
    assert wait_for_condition(lambda: log_w2.total_events == 100, timeout=3.0)

def test_windowed_memory_stability():
    log_w3 = WindowedStreamLog(window="2s", n_buckets=4)
    mem_initial = log_w3.memory_bytes()

    # Simulate burst traffic: add lots, let expire, add more
    for _ in range(3):
        for _ in range(10_000):
            log_w3.add_latency(random.lognormvariate(2, 1))
        time.sleep(0.8)

    mem_after = log_w3.memory_bytes()
    ratio = mem_after / mem_initial if mem_initial > 0 else 1.0

    assert ratio < 2.0, f"Memory ratio exceeded 2.0x (ratio={ratio:.2f})"

def test_merge_commutativity_and_associativity():
    random.seed(42)
    vals_x = [random.lognormvariate(2, 1) for _ in range(10_000)]
    vals_y = [random.lognormvariate(3, 0.5) for _ in range(10_000)]
    vals_z = [random.uniform(1, 100) for _ in range(10_000)]

    # Commutativity: merge(A,B) == merge(B,A)
    ab = StreamLog()
    ab.add_batch(vals_x)
    ab_other = StreamLog()
    ab_other.add_batch(vals_y)
    ab.merge(ab_other)

    ba = StreamLog()
    ba.add_batch(vals_y)
    ba_other = StreamLog()
    ba_other.add_batch(vals_x)
    ba.merge(ba_other)

    assert abs(ab.p99() - ba.p99()) < 0.001

    # Associativity: merge(merge(A,B), C) == merge(A, merge(B,C))
    ab_c = StreamLog()
    ab_c.add_batch(vals_x)
    ab_c_other1 = StreamLog()
    ab_c_other1.add_batch(vals_y)
    ab_c.merge(ab_c_other1)

    ab_c_other2 = StreamLog()
    ab_c_other2.add_batch(vals_z)
    ab_c.merge(ab_c_other2)

    a_bc = StreamLog()
    a_bc.add_batch(vals_x)

    bc = StreamLog()
    bc.add_batch(vals_y)
    bc_other = StreamLog()
    bc_other.add_batch(vals_z)
    bc.merge(bc_other)

    a_bc.merge(bc)

    assert abs(ab_c.p99() - a_bc.p99()) < 0.001

@pytest.mark.slow
def test_scale_proof_100m_events():
    log_scale = StreamLog()
    rnd = random.Random(42)
    checkpoints = [1_000_000, 10_000_000, 50_000_000, 100_000_000]
    memory_log = {}
    batch_size = 100_000

    for i in range(0, 100_000_000, batch_size):
        batch = [rnd.lognormvariate(2, 1) for _ in range(batch_size)]
        log_scale.add_batch(batch)
        n = i + batch_size
        if n in checkpoints:
            memory_log[n] = log_scale.memory_bytes()

    ratio_100m_1m = memory_log[100_000_000] / memory_log[1_000_000]
    assert ratio_100m_1m < 1.1, f"Memory ratio (100M/1M) = {ratio_100m_1m:.2f}x"
    assert log_scale.total_events == 100_000_000
    assert log_scale.p99() > 0.0
