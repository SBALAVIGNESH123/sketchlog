import os
import sys
import json
import time
import subprocess
import gc

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "python")))

try:
    import numpy as np
except ImportError:
    np = None

try:
    import psutil
except ImportError:
    psutil = None

def get_rss():
    if psutil:
        return psutil.Process(os.getpid()).memory_info().rss
    return 0

def generate_data(dist_name, n, seed=42):
    np.random.seed(seed)
    if dist_name == "uniform":
        return np.random.uniform(0, 100, n).astype(float)
    elif dist_name == "normal":
        return np.random.normal(50, 15, n).astype(float)
    elif dist_name == "lognormal":
        return np.random.lognormal(2, 1, n).astype(float)
    elif dist_name == "bimodal":
        a = np.random.normal(20, 5, n // 2)
        b = np.random.normal(80, 10, n - (n // 2))
        return np.concatenate((a, b)).astype(float)
    elif dist_name == "zipf":
        # numpy zipf can produce huge values, clip to something reasonable
        return np.clip(np.random.zipf(1.5, n), 0, 10000).astype(float)
    else:
        raise ValueError(f"Unknown dist: {dist_name}")

def run_worker():
    candidate = sys.argv[2]
    dist_name = sys.argv[3]
    n = int(sys.argv[4])

    data = generate_data(dist_name, n)
    
    gc.collect()
    rss_baseline = get_rss()
    start_time = time.time()
    
    p50 = p95 = p99 = 0.0

    if candidate == "exact":
        data.sort()
        p50 = float(np.percentile(data, 50))
        p95 = float(np.percentile(data, 95))
        p99 = float(np.percentile(data, 99))
    
    elif candidate == "sketchlog":
        from sketchlog import StreamLog
        log = StreamLog()
        if hasattr(log, "add_batch"):
            log.add_batch(data)
        else:
            for x in data: log.add_latency(x)
        p50 = float(log.percentile(0.5))
        p95 = float(log.p95())
        p99 = float(log.p99())
    
    elif candidate == "tdigest":
        from tdigest import TDigest
        td = TDigest()
        td.batch_update(data)
        p50 = float(td.percentile(50))
        p95 = float(td.percentile(95))
        p99 = float(td.percentile(99))
        
    elif candidate == "datasketches":
        from datasketches import kll_floats_sketch
        kll = kll_floats_sketch(200)
        # Note: Datasketches requires updating item by item if no batch method exists in the python bindings
        for x in data:
            kll.update(float(x))
        q = kll.get_quantiles([0.5, 0.95, 0.99])
        p50, p95, p99 = float(q[0]), float(q[1]), float(q[2])
    
    elapsed = time.time() - start_time
    gc.collect()
    rss_after = get_rss()
    
    res = {
        "p50": p50,
        "p95": p95,
        "p99": p99,
        "mem_bytes": max(0, rss_after - rss_baseline),
        "elapsed": elapsed,
        "throughput": n / elapsed if elapsed > 0 else 0
    }
    
    print("JSON_START")
    print(json.dumps(res))
    print("JSON_END")

def main():
    if len(sys.argv) > 1 and sys.argv[1] == "run_worker":
        run_worker()
        sys.exit(0)

    if not np or not psutil:
        print("Missing numpy or psutil. Run: pip install numpy psutil tdigest datasketches")
        sys.exit(1)

    distributions = ["uniform", "normal", "lognormal", "bimodal", "zipf"]
    sizes = [1_000_000, 10_000_000]
    candidates = ["sketchlog", "tdigest", "datasketches"]
    
    results = []

    print("Running benchmark suite...")
    
    for size in sizes:
        for dist in distributions:
            print(f"\\n--- {dist} @ {size:,} events ---")
            
            # Get exact
            try:
                exact_output = subprocess.check_output([sys.executable, __file__, "run_worker", "exact", dist, str(size)], text=True, timeout=120)
                exact_json = exact_output.split("JSON_START")[1].split("JSON_END")[0].strip()
                exact_res = json.loads(exact_json)
            except Exception as e:
                print(f"    Failed exact calculation: {e}")
                continue
            
            for cand in candidates:
                print(f"    Running {cand}...", end="", flush=True)
                try:
                    out = subprocess.check_output([sys.executable, __file__, "run_worker", cand, dist, str(size)], text=True, timeout=60)
                    out_json = out.split("JSON_START")[1].split("JSON_END")[0].strip()
                    res = json.loads(out_json)
                    
                    err50 = abs(res["p50"] - exact_res["p50"]) / max(1e-9, exact_res["p50"])
                    err95 = abs(res["p95"] - exact_res["p95"]) / max(1e-9, exact_res["p95"])
                    err99 = abs(res["p99"] - exact_res["p99"]) / max(1e-9, exact_res["p99"])
                    
                    results.append({
                        "size": size,
                        "dist": dist,
                        "candidate": cand,
                        "status": "OK",
                        "err50": err50,
                        "err95": err95,
                        "err99": err99,
                        "mem_kb": res["mem_bytes"] / 1024,
                        "throughput": res["throughput"]
                    })
                    print(f" Done. {cand:>12}: {res['throughput']:>12,.0f} ops/s | mem: {res['mem_bytes']/1024:>7.1f} KB | err99: {err99*100:>5.2f}%", flush=True)
                except subprocess.TimeoutExpired:
                    print(f" TIMEOUT (60s)", flush=True)
                    results.append({
                        "size": size, "dist": dist, "candidate": cand,
                        "status": "TIMEOUT", "err50": None, "err95": None, "err99": None, "mem_kb": None, "throughput": None
                    })
                except Exception as e:
                    print(f" FAILED ({e})", flush=True)
                    results.append({
                        "size": size, "dist": dist, "candidate": cand,
                        "status": "FAILED", "err50": None, "err95": None, "err99": None, "mem_kb": None, "throughput": None
                    })

    # Write BENCHMARKS.md
    with open("BENCHMARKS.md", "w") as f:
        f.write("# Benchmark Suite Results\n\n")
        f.write("Compares SketchLog, T-Digest, and Apache DataSketches (KLL) across multiple distributions and scales.\n\n")
        
        f.write("## 1M Events\n\n")
        f.write("| Distribution | Candidate | p50 Error | p95 Error | p99 Error | Memory (KB) | Throughput (ops/s) |\n")
        f.write("|--------------|-----------|-----------|-----------|-----------|-------------|--------------------|\n")
        def format_row(r):
            if r["status"] != "OK":
                return f"| {r['dist']} | {r['candidate']} | {r['status']} | {r['status']} | {r['status']} | {r['status']} | {r['status']} |\n"
            return f"| {r['dist']} | {r['candidate']} | {r['err50']*100:.2f}% | {r['err95']*100:.2f}% | {r['err99']*100:.2f}% | {r['mem_kb']:.1f} | {r['throughput']:,.0f} |\n"

        for r in results:
            if r["size"] == 1_000_000:
                f.write(format_row(r))
                
        f.write("\n## 10M Events\n\n")
        f.write("| Distribution | Candidate | p50 Error | p95 Error | p99 Error | Memory (KB) | Throughput (ops/s) |\n")
        f.write("|--------------|-----------|-----------|-----------|-----------|-------------|--------------------|\n")
        for r in results:
            if r["size"] == 10_000_000:
                f.write(format_row(r))

if __name__ == "__main__":
    main()
