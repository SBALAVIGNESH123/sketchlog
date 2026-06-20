
from hypothesis import given, strategies as st, settings
from sketchlog import StreamLog

# Define strategies for our operations
latency_st = st.floats(min_value=0.0, max_value=1e6, allow_nan=False, allow_infinity=False)
event_st = st.text(min_size=1, max_size=20)
unique_st = st.text(min_size=1, max_size=20)

@st.composite
def operations(draw):
    op_type = draw(st.sampled_from(["latency", "event", "unique"]))
    if op_type == "latency":
        return ("latency", draw(latency_st))
    elif op_type == "event":
        return ("event", draw(event_st))
    else:
        return ("unique", draw(unique_st))

@given(st.lists(operations(), max_size=100))
@settings(max_examples=100, deadline=None)
def test_differential_backend(ops):
    """
    Apply identical operations to both Python (deterministic=True) and C++ backends.
    They must produce exactly the same serialized output.
    """
    # Deterministic mode forces the Python backend
    py_log = StreamLog(deterministic=True)
    cpp_log = StreamLog()

    for op_type, val in ops:
        if op_type == "latency":
            py_log.add_latency(val)
            cpp_log.add_latency(val)
        elif op_type == "event":
            py_log.add_event(val)
            cpp_log.add_event(val)
        elif op_type == "unique":
            py_log.add_unique(val)
            cpp_log.add_unique(val)

    # Their observable states must match exactly
    assert py_log.total_events == cpp_log.total_events
    assert py_log.unique_count() == cpp_log.unique_count()
    if py_log.total_events > 0:
        assert py_log.p99() == cpp_log.p99()
        assert py_log.p50() == cpp_log.p50()

    for op_type, val in ops:
        if op_type == "event":
            assert py_log.event_count(val) == cpp_log.event_count(val)

@given(
    st.lists(operations(), max_size=50),
    st.lists(operations(), max_size=50)
)
@settings(max_examples=50, deadline=None)
def test_merge_commutativity(ops_a, ops_b):
    """A merge B == B merge A"""
    log_a1 = StreamLog(deterministic=True)
    log_b1 = StreamLog(deterministic=True)
    log_a2 = StreamLog(deterministic=True)
    log_b2 = StreamLog(deterministic=True)

    for op_type, val in ops_a:
        if op_type == "latency":
            log_a1.add_latency(val)
            log_a2.add_latency(val)
        elif op_type == "event":
            log_a1.add_event(val)
            log_a2.add_event(val)
        elif op_type == "unique":
            log_a1.add_unique(val)
            log_a2.add_unique(val)

    for op_type, val in ops_b:
        if op_type == "latency":
            log_b1.add_latency(val)
            log_b2.add_latency(val)
        elif op_type == "event":
            log_b1.add_event(val)
            log_b2.add_event(val)
        elif op_type == "unique":
            log_b1.add_unique(val)
            log_b2.add_unique(val)

    # A merge B
    log_a1.merge(log_b1)

    # B merge A
    log_b2.merge(log_a2)

    assert log_a1.to_dict() == log_b2.to_dict()

@given(st.lists(operations(), max_size=100))
@settings(max_examples=50, deadline=None)
def test_serialization_roundtrip(ops):
    """Serialize and deserialize should recreate the exact same state."""
    log = StreamLog(deterministic=True)
    for op_type, val in ops:
        if op_type == "latency":
            log.add_latency(val)
        elif op_type == "event":
            log.add_event(val)
        elif op_type == "unique":
            log.add_unique(val)

    d = log.to_dict()
    log2 = StreamLog.from_dict(d)

    assert log.to_dict() == log2.to_dict()

@given(st.lists(operations(), max_size=50))
@settings(max_examples=50, deadline=None)
def test_merge_identity(ops):
    """A merge empty == A"""
    log_a = StreamLog(deterministic=True)
    empty_log = StreamLog(deterministic=True)
    for op_type, val in ops:
        if op_type == "latency":
            log_a.add_latency(val)
        elif op_type == "event":
            log_a.add_event(val)
        elif op_type == "unique":
            log_a.add_unique(val)

    state_before = log_a.to_dict()
    log_a.merge(empty_log)
    assert log_a.to_dict() == state_before

