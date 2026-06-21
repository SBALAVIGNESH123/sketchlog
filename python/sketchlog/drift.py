"""
DriftSketch — Windowed correlation drift detector over compressed metrics.

Detects statistical drift and co-occurring metric changes across time windows.
NOT causal debugging. NOT tracing. NOT root cause analysis.

What it detects:
    - "redis latency increased"              ✅ yes
    - "error rate and redis moved together"   ✅ yes
    - "redis caused the error spike"          ❌ no (correlation ≠ causation)
    - "trace request X → response Y"         ❌ no (no identity preservation)

Architecture:
    Each dimension has TWO StreamLogs (double-buffered):
    - previous: snapshot of the last complete window (frozen)
    - current:  accumulating data for the active window

    On window rotation: current becomes previous (frozen), new current starts.
    drift() compares current vs previous.
    correlations() finds dimensions that drifted together.

Usage:
    from sketchlog.drift import DriftSketch

    ds = DriftSketch(window="5m")
    ds.add("api_latency", 42.0)
    ds.add("redis_latency", 8.0)

    # What changed vs last window?
    alerts = ds.drift()

    # What moved together?
    report = ds.correlations()

Guarantees:
    - Detects statistical drift, not causality
    - Correlation ≠ causation (documented, not hidden)
    - Bounded by sketch error (DDSketch alpha) + sampling variance
    - Sensitive to window size selection
    - Memory: O(dimensions) — ~14 KB per tracked dimension
"""

import time as _time
import math
from typing import Any, Dict, List, Tuple, Union, DefaultDict, Iterable, TypedDict
import threading
from collections import defaultdict

class DriftResult(TypedDict):
    dimension: str
    current_p99: float
    previous_p99: float
    drift_ratio: float
    drift_pct: float
    direction: str

class CorrelationResult(TypedDict):
    pair: Tuple[str, str]
    direction: str
    score: float
    a_drift_pct: float
    b_drift_pct: float

from sketchlog.facade import StreamLog
from sketchlog.windowed import _parse_window


