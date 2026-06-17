"""Quick integration test for C++ backend."""
import sketchlog
import pytest

def test_integration_version_and_flags():
    assert sketchlog.__version__
    assert hasattr(sketchlog, "HAS_CPP")

def test_python_streamlog():
    log = sketchlog.StreamLog()
    log.add_batch([1.0, 2.0, 3.0, 4.0, 5.0])
    assert log.total_events == 5
    assert log.p99() > 0.0

def test_cpp_streamlog():
    if not sketchlog.HAS_CPP:
        pytest.skip("C++ backend not available")
        
    import numpy as np
    cpp_log = sketchlog._cpp.StreamLog()
    cpp_log.add_batch(np.array([1.0, 2.0, 3.0, 4.0, 5.0]))
    assert cpp_log.total_events() == 5
    assert cpp_log.p99() > 0.0
    assert cpp_log.memory_kb() > 0.0

def test_cpp_nan_and_inf_handling():
    if not sketchlog.HAS_CPP:
        pytest.skip("C++ backend not available")

    import numpy as np
    cpp_log = sketchlog._cpp.StreamLog()
    cpp_log.add_latency(float('nan'))
    cpp_log.add_latency(float('inf'))
    cpp_log.add_latency(float('-inf'))

    assert cpp_log.total_events() == 0

    cpp_log.add_batch(np.array([float('nan'), 42.0, float('inf'), float('-inf')]))
    assert cpp_log.total_events() == 1

def test_all_features_intact():
    log2 = sketchlog.StreamLog()
    for i in range(1000):
        log2.add_latency(float(i))
    
    assert "total_bytes" in log2.memory_breakdown()
    assert sketchlog.StreamLog(deterministic=True)._deterministic is True
    assert isinstance(sketchlog.WindowedStreamLog(window='5m'), sketchlog.WindowedStreamLog)
    assert isinstance(sketchlog.ThreadSafeStreamLog(), sketchlog.ThreadSafeStreamLog)
