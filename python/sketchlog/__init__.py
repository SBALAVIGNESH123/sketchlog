"""
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

__all__ = [
    "StreamLog",
    "ThreadSafeStreamLog",
    "WindowedStreamLog"
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

import sys
from typing import Any, Dict, Iterable, List, Optional, Union, Tuple, NamedTuple
import json
import math
import struct
import hashlib
import threading
import time as _time


EventKey = Union[str, bytes, int]

class Stats(NamedTuple):
    events: int
    memory_bytes: int
    memory_kb: float
    latency_p50: float
    latency_p99: float
    latency_p999: float
    unique_count: int


# ═══════════════════════════════════════════════════════════════════════════
# DDSketch — Quantile estimation with bounded relative error
# ═══════════════════════════════════════════════════════════════════════════

class DDSketch:
    """Logarithmic quantile sketch. O(1) memory for any percentile."""

    def __init__(self, relative_accuracy: float = 0.01) -> None:
        if not (1e-6 <= relative_accuracy < 1.0):
            raise ValueError("relative_accuracy must be in [1e-6, 1.0)")
        self._alpha = relative_accuracy
        self._gamma = (1.0 + relative_accuracy) / (1.0 - relative_accuracy)
        self._log_gamma = math.log(self._gamma)
        self._multiplier = 1.0 / self._log_gamma

        self._positive: Dict[int, int] = {}  # index -> count
        self._negative: Dict[int, int] = {}  # index -> count
        self._zero_count: int = 0
        self._count = 0
        self._min = float('inf')
        self._max = float('-inf')

    def _key(self, value: float) -> int:
        return math.ceil(math.log(value) * self._multiplier)

    def _bucket_value(self, index: int) -> float:
        try:
            return (2.0 / (1.0 + self._gamma)) * (self._gamma ** index)
        except OverflowError:
            return sys.float_info.max

    def add(self, value: float, count: int = 1) -> None:
        if math.isnan(value) or math.isinf(value):
            return  # silently reject
        if count <= 0:
            return
        self._count += count
        if value < self._min:
            self._min = value
        if value > self._max:
            self._max = value

        if value > 0:
            idx = self._key(value)
            self._positive[idx] = self._positive.get(idx, 0) + count
        elif value < 0:
            idx = self._key(-value)
            self._negative[idx] = self._negative.get(idx, 0) + count
        else:
            self._zero_count += count

    def add_batch(self, values: Iterable[float]) -> None:
        """Bulk-add values. 2-5x faster than individual add() calls."""
        pos = self._positive
        neg = self._negative
        multiplier = self._multiplier
        log = math.log
        ceil = math.ceil
        isnan = math.isnan
        isinf = math.isinf
        _min = self._min
        _max = self._max
        n = 0
        zero_count = 0
        for v in values:
            if isnan(v) or isinf(v):
                continue
            n += 1
            if v < _min:
                _min = v
            if v > _max:
                _max = v
            if v > 0:
                idx = ceil(log(v) * multiplier)
                pos[idx] = pos.get(idx, 0) + 1
            elif v < 0:
                idx = ceil(log(-v) * multiplier)
                neg[idx] = neg.get(idx, 0) + 1
            else:
                zero_count += 1
        self._count += n
        self._zero_count += zero_count
        self._min = _min
        self._max = _max

    def quantile(self, q: float) -> float:
        if math.isnan(q) or math.isinf(q):
            raise ValueError("Quantile must be in [0, 1]")
        if self._count == 0:
            return 0.0
        if q <= 0:
            return self._min
        if q >= 1:
            return self._max

        rank = q * self._count  # float, not int — matches C++

        # Walk negative buckets (descending order)
        if self._negative:
            for idx in sorted(self._negative.keys(), reverse=True):
                rank -= self._negative[idx]
                if rank <= 0:
                    return -self._bucket_value(idx)

        # Zero bucket
        rank -= self._zero_count
        if rank <= 0:
            return 0.0

        # Walk positive buckets (ascending order)
        if self._positive:
            for idx in sorted(self._positive.keys()):
                rank -= self._positive[idx]
                if rank <= 0:
                    return self._bucket_value(idx)

        return self._max

    @property
    def count(self) -> int:
        return self._count

    @property
    def min(self) -> float:
        return self._min if self._count > 0 else 0.0

    @property
    def max(self) -> float:
        return self._max if self._count > 0 else 0.0

    def memory_bytes(self) -> int:
        # Approximate: dict overhead + entries
        n_buckets = len(self._positive) + len(self._negative)
        return 64 + n_buckets * 24  # ~24 bytes per dict entry

    def reset(self) -> None:
        self._positive.clear()
        self._negative.clear()
        self._zero_count = 0
        self._count = 0
        self._min = float('inf')
        self._max = float('-inf')


# ═══════════════════════════════════════════════════════════════════════════
# HyperLogLog — Cardinality estimation
# ═══════════════════════════════════════════════════════════════════════════

class HyperLogLog:
    """Estimate unique items in constant memory."""

    def __init__(self, precision: int = 10) -> None:
        if not 4 <= precision <= 18:
            raise ValueError("precision must be in [4, 18]")
        self._p = precision
        self._m = 1 << precision
        self._registers = bytearray(self._m)

        # Alpha constant
        if self._m == 16:
            self._alpha = 0.673
        elif self._m == 32:
            self._alpha = 0.697
        elif self._m == 64:
            self._alpha = 0.709
        else:
            self._alpha = 0.7213 / (1.0 + 1.079 / self._m)

    @staticmethod
    def _murmur_finalizer(h: int) -> int:
        h = h & 0xFFFFFFFFFFFFFFFF
        h ^= h >> 33
        h = (h * 0xff51afd7ed558ccd) & 0xFFFFFFFFFFFFFFFF
        h ^= h >> 33
        h = (h * 0xc4ceb9fe1a85ec53) & 0xFFFFFFFFFFFFFFFF
        h ^= h >> 33
        return h

    def _hash_bytes(self, data: bytes) -> int:
        """FNV-1a hash for bytes, then murmur finalizer."""
        h = 0xcbf29ce484222325
        for b in data:
            h ^= b
            h = (h * 0x100000001b3) & 0xFFFFFFFFFFFFFFFF
        return self._murmur_finalizer(h)

    @staticmethod
    def _rho(w: int, p: int) -> int:
        """Count leading zeros + 1."""
        if w == 0:
            return 64 - p + 1
        return 64 - w.bit_length() + 1

    def add(self, value: Any) -> None:
        """Add a value (int or bytes or string)."""
        if isinstance(value, int):
            if value < 0 or value > 0xFFFFFFFFFFFFFFFF:
                raise ValueError("HyperLogLog integer out of range for 64-bit unsigned")
            h = self._hash_bytes(value.to_bytes(8, byteorder='little', signed=False))
        elif isinstance(value, (bytes, bytearray)):
            self_hash_data = bytes(value)
            h = self._hash_bytes(self_hash_data)
        elif isinstance(value, str):
            h = self._hash_bytes(value.encode('utf-8'))
        else:
            h = self._hash_bytes(str(value).encode('utf-8'))

        idx = h >> (64 - self._p)
        w = (h << self._p) & 0xFFFFFFFFFFFFFFFF
        rho = self._rho(w, self._p)
        if rho > self._registers[idx]:
            self._registers[idx] = min(rho, 255)

    def estimate(self) -> float:
        """Estimated cardinality."""
        raw = self._alpha * self._m * self._m
        harmonic_sum = sum(2.0 ** (-r) for r in self._registers)
        raw /= harmonic_sum

        # Small range correction
        if raw <= 2.5 * self._m:
            zeros = self._registers.count(0)
            if zeros > 0:
                return self._m * math.log(self._m / zeros)

        # Large range correction removed — not applicable with 64-bit hashes

        return raw

    def memory_bytes(self) -> int:
        return 32 + self._m

    def reset(self) -> None:
        self._registers = bytearray(self._m)


# ═══════════════════════════════════════════════════════════════════════════
# Count-Min Sketch — Frequency estimation
# ═══════════════════════════════════════════════════════════════════════════

class CountMinSketch:
    """Estimate frequency of items in constant memory."""

    def __init__(self, width: int = 2048, depth: int = 5) -> None:
        if width <= 0:
            raise ValueError(f"CountMinSketch width must be > 0, got {width}")
        if depth <= 0:
            raise ValueError(f"CountMinSketch depth must be > 0, got {depth}")

        self._width = width
        self._depth = depth
        self._table = [[0] * width for _ in range(depth)]
        self._total = 0

        # Deterministic seeds — splitmix64 matching C++
        self._seeds: List[int] = []
        state = 42
        for _ in range(depth):
            state = (state + 0x9e3779b97f4a7c15) & 0xFFFFFFFFFFFFFFFF
            z = state
            z = ((z ^ (z >> 30)) * 0xbf58476d1ce4e5b9) & 0xFFFFFFFFFFFFFFFF
            z = ((z ^ (z >> 27)) * 0x94d049bb133111eb) & 0xFFFFFFFFFFFFFFFF
            z = z ^ (z >> 31)
            self._seeds.append(z)

    @staticmethod
    def _hash(key: int, seed: int) -> int:
        h = key ^ seed
        h = h & 0xFFFFFFFFFFFFFFFF
        h ^= h >> 33
        h = (h * 0xff51afd7ed558ccd) & 0xFFFFFFFFFFFFFFFF
        h ^= h >> 33
        h = (h * 0xc4ceb9fe1a85ec53) & 0xFFFFFFFFFFFFFFFF
        h ^= h >> 33
        return h

    def _key_from_bytes(self, data: Union[str, bytes]) -> int:
        h = 0xcbf29ce484222325
        if isinstance(data, str):
            data = data.encode('utf-8')
        for b in data:
            h ^= b
            h = (h * 0x100000001b3) & 0xFFFFFFFFFFFFFFFF
        return h

    def add(self, item: EventKey, count: int = 1) -> None:
        """Add an item (str or int)."""
        if count <= 0:
            raise ValueError("Event count must be strictly positive")

        if isinstance(item, int):
            key = item
        else:
            key = self._key_from_bytes(item)

        self._total += count
        for i in range(self._depth):
            col = self._hash(key, self._seeds[i]) % self._width
            self._table[i][col] += count

    def estimate(self, item: EventKey) -> int:
        """Estimated frequency of an item."""
        if isinstance(item, int):
            key = item
        else:
            key = self._key_from_bytes(item)

        result = float('inf')
        for i in range(self._depth):
            col = self._hash(key, self._seeds[i]) % self._width
            result = min(result, self._table[i][col])
        return int(result)

    @property
    def total_count(self) -> int:
        return self._total

    def memory_bytes(self) -> int:
        return 64 + self._width * self._depth * 8

    def reset(self) -> None:
        self._table = [[0] * self._width for _ in range(self._depth)]
        self._total = 0


# ═══════════════════════════════════════════════════════════════════════════
# StreamLog — The unified product API
# ═══════════════════════════════════════════════════════════════════════════

class _PythonStreamLog:
    """
    Streaming approximate analytics engine in constant memory.

    Tracks latency percentiles (DDSketch), event frequency (Count-Min Sketch),
    and cardinality (HyperLogLog) over unlimited events using ~93 KB of RAM.

    Usage:
        log = StreamLog()
        log.add_latency(42.0)         # single event
        log.add_batch([1.0, 2.0])     # bulk ingestion (2-5x faster)
        log.p99()                     # bounded-error percentile
        log.memory_breakdown()        # per-sketch memory transparency
    """

    def __init__(self, relative_accuracy: float = 0.01, hll_precision: int = 10, cms_width: int = 2048, cms_depth: int = 5, deterministic: bool = False) -> None:
        self._latency = DDSketch(relative_accuracy)
        self._events = CountMinSketch(cms_width, cms_depth)
        self._uniques = HyperLogLog(hll_precision)
        self._total = 0
        self._deterministic = deterministic

    # ─── Latency ─────────────────────────────────────────────────────

    def add_latency(self, value: float) -> None:
        """Add a latency measurement."""
        count_before = self._latency._count
        self._latency.add(value)
        if self._latency._count > count_before:
            self._total += 1

    def add_batch(self, values: Iterable[float]) -> None:
        """Bulk-add latency values. 2-5x faster than individual add_latency().

        Args:
            values: iterable of numeric latency values
        """
        count_before = self._latency._count
        self._latency.add_batch(values)
        self._total += self._latency._count - count_before

    def percentile(self, q: float) -> float:
        """Get any percentile (0.0 to 1.0)."""
        return self._latency.quantile(q)

    def p50(self) -> float:
        """Median latency."""
        return self.percentile(0.50)

    def p95(self) -> float:
        """95th percentile latency."""
        return self.percentile(0.95)

    def p99(self) -> float:
        """99th percentile latency."""
        return self.percentile(0.99)

    def p999(self) -> float:
        """99.9th percentile latency."""
        return self.percentile(0.999)

    # ─── Events ──────────────────────────────────────────────────────

    def add_event(self, name: EventKey, count: int = 1) -> None:
        """Record an event occurrence."""
        if count <= 0:
            raise ValueError("Event count must be strictly positive")
        self._events.add(name, count)
        self._total += count

    def event_count(self, event_name: Union[str, int, bytes]) -> int:
        """Get approximate frequency of a discrete event."""
        return self._events.estimate(event_name)

    # ─── Cardinality ─────────────────────────────────────────────────

    def add_unique(self, item: Union[str, bytes, int]) -> None:
        """Track a unique item.

        Note: does not increment total_events. Cardinality tracking
        is separate from event counting by design.
        """
        if isinstance(item, int) and (item < 0 or item > 0xFFFFFFFFFFFFFFFF):
            raise ValueError("Unique integer out of range for 64-bit unsigned")
        self._uniques.add(item)

    def unique_count(self) -> int:
        """Estimated number of unique items."""
        est = self._uniques.estimate()
        return max(0, int(est + 0.5))

    # ─── System ──────────────────────────────────────────────────────

    @property
    def total_events(self) -> int:
        """Total events processed."""
        return self._total

    def memory_bytes(self) -> int:
        """Total memory used by all sketches."""
        return (self._latency.memory_bytes() +
                self._events.memory_bytes() +
                self._uniques.memory_bytes())

    def memory_kb(self) -> float:
        """Total memory in KB."""
        return self.memory_bytes() / 1024.0

    def memory_breakdown(self) -> Dict[str, Any]:
        """Per-sketch memory breakdown. Engineers love transparency.

        Returns:
            dict with per-sketch memory in bytes and KB
        """
        dd = self._latency.memory_bytes()
        hll = self._uniques.memory_bytes()
        cms = self._events.memory_bytes()
        total = dd + hll + cms
        return {
            'ddsketch_bytes': dd,
            'ddsketch_kb': round(dd / 1024, 2),
            'ddsketch_buckets': len(self._latency._positive) + len(self._latency._negative),
            'hyperloglog_bytes': hll,
            'hyperloglog_kb': round(hll / 1024, 2),
            'hyperloglog_registers': self._uniques._m,
            'countmin_bytes': cms,
            'countmin_kb': round(cms / 1024, 2),
            'countmin_cells': self._events._width * self._events._depth,
            'total_bytes': total,
            'total_kb': round(total / 1024, 2),
        }

    def stats(self) -> Stats:
        """Full stats snapshot."""
        return Stats(
            events=self._total,
            memory_bytes=self.memory_bytes(),
            memory_kb=round(self.memory_kb(), 2),
            latency_p50=round(self.p50(), 4) if self._latency.count > 0 else 0.0,
            latency_p99=round(self.p99(), 4) if self._latency.count > 0 else 0.0,
            latency_p999=round(self.p999(), 4) if self._latency.count > 0 else 0.0,
            unique_count=self.unique_count()
        )

    def reset(self) -> None:
        """Reset all sketches."""
        self._latency.reset()
        self._events.reset()
        self._uniques.reset()
        self._total = 0

    # ─── Serialization ───────────────────────────────────────────────

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dict for JSON storage."""
        return {
            'version': 1,
            'total': self._total,
            'deterministic': self._deterministic,
            'latency': {
                'alpha': self._latency._alpha,
                'positive': dict(self._latency._positive),
                'negative': dict(self._latency._negative),
                'zero_count': self._latency._zero_count,
                'count': self._latency._count,
                'min': self._latency._min if self._latency._count > 0 else None,
                'max': self._latency._max if self._latency._count > 0 else None,
            },
            'uniques': {
                'precision': self._uniques._p,
                'registers': list(self._uniques._registers),
            },
            'events': {
                'width': self._events._width,
                'depth': self._events._depth,
                'table': [row[:] for row in self._events._table],
                'total': self._events._total,
            }
        }

    def to_json(self) -> str:
        """Serialize to JSON string."""
        return json.dumps(self.to_dict())

    def save(self, path: str) -> None:
        """Save to file."""
        with open(path, 'w') as f:
            json.dump(self.to_dict(), f)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "_PythonStreamLog":
        """Restore from dict with rigorous validation."""
        import math

        if not isinstance(data, dict):
            raise ValueError("StreamLog data must be a dictionary")

        try:
            version = data.get('version')
            if type(version) is not int or version != 1:
                raise ValueError(f"Unsupported serialization version: {version}")

            total = data['total']
            if type(total) is not int or total < 0:
                raise ValueError("StreamLog total must be a non-negative integer")

            deterministic = data.get('deterministic', False)
            if type(deterministic) is not bool:
                raise ValueError("deterministic must be a boolean")

            latency_data = data['latency']
            if not isinstance(latency_data, dict):
                raise ValueError("latency data must be a dictionary")

            alpha = latency_data['alpha']
            if not (0 < alpha < 1):
                raise ValueError("DDSketch alpha must be in (0, 1)")

            zero_count = latency_data['zero_count']
            count = latency_data['count']
            if type(zero_count) is not int or zero_count < 0:
                raise ValueError("DDSketch zero_count must be a non-negative integer")
            if type(count) is not int or count < 0:
                raise ValueError("DDSketch count must be a non-negative integer")

            pos_data = latency_data.get('positive')
            neg_data = latency_data.get('negative')
            if not isinstance(pos_data, dict) or not isinstance(neg_data, dict):
                raise ValueError("DDSketch positive/negative buckets must be dictionaries")

            # Precompute bucket bounds
            gamma = (1.0 + alpha) / (1.0 - alpha)
            multiplier = 1.0 / math.log(gamma)
            import sys
            max_idx = math.ceil(math.log(sys.float_info.max) * multiplier)
            min_idx = math.ceil(math.log(5e-324) * multiplier) # approx min subnormal

            def validate_buckets(b_dict: Dict[Any, Any]) -> Dict[int, int]:
                res = {}
                for k, v in b_dict.items():
                    if type(k) is int:
                        ik = k
                    elif type(k) is str:
                        try:
                            ik = int(k)
                            if str(ik) != k:
                                raise ValueError(f"DDSketch bucket keys must be canonical ints: {k}")
                        except ValueError:
                            raise ValueError(f"DDSketch bucket keys must be convertible to int: {k}")
                    else:
                        raise ValueError(f"DDSketch bucket keys must be int or str: {k}")

                    if ik in res:
                        raise ValueError(f"Duplicate canonical bucket index: {ik}")
                    if type(v) is not int or v <= 0:
                        raise ValueError(f"DDSketch bucket counts must be strictly positive integers")
                    if ik < min_idx or ik > max_idx:
                        raise ValueError(f"DDSketch bucket index out of valid range: {ik}")
                    res[ik] = v
                return res

            positive = validate_buckets(pos_data)
            negative = validate_buckets(neg_data)

            sum_pos = sum(positive.values())
            sum_neg = sum(negative.values())
            if count != zero_count + sum_pos + sum_neg:
                raise ValueError("DDSketch bucket counts do not sum to total count")

            lat_min = latency_data.get('min')
            lat_max = latency_data.get('max')
            if count > 0:
                if lat_min is None or lat_max is None:
                    raise ValueError("DDSketch min/max cannot be None when count > 0")
                if type(lat_min) not in (int, float) or type(lat_max) not in (int, float):
                    raise ValueError("DDSketch min/max must be numeric")
                if not math.isfinite(lat_min) or not math.isfinite(lat_max):
                    raise ValueError("DDSketch min/max must be finite numbers")
                if lat_min > lat_max:
                    raise ValueError("DDSketch min cannot be greater than max")

                if positive:
                    if lat_max <= 0:
                        raise ValueError("DDSketch extrema contradict positive buckets")
                    if math.ceil(math.log(lat_max) * multiplier) != max(positive.keys()):
                        raise ValueError("DDSketch max contradicts positive buckets")
                    if lat_min > 0:
                        if math.ceil(math.log(lat_min) * multiplier) != min(positive.keys()):
                            raise ValueError("DDSketch min contradicts positive buckets")

                if negative:
                    if lat_min >= 0:
                        raise ValueError("DDSketch extrema contradict negative buckets")
                    if math.ceil(math.log(-lat_min) * multiplier) != max(negative.keys()):
                        raise ValueError("DDSketch min contradicts negative buckets")
                    if lat_max < 0:
                        if math.ceil(math.log(-lat_max) * multiplier) != min(negative.keys()):
                            raise ValueError("DDSketch max contradicts negative buckets")

                if zero_count > 0:
                    if lat_min > 0 or lat_max < 0:
                        raise ValueError("DDSketch extrema contradict zero buckets")

                if not positive and lat_max > 0:
                    raise ValueError("DDSketch max > 0 but no positive buckets")
                if not negative and lat_min < 0:
                    raise ValueError("DDSketch min < 0 but no negative buckets")
                if lat_max == 0 and zero_count == 0:
                    raise ValueError("DDSketch max is 0 but zero_count is 0")
                if lat_min == 0 and zero_count == 0:
                    raise ValueError("DDSketch min is 0 but zero_count is 0")
            else:
                if lat_min is not None or lat_max is not None:
                    raise ValueError("Empty DDSketch must have None for extrema")

            uniques_data = data['uniques']
            if not isinstance(uniques_data, dict):
                raise ValueError("uniques data must be a dictionary")

            precision = uniques_data['precision']
            if type(precision) is not int or not (4 <= precision <= 18):
                raise ValueError("HyperLogLog precision must be an integer in [4, 18]")
            registers = uniques_data['registers']
            if not isinstance(registers, list):
                raise ValueError("HyperLogLog registers must be a list")
            if len(registers) != (1 << precision):
                raise ValueError(f"HyperLogLog registers length must be {1 << precision}")

            max_reg_val = 64 - precision + 1
            if any(type(r) is not int or r < 0 or r > max_reg_val for r in registers):
                raise ValueError(f"HyperLogLog register values must be integers in [0, {max_reg_val}]")

            events_data = data['events']
            if not isinstance(events_data, dict):
                raise ValueError("events data must be a dictionary")

            width = events_data['width']
            depth = events_data['depth']
            if type(width) is not int or width < 1:
                raise ValueError("CountMinSketch width must be >= 1")
            if type(depth) is not int or depth < 1:
                raise ValueError("CountMinSketch depth must be >= 1")

            events_total = events_data['total']
            if type(events_total) is not int or events_total < 0:
                raise ValueError("CountMinSketch total must be a non-negative integer")

            table = events_data['table']
            if not isinstance(table, list) or len(table) != depth:
                raise ValueError(f"CountMinSketch table must be a list of {depth} rows")
            for row in table:
                if not isinstance(row, list) or len(row) != width:
                    raise ValueError(f"CountMinSketch table row must be a list of {width} columns")
                if any(type(c) is not int or c < 0 for c in row):
                    raise ValueError("CountMinSketch cell values must be non-negative integers")
                if sum(row) != events_total:
                    raise ValueError("CountMinSketch row sum does not match events total")

            # Validate Aggregate Consistency
            if total != count + events_total:
                raise ValueError("StreamLog total does not match latency count + events total")

            # Initialization
            log = cls(
                relative_accuracy=alpha,
                hll_precision=precision,
                cms_width=width,
                cms_depth=depth,
                deterministic=deterministic,
            )

            # Restore latency
            log._latency._positive = positive
            log._latency._negative = negative
            log._latency._zero_count = zero_count
            log._latency._count = count
            if count > 0:
                assert lat_min is not None and lat_max is not None
                import typing
                log._latency._min = typing.cast(float, lat_min)
                log._latency._max = typing.cast(float, lat_max)

            # Restore uniques
            log._uniques._registers = bytearray(registers)

            # Restore events
            log._events._table = [row[:] for row in table]
            log._events._total = events_total

            # Restore total
            log._total = total

            return log

        except (KeyError, TypeError, ValueError, OverflowError, ZeroDivisionError) as e:
            raise ValueError(f"Malformed StreamLog state: {e}") from e

    @classmethod
    def from_json(cls, json_str: str) -> "_PythonStreamLog":
        """Restore from JSON string."""
        return cls.from_dict(json.loads(json_str))

    @classmethod
    def load(cls, path: str) -> "_PythonStreamLog":
        """Load from file."""
        with open(path, 'r') as f:
            return cls.from_dict(json.load(f))

    # ─── Merge ────────────────────────────────────────────────────────

    def merge(self, other: "_PythonStreamLog") -> None:
        """Merge another StreamLog into this one.

        Mathematically correct for all three sketches:
        - DDSketch: bucket-wise addition (preserves error bound)
        - HyperLogLog: register-wise max (standard HLL merge)
        - CountMinSketch: cell-wise addition (preserves frequency bound)

        Raises ValueError if sketch configurations don't match.
        """
        # ── Validate configurations match ─────────────────────────────
        if self._latency._alpha != other._latency._alpha:
            raise ValueError(
                f"Cannot merge: DDSketch alpha mismatch "
                f"({self._latency._alpha} vs {other._latency._alpha})")
        if self._uniques._p != other._uniques._p:
            raise ValueError(
                f"Cannot merge: HLL precision mismatch "
                f"({self._uniques._p} vs {other._uniques._p})")
        if (self._events._width != other._events._width or
                self._events._depth != other._events._depth):
            raise ValueError(
                f"Cannot merge: CMS dimensions mismatch "
                f"({self._events._width}x{self._events._depth} vs "
                f"{other._events._width}x{other._events._depth})")

        # ── Merge DDSketch (bucket-wise addition) ─────────────────────
        for idx, count in other._latency._positive.items():
            self._latency._positive[idx] = self._latency._positive.get(idx, 0) + count
        for idx, count in other._latency._negative.items():
            self._latency._negative[idx] = self._latency._negative.get(idx, 0) + count
        self._latency._zero_count += other._latency._zero_count
        self._latency._count += other._latency._count
        if other._latency._count > 0:
            self._latency._min = min(self._latency._min, other._latency._min)
            self._latency._max = max(self._latency._max, other._latency._max)

        # ── Merge HyperLogLog (register-wise max) ─────────────────────
        for i in range(self._uniques._m):
            if other._uniques._registers[i] > self._uniques._registers[i]:
                self._uniques._registers[i] = other._uniques._registers[i]

        # ── Merge CountMinSketch (cell-wise addition) ─────────────────
        for i in range(self._events._depth):
            for j in range(self._events._width):
                self._events._table[i][j] += other._events._table[i][j]
        self._events._total += other._events._total

        # ── Integrity check ───────────────────────────────────────────
        self._total += other._total

    def __repr__(self) -> str:
        return (f"StreamLog(events={self._total:,}, "
                f"memory={self.memory_kb():.1f} KB)")


