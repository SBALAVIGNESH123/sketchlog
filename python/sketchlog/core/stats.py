from typing import Union, NamedTuple

EventKey = Union[str, bytes, int]

class Stats(NamedTuple):
    events: int
    memory_bytes: int
    memory_kb: float
    latency_p50: float
    latency_p99: float
    latency_p999: float
    unique_count: int
