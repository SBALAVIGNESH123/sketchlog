"""Soak tests — sustained operation tracking RSS, latency drift, FDs."""
import asyncio
import json
import os
import random
import time
from pathlib import Path

import httpx
import pytest


pytestmark = [pytest.mark.stress, pytest.mark.soak]

SOAK_DURATION = int(os.environ.get("SOAK_DURATION", "30"))


def _make_batch(rng: random.Random) -> dict:
    return {
        "latencies": [rng.lognormvariate(2, 0.5) for _ in range(20)],
        "events": {"soak_event": rng.randint(1, 5)},
        "uniques": [f"soak-user-{rng.randint(1, 500)}"],
    }


@pytest.mark.asyncio
async def test_soak_memory_stability(live_server, resource_envelope, results_dir):
    """Run ingestion for SOAK_DURATION seconds and track RSS."""
    try:
        import psutil
    except ImportError:
        pytest.skip("psutil not installed")

    base = live_server["url"]
    proc = psutil.Process(live_server["process"].pid)

    rss_samples = []
    latency_windows: list[list[float]] = []
    current_window: list[float] = []
    window_start = time.monotonic()

    rng = random.Random(42)
    deadline = time.monotonic() + SOAK_DURATION

    async with httpx.AsyncClient() as client:
        while time.monotonic() < deadline:
            batch = _make_batch(rng)
            t0 = time.perf_counter()
            try:
                await client.post(f"{base}/v1/streams/soak-stream/events",
                                  json=batch, timeout=5)
                elapsed_ms = (time.perf_counter() - t0) * 1000
                current_window.append(elapsed_ms)
            except Exception:
                pass

            now = time.monotonic()
            if now - window_start >= 5:
                if current_window:
                    latency_windows.append(current_window)
                current_window = []
                window_start = now
                try:
                    rss_samples.append(proc.memory_info().rss / (1024 * 1024))
                except Exception:
                    pass

    if current_window:
        latency_windows.append(current_window)
    if rss_samples:
        try:
            rss_samples.append(proc.memory_info().rss / (1024 * 1024))
        except Exception:
            pass

    result = {
        "test": "soak_memory_stability",
        "duration_s": SOAK_DURATION,
        "rss_samples_mb": [round(r, 1) for r in rss_samples],
        "rss_max_mb": round(max(rss_samples), 1) if rss_samples else 0,
        "rss_start_mb": round(rss_samples[0], 1) if rss_samples else 0,
        "rss_end_mb": round(rss_samples[-1], 1) if rss_samples else 0,
        "latency_window_count": len(latency_windows),
    }
    (results_dir / "soak_results.json").write_text(json.dumps(result, indent=2))

    if rss_samples:
        assert max(rss_samples) < resource_envelope["soak_max_rss_mb"], \
            f"RSS {max(rss_samples):.1f}MB exceeds {resource_envelope['soak_max_rss_mb']}MB"


@pytest.mark.asyncio
async def test_soak_latency_drift(live_server, resource_envelope):
    """Track p99 response latency in 5-second windows and check drift."""
    base = live_server["url"]
    rng = random.Random(99)
    duration = min(SOAK_DURATION, 30)

    windows: list[float] = []
    current_latencies: list[float] = []
    window_start = time.monotonic()
    deadline = time.monotonic() + duration

    async with httpx.AsyncClient() as client:
        while time.monotonic() < deadline:
            batch = _make_batch(rng)
            t0 = time.perf_counter()
            try:
                await client.post(f"{base}/v1/streams/drift-test/events",
                                  json=batch, timeout=5)
                elapsed_ms = (time.perf_counter() - t0) * 1000
                current_latencies.append(elapsed_ms)
            except Exception:
                pass

            if time.monotonic() - window_start >= 5:
                if current_latencies:
                    current_latencies.sort()
                    p99_idx = int(len(current_latencies) * 0.99)
                    windows.append(current_latencies[p99_idx])
                current_latencies = []
                window_start = time.monotonic()

    if len(windows) >= 2:
        drift_pct = abs(windows[-1] - windows[0]) / windows[0] * 100 if windows[0] > 0 else 0
        assert drift_pct < resource_envelope["soak_max_latency_drift_pct"], \
            f"Latency drift {drift_pct:.1f}% exceeds {resource_envelope['soak_max_latency_drift_pct']}%"


@pytest.mark.asyncio
async def test_soak_fd_stability(live_server):
    """Track open file descriptors over the soak period."""
    try:
        import psutil
    except ImportError:
        pytest.skip("psutil not installed")

    proc = psutil.Process(live_server["process"].pid)
    base = live_server["url"]
    rng = random.Random(77)

    try:
        initial_fds = proc.num_fds() if hasattr(proc, 'num_fds') else len(proc.open_files())
    except Exception:
        pytest.skip("Cannot measure FDs on this platform")

    duration = min(SOAK_DURATION, 20)
    deadline = time.monotonic() + duration

    async with httpx.AsyncClient() as client:
        while time.monotonic() < deadline:
            batch = _make_batch(rng)
            try:
                await client.post(f"{base}/v1/streams/fd-test/events",
                                  json=batch, timeout=5)
            except Exception:
                pass

    try:
        final_fds = proc.num_fds() if hasattr(proc, 'num_fds') else len(proc.open_files())
    except Exception:
        return  # Can't measure, skip assertion

    fd_growth = final_fds - initial_fds
    assert fd_growth < 50, f"FD growth {fd_growth} suggests a leak"
