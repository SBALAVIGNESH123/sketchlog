
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
