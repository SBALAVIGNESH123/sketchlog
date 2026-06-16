"""Quick integration test for C++ backend."""
import sketchlog

def test_integration_version_and_flags():
    assert sketchlog.__version__
    assert hasattr(sketchlog, "HAS_CPP")

def test_python_streamlog():
    log = sketchlog.StreamLog()
    log.add_batch([1.0, 2.0, 3.0, 4.0, 5.0])
    assert log.total_events == 5
    assert log.p99() > 0.0

def test_cpp_streamlog():
    if sketchlog.HAS_CPP:
        import numpy as np
        cpp_log = sketchlog._cpp.StreamLog()
        cpp_log.add_batch(np.array([1.0, 2.0, 3.0, 4.0, 5.0]))
        assert cpp_log.total_events() == 5
        assert cpp_log.p99() > 0.0
        assert cpp_log.memory_kb() > 0.0

def test_all_features_intact():
    log2 = sketchlog.StreamLog()
    for i in range(1000):
        log2.add_latency(float(i))
    
    assert "total_bytes" in log2.memory_breakdown()
    assert sketchlog.StreamLog(deterministic=True)._deterministic is True
    assert isinstance(sketchlog.WindowedStreamLog(window='5m'), sketchlog.WindowedStreamLog)
    assert isinstance(sketchlog.ThreadSafeStreamLog(), sketchlog.ThreadSafeStreamLog)
