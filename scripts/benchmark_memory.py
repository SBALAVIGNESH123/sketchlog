import random
import platform
from sketchlog import StreamLog
import sketchlog

def run_benchmark() -> None:
    if not sketchlog.HAS_CPP:
        raise RuntimeError("Benchmark requires C++ backend to be enabled.")

    seed = 42
    random.seed(seed)

    print(f"Platform: {platform.platform()}")
    print(f"Python Version: {platform.python_version()}")
    print(f"C++ Backend Enabled: {sketchlog.HAS_CPP}")
    print(f"Seed: {seed}")
    print("Distribution: random.uniform(1.0, 1000.0)")
    print("-" * 50)
    print(f"{'Events':>12} | {'Memory (KB)':>15}")
    print("-" * 50)

    log = StreamLog()

    checkpoints = [1000, 10_000, 100_000, 1_000_000, 10_000_000, 100_000_000]

    current_events = 0

    for target in checkpoints:
        while current_events < target:
            # We batch generate in memory or just process one by one
            # to avoid generating a massive array that blows up RAM
            log.add_latency(random.uniform(1.0, 1000.0))
            current_events += 1

        print(f"{target:>12,} | {log.memory_kb():>15.2f}")

if __name__ == "__main__":
    run_benchmark()
