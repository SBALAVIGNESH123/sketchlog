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

    # Regression: check C++ backend throws correctly
    import pytest
    from sketchlog import HAS_CPP
    if HAS_CPP:
        cpp_log = StreamLog(deterministic=False)
        with pytest.raises(NotImplementedError, match="Serialization is not supported"):
            cpp_log.to_dict()

        # Regression: check restored backend (even if deterministic=False) serializes successfully
        # We can create a JSON string from a python backend and load it
        py_log = StreamLog(deterministic=True)
        py_log.add_latency(1.0)
        data = py_log.to_dict()
        data["deterministic"] = False
        import json
        j_data = json.dumps(data)

        # When we load from JSON, we use `from_json`, which creates a PythonStreamLog
        loaded_log = StreamLog.from_json(j_data)
        # Even though HAS_CPP is true, `loaded_log`'s _backend is a _PythonStreamLog
        # so this should succeed and not raise NotImplementedError
        loaded_log.to_dict()

def test_serialization_edge_cases():
    # 1. Empty sketch round trip
    empty_log = StreamLog(deterministic=True)
    assert StreamLog.from_dict(empty_log.to_dict()).to_dict() == empty_log.to_dict()

    # 2. Event-only round trip
    event_log = StreamLog(deterministic=True)
    event_log.add_event("api", 100)
    assert StreamLog.from_dict(event_log.to_dict()).to_dict() == event_log.to_dict()

    # 3. Unique-only round trip
    unique_log = StreamLog(deterministic=True)
    unique_log.add_unique("user_xyz")
    assert StreamLog.from_dict(unique_log.to_dict()).to_dict() == unique_log.to_dict()

    # 4. Large exact integer extrema round trip
    large_int = 10**100
    huge_log = StreamLog(deterministic=True)
    huge_log.add_latency(large_int)
    huge_log.add_latency(-large_int)
    restored_huge = StreamLog.from_json(huge_log.to_json())
    assert restored_huge.to_dict() == huge_log.to_dict()