@given(
    st.lists(operations(), max_size=30),
    st.lists(operations(), max_size=30),
    st.lists(operations(), max_size=30)
)
@settings(max_examples=30, deadline=None)
def test_merge_associativity(ops_a, ops_b, ops_c):
    """(A merge B) merge C == A merge (B merge C)"""
    def apply_ops(log, ops):
        for op_type, val in ops:
            if op_type == "latency":
                log.add_latency(val)
            elif op_type == "event":
                log.add_event(val)
            elif op_type == "unique":
                log.add_unique(val)
    
    log_a1 = StreamLog(deterministic=True)
    log_b1 = StreamLog(deterministic=True)
    log_c1 = StreamLog(deterministic=True)
    apply_ops(log_a1, ops_a)
    apply_ops(log_b1, ops_b)
    apply_ops(log_c1, ops_c)

    log_a2 = StreamLog(deterministic=True)
    log_b2 = StreamLog(deterministic=True)
    log_c2 = StreamLog(deterministic=True)
    apply_ops(log_a2, ops_a)
    apply_ops(log_b2, ops_b)
    apply_ops(log_c2, ops_c)

    # (A merge B) merge C
    log_a1.merge(log_b1)
    log_a1.merge(log_c1)

    # A merge (B merge C)
    log_b2.merge(log_c2)
    log_a2.merge(log_b2)

    assert log_a1.to_dict() == log_a2.to_dict()

@given(st.lists(operations(), min_size=1, max_size=100))
@settings(max_examples=50, deadline=None)
def test_monotonic_counters(ops):
    """total_events must be strictly non-decreasing."""
    log = StreamLog(deterministic=True)
    prev = 0
    for op_type, val in ops:
        if op_type == "latency":
            log.add_latency(val)
        elif op_type == "event":
            log.add_event(val)
        elif op_type == "unique":
            log.add_unique(val)
        assert log.total_events >= prev
        prev = log.total_events

def test_configuration_rejection():
    """Merging logs with different parameters should raise ValueError."""
    import pytest
    log1 = StreamLog(relative_accuracy=0.01)
    log2 = StreamLog(relative_accuracy=0.05)
    with pytest.raises(ValueError):
        log1.merge(log2)

    log3 = StreamLog(cms_width=2000)
    log4 = StreamLog(cms_width=4000)
    with pytest.raises(ValueError):
        log3.merge(log4)

from sketchlog import DDSketch, HyperLogLog, CountMinSketch

@given(st.lists(latency_st, max_size=100))
@settings(max_examples=50, deadline=None)
def test_ddsketch_properties(latencies):
    sketch = DDSketch(relative_accuracy=0.01)
    for val in latencies:
        sketch.add(val)
    if latencies:
        assert sketch.min <= sketch.max
        assert sketch.quantile(0.5) >= sketch.min * 0.98 - 1e-9
        assert sketch.quantile(0.5) <= sketch.max * 1.02 + 1e-9
    assert sketch.count == len(latencies)

@given(st.lists(unique_st, max_size=100))
@settings(max_examples=50, deadline=None)
def test_hyperloglog_properties(uniques):
    hll = HyperLogLog(precision=14)
    for val in uniques:
        hll.add(val.encode('utf-8'))
    est = hll.estimate()
    assert est >= 0
    assert est <= len(uniques) * 1.05 + 1.0

@given(st.lists(event_st, max_size=100))
@settings(max_examples=50, deadline=None)
def test_countmin_properties(events):
    cms = CountMinSketch(width=2000, depth=5)
    from collections import Counter
    counts = Counter()
    for ev in events:
        cms.add(ev.encode('utf-8'))
        counts[ev] += 1
    
    # CMS estimate should always be >= true count
    for ev, true_count in counts.items():
        assert cms.estimate(ev.encode('utf-8')) >= true_count
from sketchlog import WindowedStreamLog
from sketchlog.drift import DriftSketch

@given(st.lists(operations(), max_size=50))
@settings(max_examples=50, deadline=None)
def test_windowed_properties(ops):
    # A single window log behaves largely like a StreamLog but rotates.
    import time
    windowed = WindowedStreamLog(window=1)
    prev = 0
    for op_type, val in ops:
        if op_type == "latency":
            windowed.add_latency(val)
        elif op_type == "event":
            windowed.add_event(val)
        elif op_type == "unique":
            windowed.add_unique(val)
        assert windowed.total_events >= prev
        prev = windowed.total_events

@given(st.lists(st.tuples(st.sampled_from(["dim1", "dim2", "dim3"]), latency_st), max_size=100))
@settings(max_examples=50, deadline=None)
def test_driftsketch_properties(ops):
    drift = DriftSketch(window=1)
    for dim, val in ops:
        drift.add(dim, val)
    summary = drift.summary()
    assert summary["total_events"] == len(ops)
    assert len(summary["metrics"]) <= 3
