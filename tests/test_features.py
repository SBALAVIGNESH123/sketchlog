"""Test all new features: serialization, merge, thread safety, save/load."""
import random
import threading
from sketchlog import StreamLog, ThreadSafeStreamLog

def test_serialization():
    log = StreamLog()
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

def test_save_load(tmp_path):
    log = StreamLog()
    rnd = random.Random(42)
    for _ in range(10_000):
        log.add_latency(rnd.lognormvariate(2, 1))
    path = tmp_path / "sketch.json"
    log.save(path)
    loaded = StreamLog.load(path)
    
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
        StreamLog().add_unique(-1)
        
    with pytest.raises(ValueError):
        HyperLogLog().add(2**64)
