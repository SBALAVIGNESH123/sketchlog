"""Test WindowedStreamLog -- sliding time window metrics."""
import time
import threading
from sketchlog import WindowedStreamLog

def test_basic_windowed_operation():
    log = WindowedStreamLog(window="10s", n_buckets=5)
    for i in range(1, 101):
        log.add_latency(float(i))

    assert log.total_events == 100
    assert log.p99() > 90
    assert log.memory_kb() > 0.0

def test_window_expiry():
    log2 = WindowedStreamLog(window="2s", n_buckets=4)
    for i in range(100):
        log2.add_latency(float(i))
        
    assert log2.total_events == 100
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline and log2.total_events != 0:
        time.sleep(0.05)
    assert log2.total_events == 0, f"Expected 0 events after expiry, got {log2.total_events}"

def test_window_parsing():
    assert WindowedStreamLog(window="30s").window_seconds == 30.0
    assert WindowedStreamLog(window="5m").window_seconds == 300.0
    assert WindowedStreamLog(window="1h").window_seconds == 3600.0
    assert WindowedStreamLog(window="1d").window_seconds == 86400.0
    assert WindowedStreamLog(window=120).window_seconds == 120.0

def test_thread_safety_built_in():
    log3 = WindowedStreamLog(window="10s")
    def worker(n):
        for _ in range(1000):
            log3.add_latency(float(n))
            
    threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
        
    assert log3.total_events == 4000

def test_events_and_uniques():
    log4 = WindowedStreamLog(window="10s")
    for i in range(1000):
        log4.add_event("api_call")
        log4.add_unique(str(i))
        
    assert log4.event_count("api_call") >= 1000
    assert log4.unique_count() > 950  # HyperLogLog approximation

def test_empty_window_no_deadlock():
    from sketchlog import WindowedStreamLog
    log = WindowedStreamLog(window="1m")
    
    # Should not deadlock
    stats = log.stats()
    assert stats.events == 0
    
    # Should not deadlock
    r = repr(log)
    assert r.startswith("WindowedStreamLog")
