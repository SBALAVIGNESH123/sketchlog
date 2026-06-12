"""Quick integration test for C++ backend."""
import sys
sys.path.insert(0, "python")

import sketchlog
print(f"Version: {sketchlog.__version__}")
print(f"C++ backend: {sketchlog.HAS_CPP}")

# Pure Python API
log = sketchlog.StreamLog()
log.add_batch([1.0, 2.0, 3.0, 4.0, 5.0])
print(f"Python StreamLog: p99={log.p99():.2f}, events={log.total_events}")

# C++ direct access
if sketchlog.HAS_CPP:
    import numpy as np
    cpp_log = sketchlog._cpp.StreamLog()
    cpp_log.add_batch(np.array([1.0, 2.0, 3.0, 4.0, 5.0]))
    print(f"C++ StreamLog:    p99={cpp_log.p99():.2f}, events={cpp_log.total_events()}")
    print(f"C++ memory:       {cpp_log.memory_kb():.1f} KB")

print()
print("All features intact:")
log2 = sketchlog.StreamLog()
for i in range(10000):
    log2.add_latency(float(i))
print(f"  memory_breakdown keys: {list(log2.memory_breakdown().keys())[:3]}...")
print(f"  deterministic flag: {sketchlog.StreamLog(deterministic=True)._deterministic}")
print(f"  WindowedStreamLog: {type(sketchlog.WindowedStreamLog(window='5m')).__name__}")
print(f"  ThreadSafeStreamLog: {type(sketchlog.ThreadSafeStreamLog()).__name__}")
print()
print("INTEGRATION OK")
