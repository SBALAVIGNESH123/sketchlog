"""
Stress, adversarial, and correctness tests for sketchlog.
"""

import random
import time
import pytest
from sketchlog import StreamLog

def test_batch_vs_scalar_equivalence():
    random.seed(42)
    values = [random.lognormvariate(2, 1) for _ in range(5000)]
    
    log_scalar = StreamLog()
    for v in values:
        log_scalar.add_latency(v)
        
    log_batch = StreamLog()
    log_batch.add_batch(values)

    assert log_scalar.total_events == log_batch.total_events

    assert abs(log_scalar.p99() - log_batch.p99()) < 0.001

def test_memory_breakdown_transparency():
    log = StreamLog()
    for _ in range(100):
        log.add_latency(10.0)
    
    mem = log.memory_breakdown()
    assert "total_bytes" in mem
    assert "ddsketch_bytes" in mem
    assert "hyperloglog_bytes" in mem
    assert "countmin_bytes" in mem
    assert mem["total_bytes"] == mem["ddsketch_bytes"] + mem["hyperloglog_bytes"] + mem["countmin_bytes"]

def test_deterministic_mode():
    values = [random.uniform(1, 100) for _ in range(10_000)]
    
    log1 = StreamLog(deterministic=True)
    log1.add_batch(values)
    
    log2 = StreamLog(deterministic=True)
    log2.add_batch(values)

    assert log1.p99() == log2.p99()
    assert log1.to_json() == log2.to_json()

def test_distribution_robustness():
    distributions = {
        "uniform": lambda: random.uniform(1, 100),
        "normal": lambda: max(0.001, random.gauss(50, 10)),
        "lognormal": lambda: random.lognormvariate(2, 1),
        "bimodal": lambda: random.gauss(10, 2) if random.random() > 0.5 else random.gauss(90, 2),
        "zipf-like": lambda: 1.0 / (random.random() ** 0.5 + 0.001),
    }

    for name, func in distributions.items():
        vals = [func() for _ in range(10_000)]
        log = StreamLog()
        log.add_batch(vals)
        
        sorted_vals = sorted(vals)
        true_p99 = sorted_vals[int(0.99 * 10_000)]
        if true_p99 != 0:
            err = abs(log.p99() - true_p99) / abs(true_p99) * 100
        else:
            err = 0.0
        assert err < 2.5, f"{name} distribution error too high: {err}%"

@pytest.mark.slow
def test_long_running_memory_stability():
    log_stable = StreamLog()
    memory_at = {}
    rnd = random.Random(42)

    for i in range(5_000_000):
        log_stable.add_latency(rnd.lognormvariate(2, 1))
        if (i + 1) in (100_000, 1_000_000, 2_000_000, 5_000_000):
            memory_at[i + 1] = log_stable.memory_bytes()

    ratio = memory_at[5_000_000] / memory_at[100_000]
    assert ratio < 1.5, f"memory growth ratio (5M/100K) = {ratio:.2f}x"

@pytest.mark.slow
def test_speed_comparison_batch_vs_scalar():
    rnd = random.Random(99)
    bench_values = [rnd.lognormvariate(2, 1) for _ in range(500_000)]

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
    # Use a very loose guard to avoid gating CI on wall-clock speed, but ensure functionality
    assert speedup > 0.1, f"Batch speed is unacceptably slow, got {speedup}x"

def test_merge_correctness_5_shards():
    rnd = random.Random(42)
    all_values = [rnd.lognormvariate(2, 1) for _ in range(100_000)]
    shard_size = 20_000

    log_full = StreamLog()
    log_full.add_batch(all_values)

    shards = []
    for i in range(5):
        s = StreamLog()
        s.add_batch(all_values[i*shard_size : (i+1)*shard_size])
        shards.append(s)

    log_merged = shards[0]
    for s in shards[1:]:
        log_merged.merge(s)

    assert log_merged.total_events == log_full.total_events
    assert abs(log_merged.p99() - log_full.p99()) / log_full.p99() < 0.001
    assert abs(log_merged.p50() - log_full.p50()) / log_full.p50() < 0.001

def test_repeated_merge_stability():
    base = StreamLog()
    base.add_latency(100.0)
    for _ in range(100):
        more = StreamLog()
        more.add_latency(100.0)
        base.merge(more)

    assert base.total_events == 101
    assert abs(base.p99() - 100.0) / 100.0 < 0.02

def test_merge_config_mismatch_rejection():
    a = StreamLog(relative_accuracy=0.01)
    b = StreamLog(relative_accuracy=0.05)
    with pytest.raises(ValueError):
        a.merge(b)

def test_adversarial_extreme_values():
    log_ext = StreamLog()
    log_ext.add_latency(1e-10)
    log_ext.add_latency(1e10)
    log_ext.add_latency(0.0)
    assert log_ext.total_events == 3

def test_adversarial_nan_inf_rejection():
    log_nan = StreamLog()
    log_nan.add_latency(float('nan'))
    log_nan.add_latency(float('inf'))
    log_nan.add_latency(float('-inf'))
    log_nan.add_latency(42.0)
    assert log_nan.total_events == 1

def test_adversarial_duplicates():
    log_dup = StreamLog()
    for _ in range(100_000):
        log_dup.add_latency(42.0)
    assert abs(log_dup.p99() - 42.0) / 42.0 < 0.02

def test_adversarial_single_value():
    log_one = StreamLog()
    log_one.add_latency(7.0)
    assert abs(log_one.p99() - 7.0) < 0.1

def test_adversarial_batch_nan_inf():
    log_mixed = StreamLog()
    mixed = [1.0, float('nan'), 2.0, float('inf'), 3.0, float('-inf'), 4.0]
    log_mixed.add_batch(mixed)
    assert log_mixed.total_events == 4
