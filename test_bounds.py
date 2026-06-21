import math
import sys

def check_bounds(alpha):
    gamma = (1.0 + alpha) / (1.0 - alpha)
    multiplier = 1.0 / math.log(gamma)
    max_val = sys.float_info.max
    min_val = 5e-324
    max_idx = math.ceil(math.log(max_val) * multiplier)
    min_idx = math.ceil(math.log(min_val) * multiplier)
    print(f"alpha={alpha}, min_idx={min_idx}, max_idx={max_idx}")

check_bounds(0.01)
check_bounds(0.99)
check_bounds(1e-5)
check_bounds(0.0001)