class DriftSketch:
    """
    Windowed correlation drift detector over compressed metrics.

    Maintains per-dimension StreamLogs with double-buffered windows.
    On window rotation: current -> previous (frozen), new empty current.

    drift() detects: "this metric changed significantly"
    correlations() detects: "these metrics changed together"

    Does NOT detect causality or reconstruct event sequences.
    """

    def __init__(self, window: Union[str, int, float] = "5m", relative_accuracy: float = 0.01, hll_precision: int = 8, cms_width: int = 256, cms_depth: int = 3) -> None:
        """
        Args:
            window: Time window for drift comparison (e.g., "5m", "30s", "1h")
            relative_accuracy: DDSketch alpha per dimension
            hll_precision: HLL precision per dimension
            cms_width: CMS width per dimension
            cms_depth: CMS depth per dimension
        """
        self._window_seconds = _parse_window(window)
        if self._window_seconds < 1e-9:
            raise ValueError(f"Window {self._window_seconds}s is too small (sub-nanosecond resolution).")
        self._window_ns = math.ceil(self._window_seconds * 1_000_000_000)
        self._window_str = window
        self._sk_kwargs: Dict[str, Any] = dict(
            relative_accuracy=relative_accuracy,
            hll_precision=hll_precision,
            cms_width=cms_width,
            cms_depth=cms_depth,
        )

        self._current: Dict[str, StreamLog] = {}       # name -> StreamLog (active window)
        self._previous: Dict[str, StreamLog] = {}      # name -> StreamLog (frozen previous window)
        self._window_start: Dict[str, int] = {}    # name -> monotonic time (ns)
        self._event_counts: DefaultDict[str, int] = defaultdict(int)
        self._lock = threading.RLock()

    def _get_or_create(self, name: str) -> None:
        if name not in self._current:
            self._current[name] = StreamLog(**self._sk_kwargs)
            self._previous[name] = StreamLog(**self._sk_kwargs)
            self._window_start[name] = _time.monotonic_ns()

    def _maybe_rotate(self, name: str) -> None:
        """Rotate window if expired. Previous becomes frozen snapshot."""
        now_ns = _time.monotonic_ns()
        elapsed_ns = now_ns - self._window_start[name]

        if elapsed_ns >= self._window_ns:
            windows_elapsed = elapsed_ns // self._window_ns

            if windows_elapsed >= 2:
                self._previous[name] = StreamLog(**self._sk_kwargs)  # empty
            else:
                self._previous[name] = self._current[name]  # freeze

            self._current[name] = StreamLog(**self._sk_kwargs)  # fresh
            self._window_start[name] += windows_elapsed * self._window_ns

    def rotate_all(self) -> None:
        """Force window rotation for all dimensions.

        Useful for testing or manual window management.
        After rotation, previous holds the frozen snapshot and
        current starts fresh.
        """
        with self._lock:
            for name in list(self._current.keys()):
                self._previous[name] = self._current[name]
                self._current[name] = StreamLog(**self._sk_kwargs)
                self._window_start[name] = _time.monotonic_ns()

    def add(self, dimension: str, value: float) -> None:
        """Add a metric observation to a named dimension.

        Args:
            dimension: Name of the metric (e.g., "api_latency", "redis_p99")
            value: Numeric observation
        """
        with self._lock:
            self._get_or_create(dimension)
            self._maybe_rotate(dimension)
            sketch = self._current[dimension]
            count_before = sketch.total_events
            sketch.add_latency(value)
            if sketch.total_events > count_before:
                self._event_counts[dimension] += 1

    def add_batch(self, dimension: str, values: Iterable[float]) -> None:
        """Bulk-add observations to a dimension."""
        if not hasattr(values, "__len__"):
            values = list(values)

        with self._lock:
            self._get_or_create(dimension)
            self._maybe_rotate(dimension)
            sketch = self._current[dimension]
            count_before = sketch.total_events
            sketch.add_batch(values)
            self._event_counts[dimension] += (sketch.total_events - count_before)

    # ─── Drift Detection ─────────────────────────────────────────────

    def drift(self, threshold: float = 0.2) -> List[DriftResult]:
        """Detect dimensions where current window p99 differs from previous.

        This is statistical drift detection, not root cause analysis.
        A drift means: "this metric's recent behavior differs from its
        previous window." It does NOT mean anything caused the change.

        Returns list sorted by drift magnitude:
        [{"dimension": "api_latency", "current_p99": 120, "previous_p99": 40,
          "drift_pct": +200.0, "direction": "up"}, ...]

        Args:
            threshold: minimum relative change to report (default 0.2 = 20%)
        """
        with self._lock:
            results: List[DriftResult] = []
            for name in self._current:
                self._maybe_rotate(name)

                curr_count = self._current[name].total_events
                prev_count = self._previous[name].total_events
                if curr_count == 0 or prev_count == 0:
                    continue

                curr_p99 = self._current[name].p99()
                prev_p99 = self._previous[name].p99()

                if prev_p99 == 0 and curr_p99 == 0:
                    continue
                if prev_p99 == 0:
                    results.append({
                        "dimension": name,
                        "current_p99": round(curr_p99, 4),
                        "previous_p99": 0.0,
                        "drift_ratio": float('inf'),
                        "drift_pct": float('inf'),
                        "direction": "new",
                    })
                    continue

                ratio = curr_p99 / prev_p99
                change = abs(ratio - 1.0)

                if change >= threshold:
                    results.append({
                        "dimension": name,
                        "current_p99": round(curr_p99, 4),
                        "previous_p99": round(prev_p99, 4),
                        "drift_ratio": round(ratio, 4),
                        "drift_pct": round((ratio - 1.0) * 100, 2),
                        "direction": "up" if ratio > 1.0 else "down",
                    })

            results.sort(key=lambda x: abs(float(x.get("drift_pct", 0))), reverse=True)
            return results

    # ─── Correlation Detection ────────────────────────────────────────

    def correlations(self, min_events: int = 10) -> List[CorrelationResult]:
        """Find dimensions whose drift co-occurred (same direction + magnitude).

        This detects co-occurring changes, NOT causal relationships.
        High correlation means "these metrics changed together" —
        it does NOT mean one caused the other.

        Returns pairs sorted by correlation score:
        [{"pair": ("api", "redis"), "score": 0.92, "direction": "up"}, ...]
        """
        with self._lock:
            drift_data = {}
            for name in self._current:
                if self._event_counts[name] < min_events:
                    continue
                self._maybe_rotate(name)
                curr_count = self._current[name].total_events
                prev_count = self._previous[name].total_events
                if curr_count == 0 or prev_count == 0:
                    continue
                curr = self._current[name].p99()
                prev = self._previous[name].p99()
                if prev > 0:
                    drift_data[name] = curr / prev

            if len(drift_data) < 2:
                return []

            names = sorted(drift_data.keys())
            pairs: List[CorrelationResult] = []
            for i in range(len(names)):
                for j in range(i + 1, len(names)):
                    a_r = drift_data[names[i]]
                    b_r = drift_data[names[j]]
                    a_dir = 1 if a_r > 1 else -1
                    b_dir = 1 if b_r > 1 else -1
                    a_chg = abs(a_r - 1.0)
                    b_chg = abs(b_r - 1.0)

                    if a_chg + b_chg < 0.01:
                        continue

                    sim = 1.0 - abs(a_chg - b_chg) / (a_chg + b_chg)
                    if a_dir == b_dir:
                        score = sim
                        direction = "up" if a_dir > 0 else "down"
                    else:
                        score = -sim
                        direction = "mixed"

                    if abs(score) > 0.1:
                        pairs.append({
                            "pair": (names[i], names[j]),
                            "score": round(score, 4),
                            "direction": direction,
                            "a_drift_pct": round((a_r - 1) * 100, 2),
                            "b_drift_pct": round((b_r - 1) * 100, 2),
                        })

            pairs.sort(key=lambda x: abs(float(x["score"])), reverse=True)
            return pairs

    # ─── Summary ──────────────────────────────────────────────────────

    def summary(self) -> Dict[str, Any]:
        """Overview of all tracked dimensions."""
        with self._lock:
            dims = []
            for name in sorted(self._current.keys()):
                self._maybe_rotate(name)
                dims.append({
                    "dimension": name,
                    "events": self._event_counts[name],
                    "current_p99": round(self._current[name].p99(), 4),
                    "previous_p99": round(self._previous[name].p99(), 4),
                })
            return {
                "dimensions": len(dims),
                "total_events": sum(d["events"] for d in dims),
                "window": self._window_str,
                "metrics": dims,
            }

    @property
    def dimensions(self) -> List[str]:
        """List of tracked dimension names."""
        with self._lock:
            return list(self._current.keys())

    def memory_bytes(self) -> int:
        """Total memory across all dimensions (current + previous)."""
        with self._lock:
            return sum(self._current[n].memory_bytes() + self._previous[n].memory_bytes()
                       for n in self._current)

    def memory_kb(self) -> float:
        return self.memory_bytes() / 1024.0

    def __repr__(self) -> str:
        with self._lock:
            n = len(self._current)
            total = sum(self._event_counts.values())
            return (f"DriftSketch(dimensions={n}, events={total:,}, "
                    f"window={self._window_str}, memory={self.memory_kb():.1f} KB)")