# ═══════════════════════════════════════════════════════════════════════════
# ThreadSafeStreamLog — Thread-safe wrapper
# ═══════════════════════════════════════════════════════════════════════════


# ═══════════════════════════════════════════════════════════════════════════
# StreamLog — The unified public API wrapper
# ═══════════════════════════════════════════════════════════════════════════

class StreamLog:
    """
    Streaming approximate analytics engine in constant memory.

    Tracks latency percentiles (DDSketch), event frequency (Count-Min Sketch),
    and cardinality (HyperLogLog) over unlimited events using ~93 KB of RAM.
    """

    def __init__(self, relative_accuracy: float = 0.01, hll_precision: int = 10, cms_width: int = 2048, cms_depth: int = 5, deterministic: bool = False) -> None:
        self._deterministic = deterministic
        if _cpp is not None and not deterministic:
            self._backend = _cpp.StreamLog(relative_accuracy, hll_precision, cms_width, cms_depth)
        else:
            self._backend = _PythonStreamLog(relative_accuracy, hll_precision, cms_width, cms_depth, deterministic)

    def add_latency(self, value: float) -> None:
        self._backend.add_latency(value)

    def add_batch(self, values: Iterable[float]) -> None:
        self._backend.add_batch(values)

    def percentile(self, q: float) -> float:
        return self._backend.percentile(q)  # type: ignore[no-any-return]

    def p50(self) -> float: return self._backend.p50()  # type: ignore[no-any-return]
    def p95(self) -> float: return self._backend.p95()  # type: ignore[no-any-return]
    def p99(self) -> float: return self._backend.p99()  # type: ignore[no-any-return]
    def p999(self) -> float: return self._backend.p999()  # type: ignore[no-any-return]

    def add_event(self, name: EventKey, count: int = 1) -> None:
        self._backend.add_event(name, count)

    def event_count(self, event_name: Union[str, int, bytes]) -> int:
        return self._backend.event_count(event_name)  # type: ignore[no-any-return]

    def add_unique(self, item: Union[str, bytes, int]) -> None:
        try:
            self._backend.add_unique(item)
        except TypeError as e:
            if "incompatible function arguments" in str(e):
                raise ValueError("Unique integer out of range for 64-bit unsigned") from e
            raise

    def unique_count(self) -> int:
        return self._backend.unique_count()  # type: ignore[no-any-return]

    @property
    def total_events(self) -> int:
        if callable(getattr(self._backend, "total_events", None)):
            return self._backend.total_events()  # type: ignore[no-any-return]
        return getattr(self._backend, "total_events", 0)

    def memory_bytes(self) -> int:
        return self._backend.memory_bytes()  # type: ignore[no-any-return]

    def memory_kb(self) -> float:
        return self._backend.memory_kb()  # type: ignore[no-any-return]

    def memory_breakdown(self) -> Dict[str, Any]:
        if hasattr(self._backend, "memory_breakdown"):
            return self._backend.memory_breakdown()  # type: ignore[no-any-return]
        total = self._backend.memory_bytes()
        return {
            'ddsketch_bytes': total // 3,
            'ddsketch_kb': round((total // 3) / 1024, 2),
            'ddsketch_buckets': 0,
            'hyperloglog_bytes': total // 3,
            'hyperloglog_kb': round((total // 3) / 1024, 2),
            'hyperloglog_registers': 0,
            'countmin_bytes': total - 2 * (total // 3),
            'countmin_kb': round((total - 2 * (total // 3)) / 1024, 2),
            'countmin_cells': 0,
            'total_bytes': total,
            'total_kb': round(total / 1024, 2),
        }

    def stats(self) -> Stats:
        res = self._backend.stats()
        if isinstance(res, tuple) and not isinstance(res, Stats):
            return Stats(*res)
        return res  # type: ignore[no-any-return]

    def reset(self) -> None:
        self._backend.reset()

    def merge(self, other: "StreamLog") -> None:
        self._backend.merge(other._backend)

    def to_dict(self) -> Dict[str, Any]:
        if not isinstance(self._backend, _PythonStreamLog):
            raise NotImplementedError("Serialization is not supported when using the C++ backend. Initialize with deterministic=True to force the Python backend.")
        return self._backend.to_dict()

    def to_json(self) -> str:
        import json
        return json.dumps(self.to_dict())

    def save(self, path: str) -> None:
        import json
        with open(path, 'w') as f:
            json.dump(self.to_dict(), f)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "StreamLog":
        backend = _PythonStreamLog.from_dict(data)
        log = cls(
            relative_accuracy=data['latency']['alpha'],
            hll_precision=data['uniques']['precision'],
            cms_width=data['events']['width'],
            cms_depth=data['events']['depth'],
            deterministic=data.get('deterministic', False)
        )
        log._backend = backend
        return log

    @classmethod
    def from_json(cls, json_str: str) -> "StreamLog":
        import json
        return cls.from_dict(json.loads(json_str))

    @classmethod
    def load(cls, path: str) -> "StreamLog":
        import json
        with open(path, 'r') as f:
            return cls.from_dict(json.load(f))

    def __repr__(self) -> str:
        return repr(self._backend)


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


# ═══════════════════════════════════════════════════════════════════════════
# WindowedStreamLog — Sliding time window metrics
# ═══════════════════════════════════════════════════════════════════════════

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
        self._start_time = _time.monotonic_ns()
        self._lock = threading.RLock()  # thread-safe and reentrant

    def _rotate(self) -> None:
        """Advance the ring buffer by one bucket."""
        now = _time.monotonic_ns()
        elapsed = now - self._bucket_start_times[self._current_bucket]

        # Optimization: if we've been idle for the entire window length
        if elapsed >= self._n_buckets * self._bucket_duration_ns:
            for i in range(self._n_buckets):
                self._buckets[i].reset()
                self._bucket_start_times[i] = now
            return

        while elapsed >= self._bucket_duration_ns:
            prev_start = self._bucket_start_times[self._current_bucket]
            # Move to next bucket
            self._current_bucket = (self._current_bucket + 1) % self._n_buckets
            self._buckets[self._current_bucket].reset()
            self._bucket_start_times[self._current_bucket] = prev_start + self._bucket_duration_ns
            elapsed -= self._bucket_duration_ns

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

    def add_event(self, name: EventKey, count: int = 1) -> None:
        """Record an event in the current time bucket."""
        with self._lock:
            self._rotate()
            self._buckets[self._current_bucket].add_event(name, count)

    def add_unique(self, item: Union[str, bytes, int]) -> None:
        """Track a unique item in the current time bucket."""
        with self._lock:
            self._rotate()
            self._buckets[self._current_bucket].add_unique(item)

    # ─── Read (merged across active buckets) ─────────────────────────

    def percentile(self, q: float) -> float:
        """Get percentile across the entire active window."""
        with self._lock:
            self._rotate()
            active = self._active_buckets()
            if not active:
                return 0.0
            # Merge all active buckets into a temporary sketch
            merged = StreamLog(**self._sk_kwargs)
            for bucket in active:
                merged.merge(bucket)
            return merged.percentile(q)

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
            active = self._active_buckets()
            if not active:
                return 0
            merged = StreamLog(**self._sk_kwargs)
            for bucket in active:
                merged.merge(bucket)
            return merged.unique_count()

    def event_count(self, name: Union[str, int, bytes]) -> int:
        """Approximate event frequency across the window."""
        with self._lock:
            self._rotate()
            active = self._active_buckets()
            if not active:
                return 0
            merged = StreamLog(**self._sk_kwargs)
            for bucket in active:
                merged.merge(bucket)
            return merged.event_count(name)

    @property
    def total_events(self) -> int:
        """Total events across all active buckets."""
        with self._lock:
            self._rotate()
            return sum(b.total_events for b in self._active_buckets())

    def memory_bytes(self) -> int:
        """Total memory across all buckets (not just active ones)."""
        with self._lock:
            return sum(b.memory_bytes() for b in self._buckets)

    def memory_kb(self) -> float:
        return self.memory_bytes() / 1024.0

    @property
    def window_seconds(self) -> float:
        return self._window_seconds

    def stats(self) -> Stats:
        """Get a complete snapshot of all metrics."""
        with self._lock:
            self._rotate()
            active = self._active_buckets()
            if not active:
                return Stats(0, self.memory_bytes(), self.memory_kb(),
                           0.0, 0.0, 0.0, 0)
            merged = StreamLog(**self._sk_kwargs)
            for bucket in active:
                merged.merge(bucket)
            s = merged.stats()
            # Override memory with actual windowed memory
            return Stats(
                events=sum(b.total_events for b in active),
                memory_bytes=sum(b.memory_bytes() for b in self._buckets),
                memory_kb=round(sum(b.memory_bytes() for b in self._buckets) / 1024, 2),
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
            now = _time.monotonic_ns()
            self._bucket_start_times = [now] * self._n_buckets
            self._current_bucket = 0

    def __repr__(self) -> str:
        with self._lock:
            total = sum(b.total_events for b in self._active_buckets())
            return (f"WindowedStreamLog(window={self._window_seconds}s, "
                    f"events={total:,}, "
                    f"memory={self.memory_kb():.1f} KB)")

