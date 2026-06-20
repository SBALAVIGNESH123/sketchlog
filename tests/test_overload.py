"""Overload tests — verify documented error responses under stress."""
import asyncio

import httpx
import pytest


pytestmark = [pytest.mark.stress, pytest.mark.overload]


@pytest.mark.asyncio
async def test_overload_request_size_413(live_server):
    """Requests exceeding MAX_REQUEST_BYTES get 413 with no partial mutation."""
    base = live_server["url"]
    stream_id = "overload-413"

    # Generate a ~1.5MB payload (default limit is 1MB)
    large_payload = {"uniques": ["x" * 1500000]}

    async with httpx.AsyncClient() as client:
        r = await client.post(f"{base}/v1/streams/{stream_id}/events",
                              json=large_payload, timeout=10)
        assert r.status_code == 413

        # Stream should NOT exist
        r2 = await client.get(f"{base}/v1/streams/{stream_id}/metrics", timeout=5)
        assert r2.status_code == 404


@pytest.mark.asyncio
async def test_overload_batch_size_422(live_server):
    """Batches exceeding MAX_BATCH_SIZE get 422 with no stream creation."""
    base = live_server["url"]
    stream_id = "overload-422"

    # Default MAX_BATCH_SIZE is 10000
    payload = {"latencies": [1.0] * 10001}

    async with httpx.AsyncClient() as client:
        r = await client.post(f"{base}/v1/streams/{stream_id}/events",
                              json=payload, timeout=10)
        assert r.status_code == 422

        r2 = await client.get(f"{base}/v1/streams/{stream_id}/metrics", timeout=5)
        assert r2.status_code == 404


@pytest.mark.asyncio
async def test_overload_stream_limit_eviction(live_server):
    """Creating MAX_STREAMS+N streams triggers LRU eviction correctly."""
    base = live_server["url"]

    async with httpx.AsyncClient() as client:
        # Create 1005 streams sequentially to guarantee LRU order
        for i in range(1005):
            batch = {"latencies": [float(i)]}
            r = await client.post(f"{base}/v1/streams/evict-{i}/events",
                                  json=batch, timeout=10)
            assert r.status_code == 202

        # Early streams should have been evicted
        r = await client.get(f"{base}/v1/streams/evict-0/metrics", timeout=5)
        assert r.status_code == 404

        # Latest should exist
        r = await client.get(f"{base}/v1/streams/evict-1004/metrics", timeout=5)
        assert r.status_code == 200


@pytest.mark.asyncio
async def test_overload_concurrent_saturation(live_server):
    """Blast the server with high concurrency — must not crash."""
    base = live_server["url"]
    n = 500

    async with httpx.AsyncClient() as client:
        tasks = []
        for i in range(n):
            batch = {"latencies": [float(i % 100)]}
            tasks.append(client.post(f"{base}/v1/streams/saturate-{i % 50}/events",
                                     json=batch, timeout=15))
        responses = await asyncio.gather(*tasks, return_exceptions=True)

    # At least some should succeed; server must not crash
    successes = sum(1 for r in responses if isinstance(r, httpx.Response) and r.status_code == 202)
    assert successes > 0, "No requests succeeded under saturation"

    for r in responses:
        if isinstance(r, httpx.Response) and r.status_code != 202:
            assert r.status_code in (422, 429, 503), f"Unexpected status code under load: {r.status_code}"

    # Server should still be alive
    async with httpx.AsyncClient() as client:
        health = await client.get(f"{base}/health", timeout=5)
        assert health.status_code == 200


@pytest.mark.asyncio
async def test_overload_atomic_rejection(live_server):
    """Invalid payloads interleaved with valid ones don't mutate state."""
    base = live_server["url"]
    stream_id = "atomic-check"

    async with httpx.AsyncClient() as client:
        # Valid request
        r = await client.post(f"{base}/v1/streams/{stream_id}/events",
                              json={"latencies": [10.0]}, timeout=5)
        assert r.status_code == 202

        # Get initial state
        r = await client.get(f"{base}/v1/streams/{stream_id}/metrics", timeout=5)
        initial_events = r.json()["total_events"]

        # Invalid: event count = 0 should be rejected
        r = await client.post(f"{base}/v1/streams/{stream_id}/events",
                              json={"events": {"bad": 0}}, timeout=5)
        assert r.status_code == 422

        # State should be unchanged
        r = await client.get(f"{base}/v1/streams/{stream_id}/metrics", timeout=5)
        assert r.json()["total_events"] == initial_events
