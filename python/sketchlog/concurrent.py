import threading
from typing import Any, Dict, Iterable, List, Optional, Tuple, Union
from sketchlog.facade import StreamLog
from sketchlog.core.stats import Stats, EventKey
import time

class ThreadSafeStreamLog:
    """Thread-safe StreamLog. Safe to use from multiple threads."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._log = StreamLog(*args, **kwargs)
        self._lock = threading.Lock()
        self._save_lock = threading.Lock()
        self.last_updated = time.time()

    def add_latency(self, value: float) -> None:
        with self._lock:
            self._log.add_latency(value)
            self.last_updated = time.time()

    def add_batch(self, values: Iterable[float]) -> None:
        with self._lock:
            self._log.add_batch(values)
            self.last_updated = time.time()

    def add_event(self, name: EventKey, count: int = 1) -> None:
        with self._lock:
            self._log.add_event(name, count)
            self.last_updated = time.time()

    def get_snapshot(self) -> StreamLog:
        with self._lock:
            # Create a deep copy of the underlying log
            snapshot = self._log.clone_empty()
            snapshot.merge(self._log)
            return snapshot

    def add_unique(self, item: Union[str, bytes, int]) -> None:
        with self._lock:
            self._log.add_unique(item)
            self.last_updated = time.time()

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
            self.last_updated = time.time()

    def merge(self, other: Union["ThreadSafeStreamLog", StreamLog]) -> None:
        if isinstance(other, ThreadSafeStreamLog):
            # To avoid deadlocks, take a snapshot of the other log first
            other_snap = other.get_snapshot()
            with self._lock:
                self._log.merge(other_snap)
                self.last_updated = time.time()
        else:
            with self._lock:
                self._log.merge(other)
                self.last_updated = time.time()

    def memory_breakdown(self) -> Dict[str, Any]:
        with self._lock:
            return self._log.memory_breakdown()

    def to_dict(self) -> Dict[str, Any]:
        with self._lock:
            return self._log.to_dict()

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ThreadSafeStreamLog":
        instance = cls()
        instance._log = StreamLog.from_dict(data)
        return instance

    def to_json(self) -> str:
        with self._lock:
            return self._log.to_json()

    @classmethod
    def from_json(cls, json_str: str) -> "ThreadSafeStreamLog":
        instance = cls()
        instance._log = StreamLog.from_json(json_str)
        return instance

    def save(self, path: str) -> None:
        snapshot = self.get_snapshot()
        with self._save_lock:
            snapshot.save(path)

    @classmethod
    def load(cls, path: str) -> "ThreadSafeStreamLog":
        instance = cls()
        instance._log = StreamLog.load(path)
        return instance

    def __repr__(self) -> str:
        with self._lock:
            return f"ThreadSafe{repr(self._log)}"
