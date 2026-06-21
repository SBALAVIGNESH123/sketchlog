import os
import shutil

src = 'python/sketchlog/__init__.py'
lines = open(src, encoding='utf-8').read().splitlines()

os.makedirs('python/sketchlog/core', exist_ok=True)
open('python/sketchlog/core/__init__.py', 'w').close()

def get_lines(start, end):
    return '\n'.join(lines[start-1:end]) + '\n'

stats_code = '''from typing import Union, NamedTuple\n\nEventKey = Union[str, bytes, int]\n\n''' + get_lines(64, 71)
open('python/sketchlog/core/stats.py', 'w', encoding='utf-8').write(stats_code)

ddsketch_code = '''import math\nfrom typing import Dict, List\n\n''' + get_lines(78, 253)
open('python/sketchlog/core/ddsketch.py', 'w', encoding='utf-8').write(ddsketch_code)

hll_code = '''import math\nimport struct\nimport hashlib\nfrom typing import Any\n\n''' + get_lines(260, 345)
open('python/sketchlog/core/hll.py', 'w', encoding='utf-8').write(hll_code)

cms_code = '''import math\nimport struct\nimport hashlib\nfrom typing import List\n\n''' + get_lines(352, 434)
open('python/sketchlog/core/cms.py', 'w', encoding='utf-8').write(cms_code)

facade_code = '''import sys\nimport time as _time\nfrom typing import Any, Dict, Iterable, List, Optional, Tuple\nimport json\n\nfrom sketchlog.core.stats import Stats, EventKey\nfrom sketchlog.core.ddsketch import DDSketch\nfrom sketchlog.core.hll import HyperLogLog\nfrom sketchlog.core.cms import CountMinSketch\n\ntry:\n    import _sketchlog_cpp as _cpp  # pyright: ignore[reportMissingImports]\n    HAS_CPP = True\nexcept ImportError:\n    _cpp = None\n    HAS_CPP = False\n\n''' + get_lines(441, 904) + '\n' + get_lines(916, 1043)
open('python/sketchlog/facade.py', 'w', encoding='utf-8').write(facade_code)

concurrent_code = '''import threading\nfrom typing import Any, Dict, Iterable, List, Optional, Tuple\nfrom sketchlog.facade import StreamLog\nfrom sketchlog.core.stats import Stats, EventKey\n\n''' + get_lines(1046, 1116)
open('python/sketchlog/concurrent.py', 'w', encoding='utf-8').write(concurrent_code)

windowed_code = '''import time as _time\nfrom typing import Any, Dict, Iterable, List, Optional, Tuple\nfrom sketchlog.facade import StreamLog\nfrom sketchlog.core.stats import Stats, EventKey\n\n''' + get_lines(1123, 1143) + '\n' + get_lines(1146, 1347)
open('python/sketchlog/windowed.py', 'w', encoding='utf-8').write(windowed_code)

init_code = '''"""
sketchlog -- Streaming metrics engine with constant memory.

Track p99 latency, event frequency, and cardinality over
billions of events using ~93 KB of RAM.

    from sketchlog import StreamLog

    log = StreamLog()
    for latency in request_stream:
        log.add_latency(latency)

    print(log.p99())              # bounded-error p99
    print(log.memory_kb(), "KB")  # constant ~93 KB

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

__version__ = "1.0.1"

from sketchlog.facade import StreamLog
from sketchlog.concurrent import ThreadSafeStreamLog
from sketchlog.windowed import WindowedStreamLog

__all__ = [
    "StreamLog",
    "ThreadSafeStreamLog",
    "WindowedStreamLog"
]
'''
open('python/sketchlog/__init__.py', 'w', encoding='utf-8').write(init_code)

print('Split completed!')