def test_serialization_validation():
    import pytest
    import copy

    # Generate a valid payload
    log = StreamLog(deterministic=True)
    log.add_latency(10.0)
    log.add_unique("user_1")
    log.add_event("login", 1)
    valid_payload = log.to_dict()

    # 1. Invalid version
    bad_version = copy.deepcopy(valid_payload)
    bad_version['version'] = 999
    with pytest.raises(ValueError, match="Unsupported serialization version"):
        StreamLog.from_dict(bad_version)

    # 2. Invalid DDSketch state
    bad_dd_count = copy.deepcopy(valid_payload)
    bad_dd_count['latency']['count'] = 9999
    with pytest.raises(ValueError, match="do not sum to total count"):
        StreamLog.from_dict(bad_dd_count)

    bad_dd_minmax = copy.deepcopy(valid_payload)
    bad_dd_minmax['latency']['min'] = 100
    bad_dd_minmax['latency']['max'] = 10  # min > max
    with pytest.raises(ValueError, match="min cannot be greater than max"):
        StreamLog.from_dict(bad_dd_minmax)

    # 3. Invalid HyperLogLog state
    bad_hll_registers = copy.deepcopy(valid_payload)
    bad_hll_registers['uniques']['registers'].append(0)  # wrong length
    with pytest.raises(ValueError, match="HyperLogLog registers length must be"):
        StreamLog.from_dict(bad_hll_registers)

    bad_hll_val = copy.deepcopy(valid_payload)
    bad_hll_val['uniques']['registers'][0] = 999  # out of bounds
    with pytest.raises(ValueError, match="HyperLogLog register values must be"):
        StreamLog.from_dict(bad_hll_val)

    # 4. Invalid CountMinSketch state
    bad_cms_table = copy.deepcopy(valid_payload)
    bad_cms_table['events']['table'][0].append(0)  # one row is too long
    with pytest.raises(ValueError, match="CountMinSketch table row must be a list"):
        StreamLog.from_dict(bad_cms_table)

    bad_cms_val = copy.deepcopy(valid_payload)
    bad_cms_val['events']['table'][0][0] = -1
    with pytest.raises(ValueError, match="CountMinSketch cell values must be non-negative"):
        StreamLog.from_dict(bad_cms_val)

    bad_cms_row_sum = copy.deepcopy(valid_payload)
    bad_cms_row_sum['events']['table'][0][0] += 1
    with pytest.raises(ValueError, match="CountMinSketch row sum does not match events total"):
        StreamLog.from_dict(bad_cms_row_sum)

    # 5. Missing keys and type errors
    bad_keys = copy.deepcopy(valid_payload)
    del bad_keys['events']
    with pytest.raises(ValueError, match="Malformed StreamLog state"):
        StreamLog.from_dict(bad_keys)

    with pytest.raises(ValueError, match="StreamLog data must be a dictionary"):
        StreamLog.from_json("[]")

    bad_type = copy.deepcopy(valid_payload)
    bad_type['latency']['positive'] = []
    with pytest.raises(ValueError, match="DDSketch positive/negative buckets must be dictionaries"):
        StreamLog.from_dict(bad_type)

    # 6. Invalid Aggregate Consistency
    bad_total = copy.deepcopy(valid_payload)
    bad_total['total'] = 0
    with pytest.raises(ValueError, match=r"StreamLog total does not match latency count \+ events total"):
        StreamLog.from_dict(bad_total)

    # 7. Invalid DDSketch Bucket Index
    bad_dds_idx = copy.deepcopy(valid_payload)
    bad_dds_idx['latency']['positive'] = {"1000000000": 1}
    bad_dds_idx['latency']['count'] += 1
    bad_dds_idx['total'] += 1
    with pytest.raises(ValueError, match="DDSketch bucket index out of valid range"):
        StreamLog.from_dict(bad_dds_idx)

    # 8. Strict non-boolean int and finite check
    bad_float_count = copy.deepcopy(valid_payload)
    bad_float_count['latency']['count'] = 1.0
    with pytest.raises(ValueError, match="DDSketch count must be a non-negative integer"):
        StreamLog.from_dict(bad_float_count)

    bad_nan_min = copy.deepcopy(valid_payload)
    bad_nan_min['latency']['min'] = float('nan')
    with pytest.raises(ValueError, match="DDSketch min/max must be finite numbers"):
        StreamLog.from_dict(bad_nan_min)

    # 9. Semantic extrema alignment
    bad_semantic_pos = copy.deepcopy(valid_payload)
    bad_semantic_pos['latency']['min'] = -1.0
    bad_semantic_pos['latency']['max'] = -1.0
    with pytest.raises(ValueError, match="DDSketch extrema contradict positive buckets"):
        StreamLog.from_dict(bad_semantic_pos)

    bad_semantic_zero = copy.deepcopy(valid_payload)
    bad_semantic_zero['latency'].update(positive={}, negative={}, count=1, zero_count=1, min=5.0, max=5.0)
    with pytest.raises(ValueError, match="DDSketch extrema contradict zero buckets"):
        StreamLog.from_dict(bad_semantic_zero)

    bad_semantic_bucket = copy.deepcopy(valid_payload)
    bad_semantic_bucket['latency']['positive'] = {"1000": 1}
    bad_semantic_bucket['latency']['min'] = 1.0
    bad_semantic_bucket['latency']['max'] = 1.0
    with pytest.raises(ValueError, match="DDSketch max contradicts positive buckets"):
        StreamLog.from_dict(bad_semantic_bucket)

    # 10. Malformed payload exceptions
    bad_alpha = copy.deepcopy(valid_payload)
    bad_alpha['latency']['alpha'] = 1e-300
    with pytest.raises(ValueError, match="Malformed"):
        StreamLog.from_dict(bad_alpha)

    bad_inf_key = copy.deepcopy(valid_payload)
    bad_inf_key['latency']['positive'] = {float("inf"): 1}
    with pytest.raises(ValueError, match="Malformed"):
        StreamLog.from_dict(bad_inf_key)

    bad_huge_min = copy.deepcopy(valid_payload)
    bad_huge_min['latency']['min'] = 10**10000
    with pytest.raises(ValueError, match="Malformed"):
        StreamLog.from_dict(bad_huge_min)


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

    # 3. StreamLog DDSketch bin count overflow via add_latency
    bin_log = _cpp.StreamLog()
    pow_log = _cpp.StreamLog()
    pow_log.add_latency(1.0)
    for i in range(63):
        bin_log.merge(pow_log)
        if i < 62:
            pow_log.merge(pow_log)

    total_before = bin_log.total_events()
    p99_before = bin_log.p99()
    with pytest.raises(OverflowError, match="DDSketch: bin count overflow"):
        bin_log.add_latency(1.0)
    assert bin_log.total_events() == total_before
    assert bin_log.p99() == p99_before

    # 4. StreamLog total_events overflow via add_latency
    lat_log = _cpp.StreamLog()
    lat_log.merge(bin_log) # lat_log now has 2**63 - 1 latency events

    # lat_log now has exactly 2**63 - 1 events from latency.
    maximum = 2**63 - 1
    lat_log.add_event("x", maximum)
    # total_events is now 2**64 - 2
    lat_log.add_latency(2.0)
    # total_events is now 2**64 - 1 (UINT64_MAX)
    assert lat_log.total_events() == (2**64) - 1

    p99_before = lat_log.p99()
    with pytest.raises(OverflowError, match="StreamLog: total_events overflow"):
        lat_log.add_latency(3.0)
    # Ensure zero mutation
    assert lat_log.total_events() == (2**64) - 1
    assert lat_log.p99() == p99_before

    # 5. StreamLog merge atomicity (should not mutate if sub-component throws)
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


