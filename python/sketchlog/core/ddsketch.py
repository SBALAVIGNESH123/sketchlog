import math
import sys
from typing import Dict, List, Iterable

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
            val = (2.0 / (1.0 + self._gamma)) * (self._gamma ** index)
            if val == 0.0:
                return float.fromhex('0x0.0000000000001p-1022')
            return val
        except OverflowError:
            return sys.float_info.max

    def add(self, value: float, count: int = 1) -> None:
        if math.isnan(value) or math.isinf(value):
            return  # silently reject
        if count <= 0:
            return

        if value != 0.0:
            abs_v = abs(value)
            idx = self._key(abs_v)
            rep = self._bucket_value(idx)
            if abs(rep - abs_v) / abs_v > self._alpha:
                raise ValueError("Value magnitude too small to satisfy relative accuracy")

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
        multiplier = self._multiplier
        log = math.log
        ceil = math.ceil
        isnan = math.isnan
        isinf = math.isinf

        # Validation checks and temporary accumulation
        alpha = self._alpha
        tmp_pos: dict[int, int] = {}
        tmp_neg: dict[int, int] = {}
        tmp_count = 0
        tmp_min = float('inf')
        tmp_max = float('-inf')
        tmp_zero_count = 0

        for v in values:
            if isnan(v) or isinf(v):
                continue
            if v == 0.0:
                tmp_zero_count += 1
                tmp_count += 1
                if v < tmp_min: tmp_min = v
                if v > tmp_max: tmp_max = v
                continue

            abs_v = abs(v)
            idx = ceil(log(abs_v) * multiplier)
            rep = self._bucket_value(idx)
            if abs(rep - abs_v) / abs_v > alpha:
                raise ValueError("Value magnitude too small to satisfy relative accuracy")

            if v > 0:
                tmp_pos[idx] = tmp_pos.get(idx, 0) + 1
            else:
                tmp_neg[idx] = tmp_neg.get(idx, 0) + 1

            tmp_count += 1
            if v < tmp_min: tmp_min = v
            if v > tmp_max: tmp_max = v

        if tmp_count == 0:
            return

        # Commit ingestion
        pos = self._positive
        for idx, count in tmp_pos.items():
            pos[idx] = pos.get(idx, 0) + count

        neg = self._negative
        for idx, count in tmp_neg.items():
            neg[idx] = neg.get(idx, 0) + count

        self._count += tmp_count
        self._zero_count += tmp_zero_count
        if tmp_min < self._min:
            self._min = tmp_min
        if tmp_max > self._max:
            self._max = tmp_max

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

    def count_greater_than(self, threshold: float) -> int:
        """Count number of values strictly greater than threshold."""
        if self._count == 0:
            return 0
        if threshold >= self._max:
            return 0
        if threshold < self._min:
            return self._count

        count_gt = 0

        if threshold < 0:
            idx = self._key(-threshold)
            for k, v in self._negative.items():
                if k < idx:
                    count_gt += v
            count_gt += self._zero_count
            for v in self._positive.values():
                count_gt += v
            return count_gt

        if threshold == 0:
            for v in self._positive.values():
                count_gt += v
            return count_gt

        idx = self._key(threshold)
        for k, v in self._positive.items():
            if k > idx:
                count_gt += v

        return count_gt

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
