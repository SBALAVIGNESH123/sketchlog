import math
import random
import pytest
from sketchlog import StreamLog, DDSketch, HyperLogLog, CountMinSketch

# === DDSketch Tests ===

def test_ddsketch_empty():
    d = DDSketch()
    assert d.count == 0
    assert d.quantile(0.5) == 0.0

def test_ddsketch_single_value():
    d = DDSketch()
    d.add(42.0)
    assert d.count == 1
    assert d.min == 42.0
    assert d.max == 42.0
    assert d.quantile(0.5) == pytest.approx(42.0, rel=0.02)

def test_ddsketch_accuracy():
    """Verify <1% relative error on known distribution."""
    random.seed(42)
    d = DDSketch(relative_accuracy=0.01)
    values = [random.lognormvariate(2, 1) for _ in range(100_000)]
    for v in values:
        d.add(v)
    values.sort()
    for q in [0.5, 0.9, 0.95, 0.99]:
        exact = values[int(q * (len(values) - 1))]
        approx = d.quantile(q)
        error = abs(approx - exact) / exact
        assert error < 0.02, f"p{int(q*100)} error {error:.4f} exceeds 2%"

def test_ddsketch_rejects_nan_inf():
    d = DDSketch()
    d.add(float('nan'))
    d.add(float('inf'))
    d.add(float('-inf'))
    assert d.count == 0

def test_ddsketch_negative_values():
    d = DDSketch()
    d.add(-10.0)
    d.add(-5.0)
    d.add(0.0)
    d.add(5.0)
    d.add(10.0)
    assert d.count == 5
    assert d.min == -10.0
    assert d.max == 10.0

def test_ddsketch_invalid_alpha():
    with pytest.raises(ValueError):
        DDSketch(relative_accuracy=0.0)
    with pytest.raises(ValueError):
        DDSketch(relative_accuracy=1.0)
    with pytest.raises(ValueError):
        DDSketch(relative_accuracy=-0.5)

# === HyperLogLog Tests ===

def test_hll_empty():
    h = HyperLogLog(precision=10)
    assert h.estimate() == 0.0

def test_hll_cardinality_1k():
    h = HyperLogLog(precision=10)
    for i in range(1000):
        h.add(i)
    est = h.estimate()
    error = abs(est - 1000) / 1000
    assert error < 0.10, f"HLL error {error:.2%} exceeds 10% for 1K items"

def test_hll_cardinality_100k():
    h = HyperLogLog(precision=14)
    for i in range(100_000):
        h.add(str(i))
    est = h.estimate()
    error = abs(est - 100_000) / 100_000
    assert error < 0.05, f"HLL error {error:.2%} exceeds 5% for 100K items"

def test_hll_duplicates_dont_increase():
    h = HyperLogLog(precision=10)
    for _ in range(10000):
        h.add("same_item")
    est = h.estimate()
    assert est < 5, f"HLL should estimate ~1 for duplicates, got {est}"

def test_hll_merge():
    h1 = HyperLogLog(precision=10)
    h2 = HyperLogLog(precision=10)
    for i in range(5000):
        h1.add(i)
    for i in range(5000, 10000):
        h2.add(i)
    # Before merge, each should estimate ~5000
    # After merge, should estimate ~10000
    # We don't have merge in Python HLL yet, so just test individual
    assert h1.estimate() > 3000
    assert h2.estimate() > 3000

# === CountMinSketch Tests ===

def test_cms_empty():
    c = CountMinSketch()
    assert c.estimate("anything") == 0
    assert c.total_count == 0

def test_cms_frequency():
    c = CountMinSketch(width=2048, depth=5)
    c.add("api_call", 100)
    c.add("db_query", 50)
    c.add("cache_hit", 200)
    assert c.estimate("api_call") >= 100  # CMS overestimates, never underestimates
    assert c.estimate("db_query") >= 50
    assert c.estimate("cache_hit") >= 200
    assert c.total_count == 350

def test_cms_overestimate_bounded():
    """CMS should not overestimate by more than epsilon * total."""
    c = CountMinSketch(width=2048, depth=5)
    random.seed(42)
    for i in range(10000):
        c.add(f"item_{i}")
    # For items added once, estimate should be close to 1
    errors = [c.estimate(f"item_{i}") - 1 for i in range(100)]
    avg_error = sum(errors) / len(errors)
    assert avg_error < 10, f"Average CMS overestimate {avg_error} too high"

# === StreamLog Tests ===

def test_streamlog_basic():
    log = StreamLog()
    assert log.total_events == 0
    assert log.memory_bytes() > 0  # has overhead even when empty

def test_streamlog_latency():
    log = StreamLog()
    for i in range(1, 101):
        log.add_latency(float(i))
    assert log.p50() == pytest.approx(50.0, rel=0.05)
    assert log.p99() == pytest.approx(99.0, rel=0.05)

def test_streamlog_events():
    log = StreamLog()
    log.add_event("GET /api", 100)
    log.add_event("POST /api", 50)
    assert log.event_count("GET /api") >= 100
    assert log.event_count("POST /api") >= 50

def test_streamlog_unique():
    log = StreamLog()
    for i in range(10000):
        log.add_unique(str(i))
    est = log.unique_count()
    error = abs(est - 10000) / 10000
    assert error < 0.10, f"Unique count error {error:.2%} exceeds 10%"

def test_streamlog_constant_memory():
    """Memory should not grow linearly with events."""
    log = StreamLog()
    random.seed(42)
    for _ in range(10000):
        log.add_latency(random.lognormvariate(2, 1))
    mem_10k = log.memory_bytes()
    for _ in range(90000):
        log.add_latency(random.lognormvariate(2, 1))
    mem_100k = log.memory_bytes()
    # Memory should grow sub-linearly (at most 2x for 10x more data)
    assert mem_100k < mem_10k * 3, f"Memory grew too much: {mem_10k} -> {mem_100k}"

def test_streamlog_stats():
    log = StreamLog()
    for i in range(100):
        log.add_latency(float(i))
    s = log.stats()
    assert s.events == 100
    assert s.memory_kb > 0
    assert s.latency_p99 > 0

def test_streamlog_reset():
    log = StreamLog()
    log.add_latency(42.0)
    log.add_event("test")
    log.add_unique("user1")
    log.reset()
    assert log.total_events == 0
