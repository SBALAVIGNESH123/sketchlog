"""Test WindowedStreamLog -- sliding time window metrics."""
import time
from sketchlog import WindowedStreamLog

print("=== WINDOWED STREAMLOG ===")
print()

# Test 1: Basic windowed operation
print("Test 1: Basic operation")
log = WindowedStreamLog(window="10s", n_buckets=5)
for i in range(1, 101):
    log.add_latency(float(i))
print(f"  Events: {log.total_events}")
print(f"  p99: {log.p99():.1f} (should be ~99)")
print(f"  Memory: {log.memory_kb():.1f} KB")
assert log.total_events == 100
assert log.p99() > 90
print("  PASS")
print()

# Test 2: Window expiry
print("Test 2: Window expiry (2s window, 3s sleep)")
log2 = WindowedStreamLog(window="2s", n_buckets=4)
for i in range(100):
    log2.add_latency(float(i))
print(f"  Before sleep: {log2.total_events} events, p99={log2.p99():.1f}")
assert log2.total_events == 100
time.sleep(3)
print(f"  After 3s sleep: {log2.total_events} events, p99={log2.p99()}")
assert log2.total_events == 0, f"Expected 0 events after expiry, got {log2.total_events}"
print("  PASS")
print()

# Test 3: Window parsing
print("Test 3: Window parsing")
assert WindowedStreamLog(window="30s").window_seconds == 30.0
assert WindowedStreamLog(window="5m").window_seconds == 300.0
assert WindowedStreamLog(window="1h").window_seconds == 3600.0
assert WindowedStreamLog(window="1d").window_seconds == 86400.0
assert WindowedStreamLog(window=120).window_seconds == 120.0
print("  30s=30, 5m=300, 1h=3600, 1d=86400, 120=120")
print("  PASS")
print()

# Test 4: Thread safety (built-in)
print("Test 4: Thread safety (built-in)")
import threading
log3 = WindowedStreamLog(window="10s")
def worker(n):
    for _ in range(1000):
        log3.add_latency(float(n))
threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
for t in threads:
    t.start()
for t in threads:
    t.join()
print(f"  4 threads x 1000 = {log3.total_events} events (should be 4000)")
assert log3.total_events == 4000
print("  PASS")
print()

# Test 5: Events + uniques in windowed mode
print("Test 5: Events + uniques")
log4 = WindowedStreamLog(window="10s")
for i in range(1000):
    log4.add_event("api_call")
    log4.add_unique(str(i))
print(f"  Event count 'api_call': {log4.event_count('api_call')}")
print(f"  Unique count: {log4.unique_count()}")
assert log4.event_count("api_call") >= 1000
print("  PASS")
print()

print("=" * 50)
print("  WINDOWED STREAMLOG -- ALL TESTS PASS")
print("=" * 50)
