import math
import struct
import hashlib
from typing import List, Union
from sketchlog.core.stats import EventKey

MAX_INT64 = (1 << 63) - 1


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
        if type(count) is not int or count <= 0:
            raise ValueError("Event count must be strictly positive")
        if count > MAX_INT64:
            raise OverflowError("Event count exceeds int64 capacity")

        if isinstance(item, int):
            key = item
        else:
            key = self._key_from_bytes(item)

        if self._total > MAX_INT64 - count:
            raise OverflowError("CountMinSketch: total_count overflow")

        columns = [
            self._hash(key, self._seeds[i]) % self._width
            for i in range(self._depth)
        ]
        if any(self._table[i][col] > MAX_INT64 - count
               for i, col in enumerate(columns)):
            raise OverflowError("CountMinSketch: bucket counter overflow")

        for i, col in enumerate(columns):
            self._table[i][col] += count
        self._total += count

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
