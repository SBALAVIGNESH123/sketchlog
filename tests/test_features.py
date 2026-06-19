"""Test all new features: serialization, merge, thread safety, save/load."""
import random
import threading
from sketchlog import StreamLog, ThreadSafeStreamLog

def test_serialization():
    log = StreamLog(deterministic=True)
    rnd = random.Random(42)
    for _ in range(100_000):
        log.add_latency(rnd.lognormvariate(2, 1))
    for i in range(5000):
        log.add_unique(str(i))
    log.add_event("api_call", 1000)

    p99_before = log.p99()
    j = log.to_json()
    log2 = StreamLog.from_json(j)
    
    assert abs(log.p99() - log2.p99()) < 0.001, "Serialization mismatch!"
    assert log.total_events == log2.total_events

def test_merge_distributed():
    a = StreamLog()
    b = StreamLog()
    for i in range(1, 501):
        a.add_latency(float(i))
    for i in range(501, 1001):
        b.add_latency(float(i))
    a.merge(b)
    
    assert a.total_events == 1000
    assert a.p99() > 950

def test_thread_safety():
    ts = ThreadSafeStreamLog()

    def worker(n):
        for _ in range(10_000):
            ts.add_latency(float(n))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
        
    assert ts.total_events == 80_000, f"Expected 80000, got {ts.total_events}"

def test_save_load():
    import tempfile
    from pathlib import Path
    
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        log = StreamLog(deterministic=True)
        rnd = random.Random(42)
        for _ in range(10_000):
            log.add_latency(rnd.lognormvariate(2, 1))
        path = tmp_path / "sketch.json"
        log.save(str(path))
        loaded = StreamLog.load(str(path))

        assert abs(log.p99() - loaded.p99()) < 0.001

def test_constructor_validation():
    import pytest
    from sketchlog import CountMinSketch, WindowedStreamLog, StreamLog, HyperLogLog
    
    with pytest.raises(ValueError):
        CountMinSketch(width=0)
        
    with pytest.raises(ValueError):
        CountMinSketch(depth=-1)
        
    with pytest.raises(ValueError):
        WindowedStreamLog(n_buckets=0)
        
    with pytest.raises(ValueError):
        WindowedStreamLog(window="")
        
    with pytest.raises(ValueError):
        WindowedStreamLog(window="   ")
        
    with pytest.raises(ValueError):
        StreamLog().add_unique(-1)
        
    with pytest.raises(ValueError):
        HyperLogLog().add(2**64)

def test_hyperloglog_rho_calculation():
    from sketchlog import HyperLogLog

    # Highest bit set (clz = 0 -> rho = 1)
    assert HyperLogLog._rho(1 << 63, 10) == 1

    # Second highest bit set (clz = 1 -> rho = 2)
    assert HyperLogLog._rho(1 << 62, 10) == 2

    # Lowest bit set (clz = 63 -> rho = 64)
    assert HyperLogLog._rho(1, 10) == 64

    # Zero (clz = 64 -> rho = 64 - p + 1)
    assert HyperLogLog._rho(0, 10) == 55
    assert HyperLogLog._rho(0, 14) == 51

def test_nan_and_inf_handling():
    from sketchlog import StreamLog
    from sketchlog.drift import DriftSketch

    # 1. Test Python StreamLog
    log = StreamLog(deterministic=True)
    log.add_latency(float('nan'))
    log.add_latency(float('inf'))
    log.add_latency(float('-inf'))

    assert log.stats().events == 0

    # 2. Test DriftSketch
    ds = DriftSketch()
    ds.add('dim', float('nan'))
    ds.add('dim', float('inf'))
    ds.add('dim', float('-inf'))

    assert ds.summary()["metrics"][0]["events"] == 0

    # 3. Test DriftSketch batch
    ds.add_batch('dim', [float('nan'), 42.0, float('inf'), float('-inf')])
    assert ds.summary()["metrics"][0]["events"] == 1

def test_window_parsing_validation():
    import pytest
    from sketchlog import WindowedStreamLog
    from sketchlog.drift import DriftSketch

    invalid_windows = [
        float("nan"), float("inf"), float("-inf"),
        "nan", "inf", "-inf", "", "   "
    ]

    for cls in (WindowedStreamLog, DriftSketch):
        for w in invalid_windows:
            with pytest.raises(ValueError):
                cls(window=w)

def test_cpp_overflow_guards():
    import pytest
    from sketchlog import HAS_CPP
    if not HAS_CPP:
        pytest.skip("C++ extension not available")

    import _sketchlog_cpp as _cpp

    # 1. Constructor overflow
    with pytest.raises(ValueError, match=r"width \* depth overflows"):
        _cpp.CountMinSketch(2**63, 2)

    # 2. Counter overflow
    cms = _cpp.CountMinSketch()
    maximum = 2**63 - 1
    cms.add_int(1, maximum)
    with pytest.raises(OverflowError):
        cms.add_int(1, maximum)
    # Ensure zero-mutation
    assert cms.total_count() == maximum
    assert cms.estimate_int(1) == maximum

    # 3. StreamLog total_events overflow via add_latency
    log = _cpp.StreamLog()
    maximum = 2**63 - 1
    log.add_event("x", maximum)
    # StreamLog total_events_ is now 2**63 - 1.
    # To overflow uint64_t total_events_, we would need to add another 2**63 - 1 + 2 events.
    # We can't do this via add_event since CountMinSketch (int64_t max) throws first!
    # However, let's test that add_latency correctly increments total_events_
    events_before = log.total_events()
    log.add_latency(1.0)
    assert log.total_events() == events_before + 1

    # 4. StreamLog merge atomicity (should not mutate if sub-component throws)
    log1 = _cpp.StreamLog()
    log1.add_event("x", maximum // 2)
    log1.add_latency(1.0)

    log2 = _cpp.StreamLog()
    log2.add_event("y", (maximum // 2) + 2)
    log2.add_latency(2.0)

    p99_before = log1.p99()
    with pytest.raises(OverflowError, match="CountMinSketch: total_count overflow"):
        log1.merge(log2)
    # Ensure log1 is COMPLETELY untouched!
    assert log1.p99() == p99_before
    assert log1.total_events() == (maximum // 2) + 1
