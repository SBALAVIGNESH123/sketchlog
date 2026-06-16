"""
Stress, adversarial, and correctness tests for sketchlog.
"""

import random
import math
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
    for _ in range(100): log.add_latency(10.0)
    
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
        "normal": lambda: random.gauss(50, 10),
        "lognormal": lambda: random.lognormvariate(2, 1),
        "bimodal": lambda: random.gauss(10, 2) if random.random() > 0.5 else random.gauss(90, 2),
    }

    for name, func in distributions.items():
        vals = [func() for _ in range(10_000)]
        log = StreamLog()
        log.add_batch(vals)
        
        sorted_vals = sorted(vals)
        true_p99 = sorted_vals[int(0.99 * 10_000)]
        err = abs(log.p99() - true_p99) / true_p99 * 100
        assert err < 2.0

@pytest.mark.slow
def test_long_running_memory_stability():
    log = StreamLog()
    mem_initial = log.memory_bytes()
    
    for i in range(1_000_000):
        log.add_latency(random.lognormvariate(2, 1))
        if i % 250_000 == 0:
            pass # simulate time
            
    mem_final = log.memory_bytes()
    assert mem_final < 200_000
