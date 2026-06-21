import re

content = open('python/sketchlog/__init__.py', encoding='utf-8').read()

new_add_batch = """    def add_batch(self, values: Iterable[float]) -> None:
        \"\"\"Bulk-add values. 2-5x faster than individual add() calls.\"\"\"
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
"""

content = re.sub(r'    def add_batch\(self, values: Iterable\[float\]\) -> None:.*?            if v > self\._max:\n                self\._max = v\n', new_add_batch, content, flags=re.DOTALL)

open('python/sketchlog/__init__.py', 'w', encoding='utf-8', newline='').write(content)
