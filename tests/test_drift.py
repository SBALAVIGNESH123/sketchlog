"""DriftSketch tests — drift detection, correlation, memory, thread safety."""
import random
import threading
from sketchlog.drift import DriftSketch

def test_basic_dimension_tracking():
    ds = DriftSketch(window="9999s")
    random.seed(42)
    for _ in range(200):
        ds.add("api", random.lognormvariate(2, 0.5))
        ds.add("db", random.lognormvariate(3, 0.5))
        ds.add("cache", random.lognormvariate(1, 0.3))

    assert len(ds.dimensions) == 3
    assert ds.summary()["total_events"] == 600
    assert all(m["current_p99"] > 0 for m in ds.summary()["metrics"])

def test_drift_detection():
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
    drifted = {d["dimension"] for d in drifts}

    assert "api_latency" in drifted
    assert "redis_latency" in drifted
    assert "error_rate" in drifted

    cache_d = [d for d in drifts if d["dimension"] == "cache_miss"]
    assert "cache_miss" not in drifted or (cache_d and abs(cache_d[0]["drift_pct"]) < 30)

def test_correlation():
    ds2 = DriftSketch(window="9999s")
    random.seed(42)
    for _ in range(500):
        ds2.add("api_latency", random.gauss(50, 5))
        ds2.add("redis_latency", random.gauss(8, 1))
        ds2.add("error_rate", random.gauss(0.02, 0.005))

    ds2.rotate_all()
    for _ in range(500):
        ds2.add("api_latency", random.gauss(200, 30))
        ds2.add("redis_latency", random.gauss(50, 10))
        ds2.add("error_rate", random.gauss(0.15, 0.03))

    corrs = ds2.correlations(min_events=100)
    spiked = {"api_latency", "redis_latency", "error_rate"}
    spike_corrs = [c for c in corrs if set(c["pair"]).issubset(spiked) and c["score"] > 0]

    assert len(spike_corrs) >= 2

def test_memory_per_dimension():
    dt = DriftSketch(window="9999s")
    for i in range(20):
        for _ in range(50):
            dt.add(f"d{i}", random.lognormvariate(2, 1))

    # Check memory is reasonable (less than 50KB per 20 dims implies <2.5KB/dim overhead roughly)
    assert dt.memory_kb() / 20 < 50

def test_thread_safety():
    ds_mt = DriftSketch(window="9999s")
    def writer(name):
        for i in range(200):
            ds_mt.add(name, float(i))

    threads = [threading.Thread(target=writer, args=(f"t{i}",)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert ds_mt.summary()["total_events"] == 800

def test_add_batch_generator():
    ds = DriftSketch()
    # Using a generator expression
    ds.add_batch("generator_dim", (float(i) for i in range(10)))
    assert ds.summary()["total_events"] == 10
    assert ds.summary()["metrics"][0]["events"] == 10

def test_empty_sketch_no_deadlock():
    ds = DriftSketch()
    out = {}

    def _worker():
        out["repr"] = repr(ds)
        out["summary"] = ds.summary()

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    t.join(timeout=1.0)

    assert not t.is_alive(), "repr()/summary() deadlocked"
    assert out["repr"].startswith("DriftSketch")
    assert out["summary"]["total_events"] == 0

def test_idle_window_gaps():
    import time
    ds = DriftSketch(window=0.1)

    # First window
    for _ in range(10):
        ds.add("latency", 10.0)

    # Sleep past more than 2 windows
    time.sleep(0.25)

    # Next window
    ds.add("latency", 100.0)

    # Because of the long idle gap, the immediately previous window was empty
    summary = ds.summary()
    metric = summary["metrics"][0]
    assert metric["previous_p99"] == 0.0

    # Drift should not compare against the stale 10.0 from 3 windows ago
    drifts = ds.drift()
    assert len(drifts) == 0
