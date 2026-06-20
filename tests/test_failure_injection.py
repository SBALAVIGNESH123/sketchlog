"""Failure injection tests — malformed payloads, disconnects, corruption."""
import asyncio
import socket
import time

import httpx
import pytest


pytestmark = [pytest.mark.stress, pytest.mark.failure_injection]


@pytest.mark.asyncio
async def test_malformed_json_payloads(live_server):
    """Truncated JSON, invalid UTF-8, wrong content types must not crash."""
    base = live_server["url"]
    stream_id = "malformed-test"

    malformed_cases = [
        # Truncated JSON
        (b'{"latencies": [1.0', "application/json"),
        # Invalid UTF-8
        (b'{"uniques": ["\xff\xfe"]}', "application/json"),
        # Wrong content type
        (b'<xml>not json</xml>', "application/xml"),
        # Empty body
        (b'', "application/json"),
        # Nested bomb (deeply nested arrays)
        (b'{"latencies": ' + b'[' * 100 + b'1' + b']' * 100 + b'}', "application/json"),
        # Null bytes
        (b'{"latencies": [\x00]}', "application/json"),
    ]

    async with httpx.AsyncClient() as client:
        for body, content_type in malformed_cases:
            r = await client.post(
                f"{base}/v1/streams/{stream_id}/events",
                content=body,
                headers={"Content-Type": content_type},
                timeout=5,
            )
            assert r.status_code in (400, 413, 422), \
                f"Expected 4xx for malformed payload, got {r.status_code}"

        # Server should still be alive
        health = await client.get(f"{base}/health", timeout=5)
        assert health.status_code == 200


@pytest.mark.asyncio
async def test_abrupt_client_disconnect(live_server):
    """Open connection, send partial data, then close. Server must survive."""
    port = live_server["port"]

    # Open raw socket, send partial HTTP, then close abruptly
    for _ in range(10):
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(2)
            sock.connect(("127.0.0.1", port))
            sock.sendall(b"POST /v1/streams/disconnect-test/events HTTP/1.1\r\n")
            sock.sendall(b"Content-Type: application/json\r\n")
            sock.sendall(b"Content-Length: 999999\r\n\r\n")
            sock.sendall(b'{"latencies": [1.0')  # Incomplete
            sock.close()
        except Exception:
            pass

    # Give server a moment to recover
    await asyncio.sleep(0.5)

    # Server should still be healthy
    async with httpx.AsyncClient() as client:
        r = await client.get(f"{live_server['url']}/health", timeout=5)
        assert r.status_code == 200

        # Normal request should still work
        r = await client.post(
            f"{live_server['url']}/v1/streams/after-disconnect/events",
            json={"latencies": [42.0]},
            timeout=5,
        )
        assert r.status_code == 202


@pytest.mark.asyncio
async def test_rapid_connect_disconnect(live_server):
    """Open and close hundreds of connections without sending data."""
    port = live_server["port"]

    for _ in range(200):
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1)
            sock.connect(("127.0.0.1", port))
            sock.close()
        except Exception:
            pass

    await asyncio.sleep(0.5)

    # Server must still be healthy
    async with httpx.AsyncClient() as client:
        r = await client.get(f"{live_server['url']}/health", timeout=5)
        assert r.status_code == 200


@pytest.mark.asyncio
async def test_concurrent_stream_deletion(live_server):
    """Simultaneously ingest to and delete the same stream — no corruption."""
    base = live_server["url"]
    stream_id = "race-condition"

    async with httpx.AsyncClient() as client:
        # Create the stream
        await client.post(f"{base}/v1/streams/{stream_id}/events",
                          json={"latencies": [1.0]}, timeout=5)

        # Race: ingestion vs deletion
        async def ingest():
            for _ in range(50):
                await client.post(f"{base}/v1/streams/{stream_id}/events",
                                  json={"latencies": [2.0]}, timeout=5)

        async def delete():
            for _ in range(50):
                await client.delete(f"{base}/v1/streams/{stream_id}", timeout=5)
                await asyncio.sleep(0.01)

        await asyncio.gather(ingest(), delete(), return_exceptions=True)

        # Server must be healthy, and stream state must be consistent
        health = await client.get(f"{base}/health", timeout=5)
        assert health.status_code == 200

        # Either stream exists with valid metrics, or it's been deleted
        r = await client.get(f"{base}/v1/streams/{stream_id}/metrics", timeout=5)
        assert r.status_code in (200, 404)
        if r.status_code == 200:
            data = r.json()
            assert data["total_events"] >= 0


@pytest.mark.asyncio
async def test_large_stream_id(live_server):
    """Very long stream IDs should be rejected cleanly."""
    base = live_server["url"]
    long_id = "x" * 300  # Exceeds 255 limit

    async with httpx.AsyncClient() as client:
        r = await client.post(f"{base}/v1/streams/{long_id}/events",
                              json={"latencies": [1.0]}, timeout=5)
        assert r.status_code == 422

        # Server still healthy
        health = await client.get(f"{base}/health", timeout=5)
        assert health.status_code == 200
