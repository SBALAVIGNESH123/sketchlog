import re

with open("python/sketchlog/__init__.py", "r", encoding="utf-8") as f:
    code = f.read()

# Replace try: ... except (KeyError, TypeError)
code = code.replace(
    "except (KeyError, TypeError) as e:",
    "except (KeyError, TypeError, ValueError, OverflowError, ZeroDivisionError) as e:"
)

# version checking
code = code.replace(
    "if version != 1:",
    "if type(version) is not int or version != 1:"
)

# validate_buckets to check duplicate keys and catch int() ValueError
old_validate = """            def validate_buckets(b_dict: Dict[Any, Any]) -> Dict[int, int]:
                res = {}
                for k, v in b_dict.items():
                    ik = int(k)
                    if type(v) is not int or v < 0:
                        raise ValueError(f"DDSketch bucket counts must be non-negative integers")
                    if ik < min_idx or ik > max_idx:
                        raise ValueError(f"DDSketch bucket index out of valid range: {ik}")
                    res[ik] = v
                return res"""

new_validate = """            def validate_buckets(b_dict: Dict[Any, Any]) -> Dict[int, int]:
                res = {}
                for k, v in b_dict.items():
                    try:
                        ik = int(k)
                    except (ValueError, TypeError):
                        raise ValueError(f"DDSketch bucket keys must be convertible to int: {k}")
                    if ik in res:
                        raise ValueError(f"Duplicate canonical bucket index: {ik}")
                    if type(v) is not int or v < 0:
                        raise ValueError(f"DDSketch bucket counts must be non-negative integers")
                    if ik < min_idx or ik > max_idx:
                        raise ValueError(f"DDSketch bucket index out of valid range: {ik}")
                    res[ik] = v
                return res"""

if old_validate in code:
    code = code.replace(old_validate, new_validate)
    print("Replaced validate_buckets")
else:
    print("validate_buckets not found!")

old_minmax = """            if count > 0:
                if lat_min is None or lat_max is None:
                    raise ValueError("DDSketch min/max cannot be None when count > 0")
                if type(lat_min) not in (int, float) or type(lat_max) not in (int, float):
                    raise ValueError("DDSketch min/max must be numeric")
                if not math.isfinite(lat_min) or not math.isfinite(lat_max):
                    raise ValueError("DDSketch min/max must be finite numbers")
                if lat_min > lat_max:
                    raise ValueError("DDSketch min cannot be greater than max")"""

new_minmax = """            if count > 0:
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
                if lat_min != float('inf') or lat_max != float('-inf'):
                    raise ValueError("Empty DDSketch must have canonical extrema")"""

if old_minmax in code:
    code = code.replace(old_minmax, new_minmax)
    print("Replaced minmax")
else:
    print("minmax not found!")

with open("python/sketchlog/__init__.py", "w", encoding="utf-8") as f:
    f.write(code)
