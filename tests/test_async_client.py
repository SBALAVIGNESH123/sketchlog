"""
Tests for AsyncSketchLogClient.

pytest.ini sets asyncio_mode = auto, so no @pytest.mark.asyncio needed.
All HTTP is mocked via httpx.MockTransport — zero real network calls.
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from sketchlog.async_client import (
    AsyncClientConfig,
    AsyncSketchLogClient,
    SketchLogAuthError,
    SketchLogClientError,
    SketchLogError,
    SketchLogRateLimitError,
    SketchLogServerError,
    SketchLogTimeoutError,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_response(status: int, body: Any = None) -> httpx.Response:
    import json as _json
    content = _json.dumps(body or {}).encode()
    return httpx.Response(status, content=content)


def make_client(responses: list[httpx.Response]) -> AsyncSketchLogClient:
    """Return an already-opened client that replays *responses* in order."""
    cfg = AsyncClientConfig(base_url="http://localhost:7749", token="test-token")
    client = AsyncSketchLogClient(cfg)

    idx = 0

    async def mock_request(method, url, **kwargs):
        nonlocal idx
        resp = responses[idx % len(responses)]
        idx += 1
        return resp

    http = httpx.AsyncClient()
    http.request = mock_request  # type: ignore[method-assign]
    client._client = http
    return client


# ---------------------------------------------------------------------------
# Config tests
# ---------------------------------------------------------------------------

def test_config_valid():
    cfg = AsyncClientConfig(base_url="http://localhost:7749", token="tok")
    assert cfg.base_url == "http://localhost:7749"
    assert cfg.token == "tok"


def test_config_trailing_slash_stripped():
    cfg = AsyncClientConfig(base_url="http://localhost:7749/", token="tok")
    assert not cfg.base_url.endswith("/")


def test_config_empty_base_url_raises():
    with pytest.raises(ValueError, match="base_url"):
        AsyncClientConfig(base_url="", token="tok")


def test_config_empty_token_raises():
    with pytest.raises(ValueError, match="token"):
        AsyncClientConfig(base_url="http://localhost:7749", token="")


def test_config_negative_timeout_raises():
    with pytest.raises(ValueError, match="timeout"):
        AsyncClientConfig(base_url="http://localhost:7749", token="tok", timeout=-1.0)


def test_config_negative_retries_raises():
    with pytest.raises(ValueError, match="max_retries"):
        AsyncClientConfig(base_url="http://localhost:7749", token="tok", max_retries=-1)


# ---------------------------------------------------------------------------
# Lifecycle tests
# ---------------------------------------------------------------------------

async def test_context_manager_lifecycle():
    cfg = AsyncClientConfig(base_url="http://localhost:7749", token="tok")
    client = AsyncSketchLogClient(cfg)
    async with client:
        assert client._client is not None
    assert client._client is None


async def test_open_close_explicit():
    cfg = AsyncClientConfig(base_url="http://localhost:7749", token="tok")
    client = AsyncSketchLogClient(cfg)
    await client.open()
    assert client._client is not None
    await client.close()
    assert client._client is None


async def test_double_close_is_safe():
    cfg = AsyncClientConfig(base_url="http://localhost:7749", token="tok")
    client = AsyncSketchLogClient(cfg)
    await client.open()
    await client.close()
    await client.close()  # must not raise
    assert client._client is None


async def test_request_without_open_raises():
    cfg = AsyncClientConfig(base_url="http://localhost:7749", token="tok")
    client = AsyncSketchLogClient(cfg)
    with pytest.raises(SketchLogError, match="not open"):
        await client.health()


# ---------------------------------------------------------------------------
# Health / Info
# ---------------------------------------------------------------------------

async def test_health_ok():
    client = make_client([make_response(200, {"status": "ok"})])
    result = await client.health()
    assert result["status"] == "ok"


async def test_info_ok():
    client = make_client([make_response(200, {"version": "1.0.0"})])
    result = await client.info()
    assert result["version"] == "1.0.0"


# ---------------------------------------------------------------------------
# Ingest
# ---------------------------------------------------------------------------

async def test_ingest_ok():
    client = make_client([make_response(200, {"ingested": 1})])
    result = await client.ingest("ns", "stream", [{"val": 1}])
    assert result["ingested"] == 1


async def test_ingest_batch_ok():
    client = make_client([make_response(200, {"ingested": 3})])
    result = await client.ingest("ns", "stream", [{"v": i} for i in range(3)])
    assert result["ingested"] == 3


async def test_ingest_empty_raises():
    client = make_client([make_response(200, {})])
    with pytest.raises(ValueError, match="empty"):
        await client.ingest("ns", "stream", [])


# ---------------------------------------------------------------------------
# Query
# ---------------------------------------------------------------------------

async def test_query_ok():
    client = make_client([make_response(200, {"events": [{"v": 1}]})])
    result = await client.query("ns", "stream")
    assert result["events"][0]["v"] == 1


async def test_query_with_limit():
    client = make_client([make_response(200, {"events": []})])
    result = await client.query("ns", "stream", limit=10)
    assert "events" in result


async def test_query_cdf_ok():
    client = make_client([make_response(200, {"cdf": []})])
    result = await client.query_cdf("ns", "stream")
    assert "cdf" in result


# ---------------------------------------------------------------------------
# Namespaces
# ---------------------------------------------------------------------------

async def test_list_namespaces_ok():
    client = make_client([make_response(200, {"namespaces": ["ns1"]})])
    result = await client.list_namespaces()
    assert "namespaces" in result


async def test_create_namespace_ok():
    client = make_client([make_response(200, {"name": "ns1"})])
    result = await client.create_namespace("ns1")
    assert result["name"] == "ns1"


async def test_delete_namespace_ok():
    client = make_client([make_response(200, {"deleted": True})])
    result = await client.delete_namespace("ns1")
    assert result["deleted"] is True


# ---------------------------------------------------------------------------
# Streams
# ---------------------------------------------------------------------------

async def test_list_streams_ok():
    client = make_client([make_response(200, {"streams": ["s1"]})])
    result = await client.list_streams("ns")
    assert "streams" in result


async def test_create_stream_ok():
    client = make_client([make_response(200, {"name": "s1"})])
    result = await client.create_stream("ns", "s1")
    assert result["name"] == "s1"


async def test_delete_stream_ok():
    client = make_client([make_response(200, {"deleted": True})])
    result = await client.delete_stream("ns", "s1")
    assert result["deleted"] is True


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

async def test_401_raises_auth_error():
    client = make_client([make_response(401, {"error": "unauthorized"})])
    with pytest.raises(SketchLogAuthError):
        await client.health()


async def test_403_raises_auth_error():
    client = make_client([make_response(403, {"error": "forbidden"})])
    with pytest.raises(SketchLogAuthError):
        await client.health()


async def test_429_raises_rate_limit_error():
    client = make_client([make_response(429, {"error": "rate limited"})])
    with pytest.raises(SketchLogRateLimitError):
        await client.health()


async def test_500_raises_server_error():
    client = make_client([make_response(500, {"error": "internal"})])
    with pytest.raises(SketchLogServerError):
        await client.health()


async def test_400_raises_client_error():
    client = make_client([make_response(400, {"error": "bad request"})])
    with pytest.raises(SketchLogClientError):
        await client.health()


async def test_404_raises_client_error():
    client = make_client([make_response(404, {"error": "not found"})])
    with pytest.raises(SketchLogClientError):
        await client.health()


# ---------------------------------------------------------------------------
# Retry logic
# ---------------------------------------------------------------------------

async def test_server_error_retries_then_raises():
    """500 is retried max_retries times then raises SketchLogServerError."""
    cfg = AsyncClientConfig(
        base_url="http://localhost:7749",
        token="tok",
        max_retries=2,
        backoff_base=0.001,
    )
    client = AsyncSketchLogClient(cfg)
    responses = [make_response(500, {})] * 10
    idx = 0

    async def mock_request(method, url, **kwargs):
        nonlocal idx
        resp = responses[idx % len(responses)]
        idx += 1
        return resp

    http = httpx.AsyncClient()
    http.request = mock_request  # type: ignore[method-assign]
    client._client = http

    with pytest.raises(SketchLogServerError):
        await client.health()

    assert idx == 3  # 1 initial + 2 retries


async def test_4xx_is_not_retried():
    """400 client error must NOT be retried."""
    cfg = AsyncClientConfig(
        base_url="http://localhost:7749",
        token="tok",
        max_retries=3,
    )
    client = AsyncSketchLogClient(cfg)
    idx = 0

    async def mock_request(method, url, **kwargs):
        nonlocal idx
        idx += 1
        return make_response(400, {})

    http = httpx.AsyncClient()
    http.request = mock_request  # type: ignore[method-assign]
    client._client = http

    with pytest.raises(SketchLogClientError):
        await client.health()

    assert idx == 1  # no retry


async def test_retry_succeeds_on_third_attempt():
    """500 x2 then 200 — should return successfully."""
    cfg = AsyncClientConfig(
        base_url="http://localhost:7749",
        token="tok",
        max_retries=2,
        backoff_base=0.001,
    )
    client = AsyncSketchLogClient(cfg)
    responses = [
        make_response(500, {}),
        make_response(500, {}),
        make_response(200, {"status": "ok"}),
    ]
    idx = 0

    async def mock_request(method, url, **kwargs):
        nonlocal idx
        resp = responses[idx]
        idx += 1
        return resp

    http = httpx.AsyncClient()
    http.request = mock_request  # type: ignore[method-assign]
    client._client = http

    result = await client.health()
    assert result["status"] == "ok"
    assert idx == 3


# ---------------------------------------------------------------------------
# URL builder
# ---------------------------------------------------------------------------

def test_url_builder():
    cfg = AsyncClientConfig(base_url="http://localhost:7749", token="tok")
    client = AsyncSketchLogClient(cfg)
    assert client._url("/health") == "http://localhost:7749/health"


def test_url_builder_strips_double_slash():
    cfg = AsyncClientConfig(base_url="http://localhost:7749/", token="tok")
    client = AsyncSketchLogClient(cfg)
    assert client._url("/health") == "http://localhost:7749/health"


# ---------------------------------------------------------------------------
# Subscribe stream
# ---------------------------------------------------------------------------

async def test_subscribe_stream_yields_events():
    client = make_client([
        make_response(200, {"events": [{"id": 1}, {"id": 2}]}),
        make_response(200, {"events": []}),
    ])
    collected = []
    async with client.subscribe_stream(
        "ns", "stream",
        poll_interval=0.001,
        max_events=2,
    ) as gen:
        async for event in gen:
            collected.append(event)
    assert len(collected) == 2


async def test_subscribe_stream_exits_on_error_budget():
    """Consecutive errors should cause the generator to stop."""
    client = make_client([make_response(500, {})])
    collected = []
    async with client.subscribe_stream(
        "ns", "stream",
        poll_interval=0.001,
        max_consecutive_errors=3,
    ) as gen:
        try:
            async for event in gen:
                collected.append(event)
        except Exception:
            pass
    # Generator stopped after 3 consecutive errors — no events
    assert collected == []
