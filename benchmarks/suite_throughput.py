import sys
import os
import argparse
import random

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python"))
import sketchlog

try:
    import _sketchlog_cpp as cpp
    HAS_CPP = True
except ImportError:
    cpp = None
    HAS_CPP = False

from benchmarks.harness import BenchmarkHarness

def main():
    parser = argparse.ArgumentParser(description="SketchLog Throughput Benchmark Suite")
    parser.add_argument("--output", default="benchmark_throughput.json", help="JSON output file")
    parser.add_argument("--items", type=int, default=1_000_000, help="Number of items to process")
    parser.add_argument("--warmup", type=int, default=1, help="Warmup iterations")
    parser.add_argument("--iterations", type=int, default=5, help="Benchmark iterations")
    args = parser.parse_args()

    harness = BenchmarkHarness()
    
    # Generate data
    random.seed(42)
    values = [random.lognormvariate(2, 1) for _ in range(args.items)]
    
    def run_python_scalar():
        log = sketchlog.StreamLog()
        for v in values:
            log.add_latency(v)
            
    harness.measure("python_scalar_add", "seconds", run_python_scalar, iterations=args.iterations, warmup=args.warmup)

    if HAS_CPP:
        def run_cpp_scalar():
            log = cpp.StreamLog()
            for v in values:
                log.add_latency(v)
                
        harness.measure("cpp_scalar_add", "seconds", run_cpp_scalar, iterations=args.iterations, warmup=args.warmup)

    harness.print_summary()
    harness.save(args.output)
    print(f"\nSaved results to {args.output}")

if __name__ == "__main__":
    main()
