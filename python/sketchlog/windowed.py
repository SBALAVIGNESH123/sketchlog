import time as _time
import threading
from typing import Any, Dict, Iterable, List, Optional, Tuple, Union
from sketchlog.facade import StreamLog
from sketchlog.core.stats import Stats, EventKey

def _parse_window(window: Union[str, int, float]) -> float:
    """Parse window string like '5m', '1h', '30s' to seconds."""
    if isinstance(window, (int, float)):
        result = float(window)
    else:
        window = window.strip().lower()
        if not window:
            raise ValueError("Window string cannot be empty or whitespace")
        units = {'s': 1, 'm': 60, 'h': 3600, 'd': 86400}
        if window[-1] in units:
            result = float(window[:-1]) * units[window[-1]]
        else:
            result = float(window)

    import math
    if math.isnan(result) or math.isinf(result):
        raise ValueError(f"Window must be finite, got {result}")

    if result <= 0:
        raise ValueError(f"Window must be positive, got {result}")
    return result

class WindowedStreamLog:
    """
    Sliding time window metrics in constant memory.

    Automatically expires old data so metrics reflect only
    the recent time window (e.g., last 5 minutes).

    Uses N rotating sub-sketches (buckets). Each bucket covers
    window/N seconds. When time advances, the oldest bucket is
    reset and reused. Memory = N * single_sketch_size.

    Usage:
        log = WindowedStreamLog(window="5m")   # last 5 minutes
        log.add_latency(42.0)
        log.p99()       # p99 of last 5 minutes only

        log = WindowedStreamLog(window="1h")   # last 1 hour
        log = WindowedStreamLog(window="30s")  # last 30 seconds
        log = WindowedStreamLog(window=300)    # 300 seconds
    """

    def __init__(self, window: Union[str, int, float] = "5m", n_buckets: int = 6, relative_accuracy: float = 0.01, hll_precision: int = 10, cms_width: int = 2048, cms_depth: int = 5) -> None:
        if n_buckets <= 0:
            raise ValueError(f"WindowedStreamLog n_buckets must be > 0, got {n_buckets}")

        import math
        self._window_seconds = _parse_window(window)
        self._n_buckets = n_buckets
        self._window_ns = math.ceil(self._window_seconds * 1_000_000_000)
        self._bucket_duration_ns = (self._window_ns + n_buckets - 1) // n_buckets
        self._sk_kwargs: Dict[str, Any] = dict(
            relative_accuracy=relative_accuracy,
            hll_precision=hll_precision,
            cms_width=cms_width,
            cms_depth=cms_depth,
        )

        # Create N sub-sketches
        self._buckets = [StreamLog(**self._sk_kwargs) for _ in range(n_buckets)]
        self._bucket_start_times = [_time.monotonic_ns()] * n_buckets
        self._current_bucket = 0
        self._merged = StreamLog(**self._sk_kwargs)
        self._merged_expires_at = float('inf')
        self._start_time = _time.monotonic_ns()
        self._lock = threading.RLock()  # thread-safe and reentrant

    def _rebuild_merged(self) -> None:
        self._merged.reset()
        now = _time.monotonic_ns()
        expires_at = float('inf')
        for i in range(self._n_buckets):
            age = now - self._bucket_start_times[i]
            if age <= self._window_ns and (self._buckets[i].total_events > 0 or self._buckets[i].unique_count() > 0):
                self._merged.merge(self._buckets[i])
                expiry = self._bucket_start_times[i] + self._window_ns
                if expiry < expires_at:
                    expires_at = expiry
        self._merged_expires_at = expires_at

    def _rotate(self) -> None:
        """Advance the ring buffer by one bucket."""
        now = _time.monotonic_ns()
        elapsed = now - self._bucket_start_times[self._current_bucket]

        rotated = False
        if now > self._merged_expires_at:
            rotated = True
        # Optimization: if we've been idle for the entire window length
        if elapsed >= self._n_buckets * self._bucket_duration_ns:
            for i in range(self._n_buckets):
                self._buckets[i].reset()
                self._bucket_start_times[i] = now
            self._rebuild_merged()
            return

        while elapsed >= self._bucket_duration_ns:
            prev_start = self._bucket_start_times[self._current_bucket]
            # Move to next bucket
            self._current_bucket = (self._current_bucket + 1) % self._n_buckets
            self._buckets[self._current_bucket].reset()
            self._bucket_start_times[self._current_bucket] = prev_start + self._bucket_duration_ns
            elapsed -= self._bucket_duration_ns
            rotated = True

        if rotated:
            self._rebuild_merged()

    def _active_buckets(self) -> List[StreamLog]:
        """Return all valid buckets in chronological order."""
        now = _time.monotonic_ns()
        active = []
        for i in range(self._n_buckets):
            age = now - self._bucket_start_times[i]
            if age <= self._window_ns and (self._buckets[i].total_events > 0 or self._buckets[i].unique_count() > 0):
                active.append(self._buckets[i])
        return active

    # ─── Write ────────────────────────────────────────────────────────

    def add_latency(self, value: float) -> None:
        """Add a latency measurement to the current time bucket."""
        with self._lock:
            self._rotate()
            self._buckets[self._current_bucket].add_latency(value)
            self._merged.add_latency(value)

    def add_event(self, name: EventKey, count: int = 1) -> None:
        """Record an event in the current time bucket."""
        with self._lock:
            self._rotate()
            self._buckets[self._current_bucket].add_event(name, count)
            self._merged.add_event(name, count)

    def add_unique(self, item: Union[str, bytes, int]) -> None:
        """Track a unique item in the current time bucket."""
        with self._lock:
            self._rotate()
            self._buckets[self._current_bucket].add_unique(item)
            self._merged.add_unique(item)

    # ─── Read (merged across active buckets) ─────────────────────────

    def percentile(self, q: float) -> float:
        """Get percentile across the entire active window."""
        with self._lock:
            self._rotate()
            return self._merged.percentile(q)

    def p50(self) -> float:
        return self.percentile(0.50)

    def p95(self) -> float:
        return self.percentile(0.95)

    def p99(self) -> float:
        return self.percentile(0.99)

    def p999(self) -> float:
        return self.percentile(0.999)

    def unique_count(self) -> int:
        """Estimated unique items in the active window."""
        with self._lock:
            self._rotate()
            return self._merged.unique_count()

    def event_count(self, name: Union[str, int, bytes]) -> int:
        """Approximate event frequency across the window."""
        with self._lock:
            self._rotate()
            return self._merged.event_count(name)

    @property
    def total_events(self) -> int:
        """Total events across all active buckets."""
        with self._lock:
            self._rotate()
            return self._merged.total_events

    def memory_bytes(self) -> int:
        """Total memory across all buckets (not just active ones) and cache."""
        with self._lock:
            return sum(b.memory_bytes() for b in self._buckets) + self._merged.memory_bytes()

    def memory_kb(self) -> float:
        return self.memory_bytes() / 1024.0

    @property
    def window_seconds(self) -> float:
        return self._window_seconds

    def stats(self) -> Stats:
        """Get a complete snapshot of all metrics."""
        with self._lock:
            self._rotate()
            s = self._merged.stats()
            return Stats(
                events=s.events,
                memory_bytes=self.memory_bytes(),
                memory_kb=self.memory_kb(),
                latency_p50=s.latency_p50,
                latency_p99=s.latency_p99,
                latency_p999=s.latency_p999,
                unique_count=s.unique_count,
            )

    def reset(self) -> None:
        """Reset all buckets."""
        with self._lock:
            for b in self._buckets:
                b.reset()
            self._merged.reset()
            self._merged_expires_at = float('inf')
            now = _time.monotonic_ns()
            self._bucket_start_times = [now] * self._n_buckets
            self._current_bucket = 0

    def __repr__(self) -> str:
        with self._lock:
            self._rotate()
            return (f"WindowedStreamLog(window={self._window_seconds}s, "
                    f"events={self._merged.total_events:,}, "
                    f"memory={self.memory_kb():.1f} KB)")
