"""Tests for AsyncSketchLogClient — httpx-based, asyncio_mode=auto."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
import httpx

from sketchlog.async_client import (
    AsyncSketchLogClient,
    AsyncClientConfig,
    SketchLogAuthError,
    SketchLogRateLimitError,
    SketchLogServerError,
    SketchLogTimeoutError,
    SketchLogError,
)


def make_response(status: int, body: dict | None = None, headers: dict | None = None) -> httpx.Response:
    """Create a mock httpx.Response."""
    content = json_bytes(body or {})
    h = httpx.Headers({"content-type": "application/json", **(headers or {})})
    return httpx.Response(status_code=status, content=content, headers=h)


def json_bytes(d: dict) -> bytes:
    import json
    return json.dumps(d).encode()


# ── Config validation ─────────────────────────────────────────────────────────

def test_config_defaults():
    cfg = AsyncClientConfig(base_url="http://localhost:8080", token="tok")
    assert cfg.timeout == 30.0
    assert cfg.max_retries == 3
    assert cfg.pool_size == 100


def test_config_invalid_url():
    with pytest.raises(ValueError, match="base_url"):
        AsyncClientConfig(base_url="", token="tok")


def test_config_invalid_token():
    with pytest.raises(ValueError, match="token"):
        AsyncClientConfig(base_url="http://localhost:8080", token="")


def test_config_invalid_timeout():
    with pytest.raises(ValueError, match="timeout"):
        AsyncClientConfig(base_url="http://localhost:8080", token="tok", timeout=0)


def test_config_invalid_retries():
    with pytest.raises(ValueError, match="max_retries"):
        AsyncClientConfig(base_url="http://localhost:8080", token="tok", max_retries=-1)


def test_config_strips_trailing_slash():
    cfg = AsyncClientConfig(base_url="http://localhost:8080/", token="tok")
    assert not cfg.base_url.endswith("/")


# ── Lifecycle ─────────────────────────────────────────────────────────────────

async def test_context_manager():
    cfg = AsyncClientConfig(base_url="http://localhost:8080", token="tok")
    async with AsyncSketchLogClient(cfg) as client:
        assert client is not None


async def test_close_idempotent():
    cfg = AsyncClientConfig(base_url="http://localhost:8080", token="tok")
    client = AsyncSketchLogClient(cfg)
    await client.close()
    await client.close()  # should not raise


# ── HTTP helpers ──────────────────────────────────────────────────────────────

async def test_auth_error():
    cfg = AsyncClientConfig(base_url="http://localhost:8080", token="tok")
    async with AsyncSketchLogClient(cfg) as client:
        client._http.send = AsyncMock(return_value=make_response(401, {"error": "unauthorized"}))
        with pytest.raises(SketchLogAuthError):
            await client.health()


async def test_rate_limit_error():
    cfg = AsyncClientConfig(base_url="http://localhost:8080", token="tok", max_retries=1)
    async with AsyncSketchLogClient(cfg) as client:
        client._http.send = AsyncMock(return_value=make_response(429, {"error": "rate limit"}, {"retry-after": "0"}))
        with pytest.raises(SketchLogRateLimitError):
            await client.health()


async def test_server_error():
    cfg = AsyncClientConfig(base_url="http://localhost:8080", token="tok", max_retries=1)
    async with AsyncSketchLogClient(cfg) as client:
        client._http.send = AsyncMock(return_value=make_response(500, {"error": "internal"}))
        with pytest.raises(SketchLogServerError):
            await client.health()


async def test_timeout_error():
    cfg = AsyncClientConfig(base_url="http://localhost:8080", token="tok", max_retries=1)
    async with AsyncSketchLogClient(cfg) as client:
        client._http.send = AsyncMock(side_effect=httpx.TimeoutException("timed out"))
        with pytest.raises(SketchLogTimeoutError):
            await client.health()


# ── Health / Info ─────────────────────────────────────────────────────────────

async def test_health_ok():
    cfg = AsyncClientConfig(base_url="http://localhost:8080", token="tok")
    async with AsyncSketchLogClient(cfg) as client:
        client._http.send = AsyncMock(return_value=make_response(200, {"status": "ok"}))
        result = await client.health()
    assert result["status"] == "ok"


async def test_info_ok():
    cfg = AsyncClientConfig(base_url="http://localhost:8080", token="tok")
    async with AsyncSketchLogClient(cfg) as client:
        client._http.send = AsyncMock(return_value=make_response(200, {"version": "1.0"}))
        result = await client.info()
    assert result["version"] == "1.0"


# ── Ingest ────────────────────────────────────────────────────────────────────

async def test_ingest_ok():
    cfg = AsyncClientConfig(base_url="http://localhost:8080", token="tok")
    async with AsyncSketchLogClient(cfg) as client:
        client._http.send = AsyncMock(return_value=make_response(200, {"ingested": 1}))
        result = await client.ingest("ns", "stream", {"key": "val"})
    assert result["ingested"] == 1


async def test_ingest_batch_ok():
    cfg = AsyncClientConfig(base_url="http://localhost:8080", token="tok")
    async with AsyncSketchLogClient(cfg) as client:
        client._http.send = AsyncMock(return_value=make_response(200, {"ingested": 3}))
        result = await client.ingest_batch("ns", "stream", [{"a": 1}, {"b": 2}, {"c": 3}])
    assert result["ingested"] == 3


# ── Query ─────────────────────────────────────────────────────────────────────

async def test_query_ok():
    cfg = AsyncClientConfig(base_url="http://localhost:8080", token="tok")
    async with AsyncSketchLogClient(cfg) as client:
        client._http.send = AsyncMock(return_value=make_response(200, {"events": []}))
        result = await client.query("ns", "stream")
    assert "events" in result


async def test_query_with_limit():
    cfg = AsyncClientConfig(base_url="http://localhost:8080", token="tok")
    async with AsyncSketchLogClient(cfg) as client:
        client._http.send = AsyncMock(return_value=make_response(200, {"events": [{"id": 1}]}))
        result = await client.query("ns", "stream", limit=1)
    assert len(result["events"]) == 1


# ── Namespaces ────────────────────────────────────────────────────────────────

async def test_list_namespaces():
    cfg = AsyncClientConfig(base_url="http://localhost:8080", token="tok")
    async with AsyncSketchLogClient(cfg) as client:
        client._http.send = AsyncMock(return_value=make_response(200, {"namespaces": ["ns1"]}))
        result = await client.list_namespaces()
    assert "namespaces" in result


async def test_create_namespace():
    cfg = AsyncClientConfig(base_url="http://localhost:8080", token="tok")
    async with AsyncSketchLogClient(cfg) as client:
        client._http.send = AsyncMock(return_value=make_response(201, {"name": "ns1"}))
        result = await client.create_namespace("ns1")
    assert result["name"] == "ns1"


async def test_delete_namespace():
    cfg = AsyncClientConfig(base_url="http://localhost:8080", token="tok")
    async with AsyncSketchLogClient(cfg) as client:
        client._http.send = AsyncMock(return_value=make_response(200, {"deleted": True}))
        result = await client.delete_namespace("ns1")
    assert result["deleted"] is True


# ── Streams ───────────────────────────────────────────────────────────────────

async def test_list_streams():
    cfg = AsyncClientConfig(base_url="http://localhost:8080", token="tok")
    async with AsyncSketchLogClient(cfg) as client:
        client._http.send = AsyncMock(return_value=make_response(200, {"streams": ["s1"]}))
        result = await client.list_streams("ns1")
    assert "streams" in result


async def test_create_stream():
    cfg = AsyncClientConfig(base_url="http://localhost:8080", token="tok")
    async with AsyncSketchLogClient(cfg) as client:
        client._http.send = AsyncMock(return_value=make_response(201, {"name": "s1"}))
        result = await client.create_stream("ns1", "s1")
    assert result["name"] == "s1"


async def test_delete_stream():
    cfg = AsyncClientConfig(base_url="http://localhost:8080", token="tok")
    async with AsyncSketchLogClient(cfg) as client:
        client._http.send = AsyncMock(return_value=make_response(200, {"deleted": True}))
        result = await client.delete_stream("ns1", "s1")
    assert result["deleted"] is True


# ── Retry logic ───────────────────────────────────────────────────────────────

async def test_retry_on_500_then_success():
    cfg = AsyncClientConfig(base_url="http://localhost:8080", token="tok", max_retries=3)
    async with AsyncSketchLogClient(cfg) as client:
        client._http.send = AsyncMock(side_effect=[
            make_response(500, {"error": "internal"}),
            make_response(500, {"error": "internal"}),
            make_response(200, {"status": "ok"}),
        ])
        result = await client.health()
    assert result["status"] == "ok"


async def test_no_retry_on_400():
    cfg = AsyncClientConfig(base_url="http://localhost:8080", token="tok", max_retries=3)
    async with AsyncSketchLogClient(cfg) as client:
        client._http.send = AsyncMock(return_value=make_response(400, {"error": "bad request"}))
        with pytest.raises(SketchLogError):
            await client.health()
    # should only be called once — no retry on 4xx
    assert client._http.send.call_count == 1


async def test_retry_exhausted_raises_server_error():
    cfg = AsyncClientConfig(base_url="http://localhost:8080", token="tok", max_retries=2)
    async with AsyncSketchLogClient(cfg) as client:
        client._http.send = AsyncMock(return_value=make_response(503, {"error": "unavailable"}))
        with pytest.raises(SketchLogServerError):
            await client.health()


# ── URL building ──────────────────────────────────────────────────────────────

async def test_url_no_double_slash():
    cfg = AsyncClientConfig(base_url="http://localhost:8080/", token="tok")
    async with AsyncSketchLogClient(cfg) as client:
        client._http.send = AsyncMock(return_value=make_response(200, {}))
        await client.health()
    url = str(client._http.send.call_args[0][0].url)
    assert "//" not in url.replace("http://", "")


# ── Cancellation ──────────────────────────────────────────────────────────────

async def test_cancellation_safe():
    cfg = AsyncClientConfig(base_url="http://localhost:8080", token="tok")
    async with AsyncSketchLogClient(cfg) as client:
        async def slow():
            await asyncio.sleep(10)
        client._http.send = AsyncMock(side_effect=slow)
        task = asyncio.create_task(client.health())
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
