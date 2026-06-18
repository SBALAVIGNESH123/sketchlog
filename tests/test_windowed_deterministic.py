import sketchlog
from sketchlog import WindowedStreamLog

def test_rotate_float_precision_catchup(monkeypatch):
    now_ns = 0
    monkeypatch.setattr(sketchlog._time, "monotonic_ns", lambda: now_ns)

    # window=5.0s, n_buckets=6
    # window_ns = 5000000000, bucket_duration_ns = 833333334
    log = WindowedStreamLog(window=5.0, n_buckets=6)

    # Advance to exactly 3 * 833333333.333 -> 2.5s -> 2500000000ns
    # With new ceiling division: bucket_duration_ns = 833333334
    # 2500000000 // 833333334 = 2 (so it is in bucket 2)
    # Wait, the test specifically checked catchup for exact float cancellation.
    # At 2_500_000_000, elapsed = 2500000000. 3 * 833333334 = 2500000002.
    # So 2 rotations happen. current_bucket is 2.
    now_ns = 2_500_000_000
    log.add_event("x")
    assert log._current_bucket == 2

    # Advance exactly 1 more bucket (833333334) -> 3333333334
    now_ns += 833333334
    log.add_event("y")
    assert log._current_bucket == 3

def test_retention_until_expiry_boundary(monkeypatch):
    now_ns = 0
    monkeypatch.setattr(sketchlog._time, "monotonic_ns", lambda: now_ns)

    log = WindowedStreamLog(window=5.0, n_buckets=6)
    log.add_event("a") # bucket 0, start time 0

    # The window is exactly 5000000000 ns.
    # Data must be retained *before* and *at* the exact boundary.
    now_ns = 5_000_000_000
    assert log.event_count("a") > 0

    # Just after the configured window, age > window_ns, so it expires.
    now_ns = 5_000_000_001
    assert log.event_count("a") == 0

def test_full_window_idle_reset_and_reuse(monkeypatch):
    now_ns = 0
    monkeypatch.setattr(sketchlog._time, "monotonic_ns", lambda: now_ns)

    log = WindowedStreamLog(window=5.0, n_buckets=6)
    log.add_event("a")

    # Idle for more than the full ring capacity (6 * 833333334 = 5000000004 ns)
    now_ns = 5_000_000_004
    log.add_event("b")

    # All previous buckets should be reset and start times updated to now_ns
    for i in range(6):
        assert log._bucket_start_times[i] == 5_000_000_004

    assert log.event_count("a") == 0
    assert log.event_count("b") > 0

def test_ring_wraparound(monkeypatch):
    now_ns = 0
    monkeypatch.setattr(sketchlog._time, "monotonic_ns", lambda: now_ns)

    log = WindowedStreamLog(window=5.0, n_buckets=6)

    # Advance 4 buckets
    now_ns = 4 * 833333334
    log.add_event("catchup")
    assert log._current_bucket == 4

    # Advance 3 more buckets. 4 + 3 = 7. 7 % 6 = 1.
    now_ns += 3 * 833333334
    log.add_event("wraparound")
    assert log._current_bucket == 1

def test_zero_duration_bucket_prevention(monkeypatch):
    now_ns = 0
    monkeypatch.setattr(sketchlog._time, "monotonic_ns", lambda: now_ns)

    # A tiny window_ns < n_buckets should correctly have a bucket_duration_ns >= 1
    # 1e-9 seconds is 1 ns
    log = WindowedStreamLog(window=1e-9, n_buckets=2)
    assert log._bucket_duration_ns >= 1
    log.add_event("x")

    # Prove it does not hang
    now_ns = 10
    assert log.event_count("x") == 0
