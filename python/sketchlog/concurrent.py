import threading
from typing import Any, Dict, Iterable, List, Optional, Tuple, Union
from sketchlog.facade import StreamLog
from sketchlog.core.stats import Stats, EventKey

class ThreadSafeStreamLog:
    """Thread-safe StreamLog. Safe to use from multiple threads."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._log = StreamLog(*args, **kwargs)
        self._lock = threading.Lock()

    def add_latency(self, value: float) -> None:
        with self._lock:
            self._log.add_latency(value)

    def add_event(self, name: EventKey, count: int = 1) -> None:
        with self._lock:
            self._log.add_event(name, count)

    def add_unique(self, item: Union[str, bytes, int]) -> None:
        with self._lock:
            self._log.add_unique(item)

    def p50(self) -> float:
        with self._lock:
            return self._log.p50()

    def p95(self) -> float:
        with self._lock:
            return self._log.p95()

    def p99(self) -> float:
        with self._lock:
            return self._log.p99()

    def p999(self) -> float:
        with self._lock:
            return self._log.p999()

    def percentile(self, q: float) -> float:
        with self._lock:
            return self._log.percentile(q)

    def event_count(self, name: Union[str, int, bytes]) -> int:
        with self._lock:
            return self._log.event_count(name)

    def unique_count(self) -> int:
        with self._lock:
            return self._log.unique_count()

    @property
    def total_events(self) -> int:
        with self._lock:
            return self._log.total_events

    def memory_bytes(self) -> int:
        with self._lock:
            return self._log.memory_bytes()

    def memory_kb(self) -> float:
        with self._lock:
            return self._log.memory_kb()

    def stats(self) -> Stats:
        with self._lock:
            return self._log.stats()

    def reset(self) -> None:
        with self._lock:
            self._log.reset()

    def __repr__(self) -> str:
        with self._lock:
            return f"ThreadSafe{repr(self._log)}"
