import time
import os
import random
try:
    import psutil
except ImportError:
    psutil = None
from sketchlog import StreamLog
try:
    from tdigest import TDigest
except ImportError:
    TDigest = None

def get_memory_rss():
    if psutil:
        process = psutil.Process(os.getpid())
        return process.memory_info().rss
    return 0

def benchmark_tdigest_vs_sketchlog():
    if not TDigest:
        print("tdigest not installed. Skipping benchmark.")
        return
        
    num_events = 1_000_000
    data = [random.lognormvariate(1.0, 0.5) for _ in range(num_events)]
    
    print(f"Benchmarking with {num_events:,} events...\n")
    
    # --- SketchLog ---
    print("--- SketchLog ---")
    log = StreamLog()
    
    mem_before = get_memory_rss()
    start = time.time()
    
    for val in data:
        log.add_latency(val)
        
    elapsed = time.time() - start
    mem_after = get_memory_rss()
    
    p99_sl = log.p99()
    mem_used_sl = mem_after - mem_before
    throughput_sl = num_events / elapsed
    
    print(f"Throughput: {throughput_sl:,.0f} events/sec")
    print(f"p99: {p99_sl:.4f}")
    if psutil:
        print(f"Peak RSS Memory: {mem_used_sl / 1024:.1f} KB")
    
    # --- T-Digest ---
    print("\n--- T-Digest ---")
    td = TDigest()
    
    mem_before = get_memory_rss()
    start = time.time()
    
    for val in data:
        td.update(val)
        
    elapsed = time.time() - start
    mem_after = get_memory_rss()
    
    p99_td = td.percentile(99)
    mem_used_td = mem_after - mem_before
    throughput_td = num_events / elapsed
    
    print(f"Throughput: {throughput_td:,.0f} events/sec")
    print(f"p99: {p99_td:.4f}")
    if psutil:
        print(f"Peak RSS Memory: {mem_used_td / 1024:.1f} KB")
        
    # --- Merge Performance ---
    print("\n--- Merge Performance (100 merges) ---")
    
    # SketchLog Merge
    logs = [StreamLog() for _ in range(100)]
    for l in logs:
        l.add_latency(42.0)
    master_log = StreamLog()
    
    start = time.time()
    for l in logs:
        master_log.merge(l)
    print(f"SketchLog Merge: {(time.time() - start)*1000:.2f} ms")
    
    # T-Digest Merge
    tds = [TDigest() for _ in range(100)]
    for t in tds:
        t.update(42.0)
    master_td = TDigest()
    
    start = time.time()
    for t in tds:
        master_td = master_td + t
    print(f"T-Digest Merge: {(time.time() - start)*1000:.2f} ms")

if __name__ == "__main__":
    benchmark_tdigest_vs_sketchlog()