def test_ddsketch_extreme_bounds():
    import sys
    import math
    import pytest
    import sketchlog
    from sketchlog import DDSketch
    backends = [DDSketch]
    if sketchlog.HAS_CPP:
        backends.append(sketchlog._cpp.DDSketch)

    for cls in backends:
        # 1. Invalid alpha
        with pytest.raises(ValueError):
            cls(1e-20)

        # 2. Maximum finite values
        d = cls()
        d.add(sys.float_info.max)
        q = d.quantile(0.5)
        assert math.isfinite(q)
        assert q > 0

        # Sub-maximum large finite values (should be accurately recovered)
        d2 = cls(0.01)
        target = sys.float_info.max / 2.0
        d2.add(target)
        q2 = d2.quantile(0.5)
        assert math.isfinite(q2)
        # Should be within 1% relative error
        assert abs(q2 - target) / target <= 0.01

        # 3. NaN quantiles
        d3 = cls()
        d3.add(1.0)
        with pytest.raises(ValueError, match='Quantile must be in'):
            d3.quantile(float('nan'))

        # 4. Subnormal minimum values (should not underflow to zero, and bounds checked)
        smallest = float.fromhex('0x0.0000000000001p-1022')
        
        # This accuracy (0.95) allows tracking the smallest float
        d4 = cls(0.95)
        d4.add(smallest)
        q4 = d4.quantile(0.5)
        assert q4 > 0.0
        assert abs(q4 - smallest) / smallest <= 0.95

        d5 = cls(0.99)
        d5.add(-smallest)
        q5 = d5.quantile(0.5)
        assert q5 < 0.0
        assert abs(q5 - (-smallest)) / smallest <= 0.99
        
        # This accuracy (0.01) is too strict for granular subnormals, should reject
        d6 = cls(0.01)
        with pytest.raises(ValueError, match="too small"):
            d6.add(27 * smallest)

        with pytest.raises(ValueError, match="too small"):
            d6.add_batch([51 * smallest])

        with pytest.raises(ValueError, match="too small"):
            d6.add(51 * smallest)

        from sketchlog import StreamLog
        log_py = StreamLog(deterministic=True, relative_accuracy=0.01)
        with pytest.raises(ValueError, match="too small"):
            log_py.add_batch([51 * smallest])
            
        # Test generator ingestion ensures validation and ingestion consume the same elements
        if cls is DDSketch:
            d7 = cls(0.01)
            def gen():
                yield 1.0
                yield 2.0
                yield 3.0
            d7.add_batch(gen())
            assert d7.count == 3
            assert d7.quantile(0.5) > 0
