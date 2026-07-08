"""
sketchlog -- Streaming metrics engine with constant memory.

Track p99 latency, event frequency, and cardinality in bounded memory.

    from sketchlog import StreamLog

    log = StreamLog()
    for latency in request_stream:
        log.add_latency(latency)

    print(log.p99())              # bounded-error p99
    print(log.memory_kb(), "KB")  # configuration-dependent bounded memory

For real-time windows:

    from sketchlog import WindowedStreamLog

    log = WindowedStreamLog(window="5m")  # last 5 minutes only
    log.add_latency(42.0)
    log.p99()  # p99 of the last 5 minutes

For multi-threaded use:

    from sketchlog import ThreadSafeStreamLog

    log = ThreadSafeStreamLog()
    # safe to call from any thread
"""

__version__ = "1.2.4"

from sketchlog.facade import StreamLog
from sketchlog.concurrent import ThreadSafeStreamLog
from sketchlog.windowed import WindowedStreamLog
from sketchlog.drift import DriftSketch
from sketchlog.sql import SQLStreamEngine
from sketchlog.core.stats import Stats
from sketchlog.core.ddsketch import DDSketch
from sketchlog.core.hll import HyperLogLog
from sketchlog.core.cms import CountMinSketch
from sketchlog.ebpf import EBPFCollector
from sketchlog.diff import SketchDiff

import time as _time

__all__ = [
    "StreamLog",
    "ThreadSafeStreamLog",
    "WindowedStreamLog",
    "DriftSketch",
    "SQLStreamEngine",
    "Stats",
    "DDSketch",
    "HyperLogLog",
    "CountMinSketch",
    "EBPFCollector",
    "SketchDiff",
]

# ── C++ backend auto-detection ───────────────────────────────────────────
# If the compiled C++ extension is available, we expose it as _cpp module.
# The Python StreamLog remains the primary API (with serialization, windowing,
# etc.), but users can access the raw C++ engine for maximum throughput.

try:
    import _sketchlog_cpp as _cpp  # pyright: ignore[reportMissingImports]
    HAS_CPP = True
except ImportError:
    _cpp = None
    HAS_CPP = False
