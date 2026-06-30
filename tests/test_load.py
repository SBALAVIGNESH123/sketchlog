"""Load tests — throughput and latency under controlled concurrency."""
import asyncio
import json
import os
import random
import statistics
import time
from pathlib import Path

import httpx
import pytest


pytestmark = [pytest.mark.stress, pytest.mark.load]


def _positive_int_env(name: str, default: int) -> int:
    value = int(os.environ.get(name, str(default)))
    if value < 1:
        raise ValueError(f"{name} must be at least 1, got {value}")
    return value


def _assert_p99_within_ceiling(p99: float, maximum_p99_ms: float) -> None:
    assert p99 <= maximum_p99_ms, (
        f"p99 {p99:.1f}ms exceeds {maximum_p99_ms:.1f}ms")


CONCURRENCY = int(os.environ.get("LOAD_CONCURRENCY", "20"))
REQUESTS_PER_CLIENT = int(os.environ.get("LOAD_REQUESTS", "50"))
LOAD_TRIALS = _positive_int_env("LOAD_TRIALS", 1)


def _make_batch(rng: random.Random) -> dict:
    return {
        "latencies": [rng.lognormvariate(2, 0.5) for _ in range(50)],
        "events": {"api_call": rng.randint(1, 10), "cache_miss": rng.randint(1, 5)},
        "uniques": [f"user-{rng.randint(1, 1000)}" for _ in range(5)],
    }


async def _worker(client: httpx.AsyncClient, base: str, worker_id: int,
                  n: int, latencies: list, rng: random.Random,
                  stream_prefix: str = "load") -> int:
    ok = 0
    for i in range(n):
        stream_id = f"{stream_prefix}-{worker_id}-{i % 10}"
        batch = _make_batch(rng)
        t0 = time.perf_counter()
        try:
            r = await client.post(f"{base}/v1/streams/{stream_id}/events", json=batch, timeout=10)
            elapsed_ms = (time.perf_counter() - t0) * 1000
            latencies.append(elapsed_ms)
            if r.status_code == 202:
                ok += 1
        except httpx.RequestError:
            pass
    return ok


@pytest.mark.asyncio
async def test_load_ingestion(live_server, results_dir):
    """Verify reliable ingestion and optionally enforce controlled throughput."""
    base = live_server["url"]
    trial_results: list[dict[str, float | int]] = []
    all_latencies: list[float] = []

    async with httpx.AsyncClient() as client:
        for trial in range(LOAD_TRIALS):
            latencies: list[float] = []
            t_start = time.perf_counter()
            tasks = [
                _worker(
                    client,
                    base,
                    worker_id,
                    REQUESTS_PER_CLIENT,
                    latencies,
                    random.Random(trial * CONCURRENCY + worker_id),
                    stream_prefix=f"load-trial-{trial}",
                )
                for worker_id in range(CONCURRENCY)
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            exceptions = [r for r in results if isinstance(r, Exception)]
            assert not exceptions, (
                f"Load workers crashed with exceptions: {exceptions}")
            total_ok = sum(results)
            elapsed = time.perf_counter() - t_start
            trial_results.append(
                {
                    "trial": trial + 1,
                    "successful": total_ok,
                    "elapsed_s": round(elapsed, 2),
                    "rps": round(total_ok / elapsed if elapsed > 0 else 0, 1),
                }
            )
            all_latencies.extend(latencies)

    expected_per_trial = CONCURRENCY * REQUESTS_PER_CLIENT
    assert all(
        trial["successful"] == expected_per_trial
        for trial in trial_results
    ), f"Not every ingestion request succeeded: {trial_results}"

    median_rps = statistics.median(
        float(trial["rps"]) for trial in trial_results)
    all_latencies.sort()
    p50 = all_latencies[len(all_latencies) // 2] if all_latencies else 0
    p95 = all_latencies[
        min(int(len(all_latencies) * 0.95), len(all_latencies) - 1)
    ] if all_latencies else 0
    p99 = all_latencies[
        min(int(len(all_latencies) * 0.99), len(all_latencies) - 1)
    ] if all_latencies else 0

    result = {
        "test": "load_ingestion",
        "concurrency": CONCURRENCY,
        "trials": trial_results,
        "total_requests": expected_per_trial * LOAD_TRIALS,
        "successful": sum(
            int(trial["successful"]) for trial in trial_results),
        "median_rps": round(median_rps, 1),
        "p50_ms": round(p50, 2),
        "p95_ms": round(p95, 2),
        "p99_ms": round(p99, 2),
    }
    (results_dir / "load_results.json").write_text(json.dumps(result, indent=2))

    configured_floor = os.environ.get("LOAD_MIN_RPS")
    if configured_floor is not None:
        minimum_rps = float(configured_floor)
        assert median_rps >= minimum_rps, (
            f"Median throughput {median_rps:.1f} RPS is below "
            f"{minimum_rps:.1f} RPS across {LOAD_TRIALS} trial(s)"
        )
    configured_p99_ceiling = os.environ.get("LOAD_MAX_P99_MS")
    if configured_p99_ceiling is not None:
        maximum_p99_ms = float(configured_p99_ceiling)
        _assert_p99_within_ceiling(p99, maximum_p99_ms)


@pytest.mark.parametrize("configured_value", ["0", "-1"])
def test_load_trials_must_be_positive(monkeypatch, configured_value):
    """Reject invalid trial counts before attempting to aggregate results."""
    monkeypatch.setenv("TEST_LOAD_TRIALS", configured_value)
    with pytest.raises(ValueError, match="must be at least 1"):
        _positive_int_env("TEST_LOAD_TRIALS", 1)


def test_p99_ceiling_accepts_exact_boundary():
    """Treat a measurement equal to the configured maximum as acceptable."""
    _assert_p99_within_ceiling(2000.0, 2000.0)


async def _query_worker(client: httpx.AsyncClient, base: str, stream_id: str,
                        n: int, latencies: list) -> int:
    ok = 0
    for _ in range(n):
        t0 = time.perf_counter()
        try:
            r = await client.get(f"{base}/v1/streams/{stream_id}/metrics", timeout=5)
            elapsed_ms = (time.perf_counter() - t0) * 1000
            latencies.append(elapsed_ms)
            if r.status_code == 200:
                ok += 1
        except httpx.RequestError:
            pass
    return ok


@pytest.mark.asyncio
async def test_load_query_under_write(live_server):
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
        results = await asyncio.gather(*writers, *readers, return_exceptions=True)

    exceptions = [r for r in results if isinstance(r, Exception)]
    assert not exceptions, f"Workers crashed with exceptions: {exceptions}"
    writer_results = results[:8]
    reader_results = results[8:]
    assert sum(writer_results) == 8 * 20, (
        "Not every mixed-load write request succeeded")
    assert sum(reader_results) == 4 * 10, (
        "Not every mixed-load read request succeeded")

    assert len(write_latencies) > 0
    assert len(read_latencies) > 0
    read_p99 = sorted(read_latencies)[min(int(len(read_latencies) * 0.99), len(read_latencies) - 1)] if read_latencies else 0
    configured_p99_ceiling = os.environ.get("LOAD_MAX_P99_MS")
    if configured_p99_ceiling is not None:
        assert read_p99 < float(configured_p99_ceiling)


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
        responses = await asyncio.gather(*tasks, return_exceptions=True)

    accepted = sum(1 for r in responses if isinstance(r, httpx.Response) and r.status_code == 202)
    assert accepted == n_streams
