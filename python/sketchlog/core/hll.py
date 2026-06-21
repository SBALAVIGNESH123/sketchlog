import math
import struct
import hashlib
from typing import Any, Union
from sketchlog.core.stats import EventKey

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
