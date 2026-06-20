"""Load tests — throughput and latency under controlled concurrency."""
import asyncio
import json
import os
import random
import time
from pathlib import Path

import httpx
import pytest


pytestmark = [pytest.mark.stress, pytest.mark.load]

CONCURRENCY = int(os.environ.get("LOAD_CONCURRENCY", "20"))
REQUESTS_PER_CLIENT = int(os.environ.get("LOAD_REQUESTS", "50"))


def _make_batch(rng: random.Random) -> dict:
    return {
        "latencies": [rng.lognormvariate(2, 0.5) for _ in range(50)],
        "events": {"api_call": rng.randint(1, 10), "cache_miss": rng.randint(1, 5)},
        "uniques": [f"user-{rng.randint(1, 1000)}" for _ in range(5)],
    }


async def _worker(client: httpx.AsyncClient, base: str, worker_id: int,
                  n: int, latencies: list, rng: random.Random) -> int:
    ok = 0
    for i in range(n):
        stream_id = f"load-{worker_id}-{i % 10}"
        batch = _make_batch(rng)
        t0 = time.perf_counter()
        r = await client.post(f"{base}/v1/streams/{stream_id}/events", json=batch, timeout=10)
        elapsed_ms = (time.perf_counter() - t0) * 1000
        latencies.append(elapsed_ms)
        if r.status_code == 202:
            ok += 1
    return ok


@pytest.mark.asyncio
async def test_load_ingestion(live_server, resource_envelope, results_dir):
    """Fire concurrent clients and measure throughput + latency."""
    base = live_server["url"]
    latencies: list[float] = []
    total_ok = 0

    t_start = time.perf_counter()
    async with httpx.AsyncClient() as client:
        tasks = []
        for w in range(CONCURRENCY):
            rng = random.Random(w)
            tasks.append(_worker(client, base, w, REQUESTS_PER_CLIENT, latencies, rng))
        results = await asyncio.gather(*tasks)
        total_ok = sum(results)
    elapsed = time.perf_counter() - t_start

    rps = total_ok / elapsed if elapsed > 0 else 0
    latencies.sort()
    p50 = latencies[len(latencies) // 2] if latencies else 0
    p95 = latencies[int(len(latencies) * 0.95)] if latencies else 0
    p99 = latencies[int(len(latencies) * 0.99)] if latencies else 0

    result = {
        "test": "load_ingestion",
        "concurrency": CONCURRENCY,
        "total_requests": CONCURRENCY * REQUESTS_PER_CLIENT,
        "successful": total_ok,
        "elapsed_s": round(elapsed, 2),
        "rps": round(rps, 1),
        "p50_ms": round(p50, 2),
        "p95_ms": round(p95, 2),
        "p99_ms": round(p99, 2),
    }
    (results_dir / "load_results.json").write_text(json.dumps(result, indent=2))

    assert total_ok > 0, "No successful requests"
    assert rps >= resource_envelope["load_min_throughput_rps"], f"Throughput {rps:.1f} RPS is below {resource_envelope['load_min_throughput_rps']} RPS"
    assert p99 < resource_envelope["load_max_p99_ms"], f"p99 {p99:.1f}ms exceeds {resource_envelope['load_max_p99_ms']}ms"


async def _query_worker(client: httpx.AsyncClient, base: str, stream_id: str,
                        n: int, latencies: list) -> int:
    ok = 0
    for _ in range(n):
        t0 = time.perf_counter()
        r = await client.get(f"{base}/v1/streams/{stream_id}/metrics", timeout=10)
        elapsed_ms = (time.perf_counter() - t0) * 1000
        latencies.append(elapsed_ms)
        if r.status_code == 200:
            ok += 1
    return ok


@pytest.mark.asyncio
async def test_load_query_under_write(live_server, resource_envelope):
    """Mix ingestion and query traffic at 80/20 ratio."""
    base = live_server["url"]

    # Seed some data first
    async with httpx.AsyncClient() as client:
        for i in range(5):
            rng = random.Random(i)
            batch = _make_batch(rng)
            await client.post(f"{base}/v1/streams/mixed-{i}/events", json=batch, timeout=5)

    write_latencies: list[float] = []
    read_latencies: list[float] = []

    async with httpx.AsyncClient() as client:
        writers = [_worker(client, base, w, 20, write_latencies, random.Random(w + 100))
                   for w in range(8)]
        readers = [_query_worker(client, base, f"mixed-{i % 5}", 10, read_latencies)
                   for i in range(4)]
        await asyncio.gather(*writers, *readers)

    assert len(write_latencies) > 0
    assert len(read_latencies) > 0
    read_p99 = sorted(read_latencies)[int(len(read_latencies) * 0.99)] if read_latencies else 0
    assert read_p99 < resource_envelope["load_max_p99_ms"]


@pytest.mark.asyncio
async def test_load_multi_stream(live_server):
    """Create hundreds of distinct streams concurrently."""
    base = live_server["url"]
    n_streams = 200

    async with httpx.AsyncClient() as client:
        tasks = []
        for i in range(n_streams):
            batch = {"latencies": [float(i)]}
            tasks.append(client.post(f"{base}/v1/streams/multi-{i}/events", json=batch, timeout=10))
        responses = await asyncio.gather(*tasks)

    accepted = sum(1 for r in responses if r.status_code == 202)
    assert accepted == n_streams
