"""DriftSketch tests — drift detection, correlation, memory, thread safety."""
import sys, random, threading
sys.path.insert(0, "python")
from sketchlog.drift import DriftSketch

PASS = 0
FAIL = 0
def check(name, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1; print(f"  [PASS] {name}", flush=True)
    else:
        FAIL += 1; print(f"  [FAIL] {name} -- {detail}", flush=True)

print("=" * 66, flush=True)
print("  DRIFTSKETCH TESTS", flush=True)
print("=" * 66, flush=True)

# ── 1. Basic operation ──────────────────────────────────────────────
print("\n1. Basic dimension tracking", flush=True)
ds = DriftSketch(window="9999s")
random.seed(42)
for _ in range(200):
    ds.add("api", random.lognormvariate(2, 0.5))
    ds.add("db", random.lognormvariate(3, 0.5))
    ds.add("cache", random.lognormvariate(1, 0.3))
check("3 dimensions", len(ds.dimensions) == 3)
check("600 events", ds.summary()["total_events"] == 600)
check("p99 > 0", all(m["current_p99"] > 0 for m in ds.summary()["metrics"]))
print(f"  [INFO] Memory: {ds.memory_kb():.1f} KB ({ds.memory_kb()/3:.1f} KB/dim)", flush=True)

# ── 2. Drift detection ──────────────────────────────────────────────
print("\n2. Drift detection (simulated incident)", flush=True)
ds2 = DriftSketch(window="9999s")
random.seed(42)
for _ in range(500):
    ds2.add("api_latency", random.gauss(50, 5))
    ds2.add("redis_latency", random.gauss(8, 1))
    ds2.add("error_rate", random.gauss(0.02, 0.005))
    ds2.add("cache_miss", random.gauss(0.10, 0.02))
ds2.rotate_all()  # freeze baseline, start fresh
for _ in range(500):
    ds2.add("api_latency", random.gauss(200, 30))
    ds2.add("redis_latency", random.gauss(50, 10))
    ds2.add("error_rate", random.gauss(0.15, 0.03))
    ds2.add("cache_miss", random.gauss(0.10, 0.02))

drifts = ds2.drift(threshold=0.1)
print(f"  Drifts: {len(drifts)}", flush=True)
for d in drifts:
    print(f"    {d['dimension']:20s}: {d['previous_p99']:>8.4f} -> {d['current_p99']:>8.4f} "
          f"({d['drift_pct']:+.1f}%)", flush=True)
drifted = {d["dimension"] for d in drifts}
check("api_latency drifted", "api_latency" in drifted)
check("redis_latency drifted", "redis_latency" in drifted)
check("error_rate drifted", "error_rate" in drifted)
cache_d = [d for d in drifts if d["dimension"] == "cache_miss"]
check("cache_miss stable", "cache_miss" not in drifted or
      (cache_d and abs(cache_d[0]["drift_pct"]) < 30))

# ── 3. Correlation ──────────────────────────────────────────────────
print("\n3. Co-occurring drift detection", flush=True)
corrs = ds2.correlations(min_events=100)
print(f"  Co-occurring pairs: {len(corrs)}", flush=True)
for c in corrs[:5]:
    print(f"    {c['pair'][0]:20s} <-> {c['pair'][1]:20s}: "
          f"score={c['score']:+.4f} ({c['direction']})", flush=True)
spiked = {"api_latency", "redis_latency", "error_rate"}
spike_corrs = [c for c in corrs if set(c["pair"]).issubset(spiked) and c["score"] > 0]
check(f"spiked metrics co-occurred ({len(spike_corrs)} pairs)", len(spike_corrs) >= 2)

# ── 4. Memory ──────────────────────────────────────────────────────
print("\n4. Memory per dimension", flush=True)
for n in [1, 5, 10, 20]:
    dt = DriftSketch(window="9999s")
    for i in range(n):
        for _ in range(50):
            dt.add(f"d{i}", random.lognormvariate(2, 1))
    print(f"  {n:>3} dims: {dt.memory_kb():>8.1f} KB ({dt.memory_kb()/n:.1f}/dim)", flush=True)
check("memory reasonable", dt.memory_kb() / 20 < 50)

# ── 5. Thread safety ──────────────────────────────────────────────
print("\n5. Thread safety", flush=True)
ds_mt = DriftSketch(window="9999s")
def writer(name):
    for i in range(200):
        ds_mt.add(name, float(i))
threads = [threading.Thread(target=writer, args=(f"t{i}",)) for i in range(4)]
for t in threads: t.start()
for t in threads: t.join()
check("4 threads, 800 events", ds_mt.summary()["total_events"] == 800,
      f"got {ds_mt.summary()['total_events']}")

# ── Summary ─────────────────────────────────────────────────────────
print("\n" + "=" * 66, flush=True)
print(f"  RESULTS: {PASS}/{PASS+FAIL} passed, {FAIL} failed", flush=True)
if FAIL == 0:
    print("  ALL DRIFTSKETCH TESTS PASS", flush=True)
print("=" * 66, flush=True)
sys.exit(FAIL)
