import sys
import time as _time
from typing import Any, Dict, Iterable, List, Optional, Tuple, Union
import json

from sketchlog.core.stats import Stats, EventKey
from sketchlog.core.ddsketch import DDSketch
from sketchlog.core.hll import HyperLogLog
from sketchlog.core.cms import CountMinSketch

try:
    import _sketchlog_cpp as _cpp  # pyright: ignore[reportMissingImports]
    HAS_CPP = True
except ImportError:
    _cpp = None
    HAS_CPP = False

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
