from typing import List, Tuple, Dict, Any
from sketchlog.facade import StreamLog

def get_cdf(stream: StreamLog) -> List[Tuple[float, float]]:
    """
    Extract sorted (value, cumulative_probability) points from a StreamLog.
    """
    state = stream.to_dict()['latency']
    total_count = state['count']
    if total_count == 0:
        return []

    alpha = state['alpha']
    gamma = (1.0 + alpha) / (1.0 - alpha)
    
    def bucket_value(idx: int) -> float:
        return float((2.0 / (1.0 + gamma)) * (gamma ** idx))

    points = []
    
    for k_str, count in state.get('negative', {}).items():
        points.append((-bucket_value(int(k_str)), count))
        
    if state.get('zero_count', 0) > 0:
        points.append((0.0, state['zero_count']))
        
    for k_str, count in state.get('positive', {}).items():
        points.append((bucket_value(int(k_str)), count))
        
    # Sort by value
    points.sort(key=lambda x: x[0])
    
    # Compute CDF
    cdf: List[Tuple[float, float]] = []
    cum = 0.0
    for val, count in points:
        cum += count
        cdf.append((val, cum / total_count))
        
    return cdf

def ks_statistic(cdf1: List[Tuple[float, float]], cdf2: List[Tuple[float, float]]) -> float:
    """
    Kolmogorov-Smirnov statistic: max absolute difference between two CDFs.
    """
    if not cdf1 and not cdf2:
        return 0.0
    if not cdf1 or not cdf2:
        return 1.0

    all_vals = sorted(list(set([v for v, p in cdf1] + [v for v, p in cdf2])))
    
    i, j = 0, 0
    p1, p2 = 0.0, 0.0
    max_diff = 0.0
    
    for v in all_vals:
        while i < len(cdf1) and cdf1[i][0] <= v:
            p1 = cdf1[i][1]
            i += 1
        while j < len(cdf2) and cdf2[j][0] <= v:
            p2 = cdf2[j][1]
            j += 1
            
        diff = abs(p1 - p2)
        if diff > max_diff:
            max_diff = diff
            
    return max_diff

def wasserstein_distance(cdf1: List[Tuple[float, float]], cdf2: List[Tuple[float, float]]) -> float:
    """
    1D Wasserstein Distance (Earth Mover's Distance)
    Calculated as the integral of the absolute difference between the two CDFs.
    """
    if not cdf1 and not cdf2:
        return 0.0

    all_vals = sorted(list(set([v for v, p in cdf1] + [v for v, p in cdf2])))
    
    if not all_vals:
        return 0.0
        
    i, j = 0, 0
    p1, p2 = 0.0, 0.0
    w_dist = 0.0
    
    prev_v = all_vals[0]
    
    for v in all_vals:
        w_dist += abs(p1 - p2) * (v - prev_v)
        
        while i < len(cdf1) and cdf1[i][0] <= v:
            p1 = cdf1[i][1]
            i += 1
        while j < len(cdf2) and cdf2[j][0] <= v:
            p2 = cdf2[j][1]
            j += 1
            
        prev_v = v
        
    return w_dist

def plot_ascii_cdf(cdf1: List[Tuple[float, float]], cdf2: List[Tuple[float, float]], width: int = 60, height: int = 15) -> str:
    """
    Renders an ASCII overlay plot of two CDFs.
    Uses '.' for CDF 1, '*' for CDF 2, and 'B' where they overlap.
    """
    if not cdf1 and not cdf2:
        return "Empty distributions"
        
    all_vals = [v for v, p in cdf1] + [v for v, p in cdf2]
    min_v = min(all_vals)
    max_v = max(all_vals)
    
    grid = [[' ' for _ in range(width)] for _ in range(height)]
    
    def get_x(val: float) -> int:
        if max_v == min_v: return 0
        return int((val - min_v) / (max_v - min_v) * (width - 1))
        
    def get_y(prob: float) -> int:
        return int((1.0 - prob) * (height - 1))
        
    # Plot cdf1
    for i in range(len(cdf1)):
        x = get_x(cdf1[i][0])
        y = get_y(cdf1[i][1])
        if grid[y][x] == ' ':
            grid[y][x] = '.'
            
    # Plot cdf2
    for i in range(len(cdf2)):
        x = get_x(cdf2[i][0])
        y = get_y(cdf2[i][1])
        if grid[y][x] == ' ':
            grid[y][x] = '*'
        elif grid[y][x] == '.':
            grid[y][x] = 'B'
            
    lines = []
    lines.append("1.0 |" + "".join(grid[0]))
    for y in range(1, height-1):
        lines.append("    |" + "".join(grid[y]))
    lines.append("0.0 |" + "".join(grid[height-1]))
    lines.append("    +" + "-" * width)
    lines.append(f"     {min_v:<{width//2}.4f}{max_v:>{width//2-1}.4f}")
    return "\n".join(lines)

class SketchDiff:
    """
    Programmatic comparison between two StreamLog instances.
    """
    def __init__(self, stream1: StreamLog, stream2: StreamLog):
        self.cdf1 = get_cdf(stream1)
        self.cdf2 = get_cdf(stream2)
        
    @property
    def ks_statistic(self) -> float:
        return ks_statistic(self.cdf1, self.cdf2)
        
    @property
    def wasserstein_distance(self) -> float:
        return wasserstein_distance(self.cdf1, self.cdf2)
        
    def plot_ascii(self, width: int = 60, height: int = 15) -> str:
        return plot_ascii_cdf(self.cdf1, self.cdf2, width, height)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ks_statistic": self.ks_statistic,
            "wasserstein_distance": self.wasserstein_distance,
            "cdf_1": [{"value": v, "prob": p} for v, p in self.cdf1],
            "cdf_2": [{"value": v, "prob": p} for v, p in self.cdf2],
            "ascii_plot": self.plot_ascii()
        }
