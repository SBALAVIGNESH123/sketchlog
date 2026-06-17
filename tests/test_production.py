"""
Production-grade tests for sketchlog.

1. Concurrency profiling (1, 2, 4, 8, 16 threads)
2. Chaos windowing (bursts, silence, reactivation)
3. Adversarial CMS hash collision stress
4. Pathological burst insertion
5. Skew drift over time windows
"""

import random
import time
import threading
import pytest
from sketchlog import StreamLog, ThreadSafeStreamLog, WindowedStreamLog

@pytest.mark.slow
@pytest.mark.parametrize("n_threads", [1, 2, 4, 8, 16])
def test_concurrency_profiling(n_threads):
    EVENTS_PER_THREAD = 50_000
    log = ThreadSafeStreamLog()
    
    def worker():
        for i in range(EVENTS_PER_THREAD):
            log.add_latency(float(i % 1000) + 1.0)
    
    threads = [threading.Thread(target=worker) for _ in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    
    expected = n_threads * EVENTS_PER_THREAD
    assert log.total_events == expected

@pytest.mark.slow
def test_chaos_windowing():
    log_chaos = WindowedStreamLog(window="2s", n_buckets=4)

    # Burst 1
    for i in range(5000):
        log_chaos.add_latency(float(i + 1))
    assert log_chaos.total_events == 5000

    time.sleep(1.0)

    # Burst 2
    for i in range(3000):
        log_chaos.add_latency(float(i + 10000))
    assert log_chaos.total_events == 8000

    # Full silence
    time.sleep(2.5)
    assert log_chaos.total_events == 0

    # Reactivation
    for i in range(2000):
        log_chaos.add_latency(float(i + 50000))
    assert log_chaos.total_events == 2000
    assert log_chaos.p99() > 50000

    assert log_chaos.memory_bytes() < 500_000

def test_adversarial_cms_hash_collision_stress():
    log_cms = StreamLog(cms_width=256, cms_depth=3)
    N_KEYS = 1000
    TRUE_COUNTS = {}
    random.seed(42)

    for i in range(N_KEYS):
        key = f"key_{i}"
        count = random.randint(1, 100)
        TRUE_COUNTS[key] = count
        log_cms.add_event(key, count)

    underestimates = 0
    for key, true_count in TRUE_COUNTS.items():
        if log_cms.event_count(key) < true_count:
            underestimates += 1
            
    assert underestimates == 0

    log_adv = StreamLog(cms_width=512, cms_depth=4)
    adversarial_keys = [f"aaaa{i}" for i in range(100)]
    for key in adversarial_keys:
        log_adv.add_event(key, 1000)

    underestimates_adv = 0
    for key in adversarial_keys:
        if log_adv.event_count(key) < 1000:
            underestimates_adv += 1

    assert underestimates_adv == 0

def test_pathological_burst_insertion():
    # Identical values
    log_same = StreamLog()
    for _ in range(100_000):
        log_same.add_latency(42.0)
    assert abs(log_same.p99() - 42.0) / 42.0 < 0.02
    assert log_same.memory_bytes() < 100_000

    # Alternating extremes
    log_alt = StreamLog()
    for i in range(50_000):
        log_alt.add_latency(0.001)
        log_alt.add_latency(10_000.0)
    
    exact_p99 = 10_000.0
    err = abs(log_alt.p99() - exact_p99) / exact_p99 * 100
    assert err < 1.0

    # Monotonic stream
    log_mono = StreamLog()
    for i in range(100_000):
        log_mono.add_latency(float(i + 1))
    assert log_mono.memory_bytes() < 200_000

@pytest.mark.slow
def test_skew_drift_over_time_windows():
    log_drift = WindowedStreamLog(window="3s", n_buckets=6)
    random.seed(42)
    
    for _ in range(5000): log_drift.add_latency(random.uniform(0.1, 100))
    p99_uniform = log_drift.p99()

    time.sleep(1.5)

    for _ in range(5000): log_drift.add_latency(random.lognormvariate(5, 2))
    p99_mixed = log_drift.p99()

    assert p99_mixed > p99_uniform

    time.sleep(2.0)

    for _ in range(2000): log_drift.add_latency(random.lognormvariate(5, 2))
    p99_heavy = log_drift.p99()

    assert p99_heavy >= p99_mixed * 0.8
