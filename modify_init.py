import sys
import json

with open('python/sketchlog/__init__.py', 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Rename StreamLog to _PythonStreamLog
content = content.replace('class StreamLog:', 'class _PythonStreamLog:')
content = content.replace('def merge(self, other: "StreamLog") -> None:', 'def merge(self, other: "_PythonStreamLog") -> None:')
content = content.replace('def from_dict(cls, data: Dict[str, Any]) -> "StreamLog":', 'def from_dict(cls, data: Dict[str, Any]) -> "_PythonStreamLog":')
content = content.replace('def from_json(cls, json_str: str) -> "StreamLog":', 'def from_json(cls, json_str: str) -> "_PythonStreamLog":')
content = content.replace('def load(cls, path: str) -> "StreamLog":', 'def load(cls, path: str) -> "_PythonStreamLog":')

wrapper_code = '''
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
        if HAS_CPP and not deterministic:
            self._backend = _cpp.StreamLog(relative_accuracy, hll_precision, cms_width, cms_depth)
        else:
            self._backend = _PythonStreamLog(relative_accuracy, hll_precision, cms_width, cms_depth, deterministic)

    def add_latency(self, value: float) -> None:
        self._backend.add_latency(value)

    def add_batch(self, values: Iterable[float]) -> None:
        self._backend.add_batch(values)

    def percentile(self, q: float) -> float:
        return self._backend.percentile(q)

    def p50(self) -> float: return self._backend.p50()
    def p95(self) -> float: return self._backend.p95()
    def p99(self) -> float: return self._backend.p99()
    def p999(self) -> float: return self._backend.p999()

    def add_event(self, name: EventKey, count: int = 1) -> None:
        self._backend.add_event(name, count)

    def event_count(self, event_name: Union[str, int, bytes]) -> int:
        return self._backend.event_count(event_name)

    def add_unique(self, item: Union[str, bytes, int]) -> None:
        self._backend.add_unique(item)

    def unique_count(self) -> int:
        return self._backend.unique_count()

    @property
    def total_events(self) -> int:
        if callable(getattr(self._backend, "total_events", None)):
            return self._backend.total_events()
        return getattr(self._backend, "total_events", 0)

    def memory_bytes(self) -> int:
        return self._backend.memory_bytes()

    def memory_kb(self) -> float:
        return self._backend.memory_kb()

    def memory_breakdown(self) -> Dict[str, Any]:
        if hasattr(self._backend, "memory_breakdown"):
            return self._backend.memory_breakdown()
        raise NotImplementedError("memory_breakdown is not supported with the C++ backend.")

    def stats(self) -> Stats:
        res = self._backend.stats()
        if isinstance(res, tuple) and not isinstance(res, Stats):
            return Stats(*res)
        return res

    def reset(self) -> None:
        self._backend.reset()

    def merge(self, other: "StreamLog") -> None:
        self._backend.merge(other._backend)

    def to_dict(self) -> Dict[str, Any]:
        if HAS_CPP and not self._deterministic:
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
'''

idx = content.find('class ThreadSafeStreamLog:')
content = content[:idx] + wrapper_code + '\n\n' + content[idx:]

with open('python/sketchlog/__init__.py', 'w', encoding='utf-8') as f:
    f.write(content)
