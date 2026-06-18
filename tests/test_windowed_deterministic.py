import sketchlog
from sketchlog import WindowedStreamLog
import math

def test_rotate_float_precision_catchup():
    now_ns = 0
    sketchlog._time.monotonic_ns = lambda: now_ns
    
    # window=5.0s, n_buckets=6
    # window_ns = 5000000000, bucket_duration_ns = 833333333
    log = WindowedStreamLog(window=5.0, n_buckets=6)
    
    # Advance to exactly 3 * 833333333 = 2499999999
    now_ns = 2_499_999_999
    log.add_event("x")
    assert log._current_bucket == 3
    
    # Advance exactly 1 more bucket
    now_ns += 833333333
    log.add_event("y")
    assert log._current_bucket == 4

def test_retention_until_expiry_boundary():
    now_ns = 0
    sketchlog._time.monotonic_ns = lambda: now_ns
    
    log = WindowedStreamLog(window=5.0, n_buckets=6)
    log.add_event("a") # bucket 0, start time 0
    
    # bucket_duration_ns = 5000000000 // 6 = 833333333
    # 6 rotations happen at 6 * 833333333 = 4999999998
    
    # Just before rotation, the event is retained!
    now_ns = 4_999_999_997
    assert log.event_count("a") > 0
    
    # At the expiry boundary, bucket 0 is reset and rotated!
    now_ns = 4_999_999_998
    assert log.event_count("a") == 0

def test_full_window_idle_reset_and_reuse():
    now_ns = 0
    sketchlog._time.monotonic_ns = lambda: now_ns
    
    log = WindowedStreamLog(window=5.0, n_buckets=6)
    log.add_event("a")
    
    # Idle for more than the full window (5000000000 ns)
    now_ns = 6_000_000_000
    log.add_event("b")
    
    # All previous buckets should be reset and start times updated to now_ns
    for i in range(6):
        assert log._bucket_start_times[i] == 6_000_000_000
    
    assert log.event_count("a") == 0
    assert log.event_count("b") > 0

def test_ring_wraparound():
    now_ns = 0
    sketchlog._time.monotonic_ns = lambda: now_ns
    
    log = WindowedStreamLog(window=5.0, n_buckets=6)
    
    # Advance 4 buckets
    now_ns = 4 * 833333333
    log.add_event("catchup")
    assert log._current_bucket == 4
    
    # Advance 3 more buckets. 4 + 3 = 7. 7 % 6 = 1.
    now_ns += 3 * 833333333
    log.add_event("wraparound")
    assert log._current_bucket == 1

