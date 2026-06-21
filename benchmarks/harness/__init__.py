import platform
import json
import time
import statistics
from typing import Callable, Dict, Any, List
from datetime import datetime

class BenchmarkResult:
    def __init__(self, name: str, unit: str):
        self.name = name
        self.unit = unit
        self.samples: List[float] = []
        self.mean = 0.0
        self.median = 0.0
        self.stddev = 0.0
        self.p95 = 0.0

    def add_sample(self, value: float):
        self.samples.append(value)

    def finalize(self):
        if not self.samples:
            return
        self.samples.sort()
        self.mean = statistics.mean(self.samples)
        self.median = statistics.median(self.samples)
        self.stddev = statistics.stdev(self.samples) if len(self.samples) > 1 else 0.0
        # Use 0-based percentile formula to prevent systematic inflation
        import math
        n = len(self.samples)
        idx_95 = max(0, min(n - 1, int(math.ceil(0.95 * n)) - 1))
        self.p95 = self.samples[idx_95]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "unit": self.unit,
            "samples": len(self.samples),
            "mean": self.mean,
            "median": self.median,
            "stddev": self.stddev,
            "p95": self.p95,
            "raw_samples": self.samples
        }

class BenchmarkHarness:
    def __init__(self):
        self.env = {
            "os": platform.system(),
            "os_release": platform.release(),
            "machine": platform.machine(),
            "python_version": platform.python_version(),
            "processor": platform.processor(),
            "timestamp": datetime.utcnow().isoformat() + "Z"
        }
        self.results: List[BenchmarkResult] = []

    def measure(self, name: str, unit: str, func: Callable, iterations: int = 5, warmup: int = 1) -> BenchmarkResult:
        """Measure execution time of func() over multiple iterations, after warmup."""
        # Warmup
        for _ in range(warmup):
            func()

        result = BenchmarkResult(name, unit)
        for _ in range(iterations):
            t0 = time.perf_counter()
            func()
            t1 = time.perf_counter()
            result.add_sample(t1 - t0)

        result.finalize()
        self.results.append(result)
        return result

    def add_metric(self, name: str, unit: str, samples: List[float]) -> BenchmarkResult:
        """Add an explicitly measured metric (e.g. accuracy errors)."""
        result = BenchmarkResult(name, unit)
        for s in samples:
            result.add_sample(s)
        result.finalize()
        self.results.append(result)
        return result

    def save(self, filepath: str):
        data = {
            "environment": self.env,
            "results": [r.to_dict() for r in self.results]
        }
        with open(filepath, "w") as f:
            json.dump(data, f, indent=2)

    def print_summary(self):
        print(f"=== Benchmark Environment ===")
        for k, v in self.env.items():
            print(f"{k}: {v}")
        print(f"\n=== Results ===")
        for r in self.results:
            print(f"{r.name} ({r.unit}): mean={r.mean:.4f}, median={r.median:.4f}, stddev={r.stddev:.4f}, p95={r.p95:.4f}")
