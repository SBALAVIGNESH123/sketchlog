"""
Tests for AsyncSketchLogClient.
pytest.ini sets asyncio_mode = auto — no @pytest.mark.asyncio needed.
All HTTP is mocked via httpx.MockTransport — zero real network calls.
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List
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

def make_transport(status: int = 200, body: Any = None) -> httpx.MockTransport:
    if body is None:
        body = {"ok": True}
    import json as _json
    raw = _json.dumps(body).encode()
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, content=raw, headers={"content-type": "application/json"})
    return httpx.MockTransport(handler)


def make_client(status: int = 200, body: Any = None) -> AsyncSketchLogClient:
    transport = make_transport(status, body)
    cfg = AsyncClientConfig(base_url="http://localhost:7749", token="test-token")
    client = AsyncSketchLogClient(cfg)
    client._client = httpx.AsyncClient(
        base_url="http://localhost:7749",
        transport=transport,
        headers={"Authorization": "Bearer test-token"},
    )
    return client


# ---------------------------------------------------------------------------
# Config tests
# ---------------------------------------------------------------------------

def test_config_valid() -> None:
    cfg = AsyncClientConfig(base_url="http://localhost:7749", token="tok")
    assert cfg.base_url == "http://localhost:7749"
    assert cfg.token == "tok"


def test_config_trailing_slash_stripped() -> None:
    cfg = AsyncClientConfig(base_url="http://localhost:7749/", token="tok")
    assert not cfg.base_url.endswith("/")


def test_config_empty_url_raises() -> None:
    with pytest.raises(ValueError, match="base_url"):
        AsyncClientConfig(base_url="", token="tok")


def test_config_empty_token_raises() -> None:
    with pytest.raises(ValueError, match="token"):
        AsyncClientConfig(base_url="http://localhost:7749", token="")


def test_config_negative_timeout_raises() -> None:
    with pytest.raises(ValueError, match="timeout"):
        AsyncClientConfig(base_url="http://localhost:7749", token="tok", timeout=-1.0)


def test_config_negative_retries_raises() -> None:
    with pytest.raises(ValueError, match="max_retries"):
        AsyncClientConfig(base_url="http://localhost:7749", token="tok", max_retries=-1)


# ---------------------------------------------------------------------------
# Lifecycle tests
# ---------------------------------------------------------------------------

async def test_context_manager_lifecycle() -> None:
    cfg = AsyncClientConfig(base_url="http://localhost:7749", token="tok")
    async with AsyncSketchLogClient(cfg) as client:
        assert client._client is not None
    assert client._client is None


async def test_open_close_explicit() -> None:
    cfg = AsyncClientConfig(base_url="http://localhost:7749", token="tok")
    client = AsyncSketchLogClient(cfg)
    await client.open()
    assert client._client is not None
    await client.close()
    assert client._client is None


async def test_double_close_is_safe() -> None:
    cfg = AsyncClientConfig(base_url="http://localhost:7749", token="tok")
    client = AsyncSketchLogClient(cfg)
    await client.open()
    await client.close()
    await client.close()  # should not raise


async def test_request_without_open_raises() -> None:
    cfg = AsyncClientConfig(base_url="http://localhost:7749", token="tok")
    client = AsyncSketchLogClient(cfg)
    with pytest.raises(RuntimeError, match="not open"):
        await client.health()


# ---------------------------------------------------------------------------
# URL helper
# ---------------------------------------------------------------------------

def test_url_builder() -> None:
    cfg = AsyncClientConfig(base_url="http://localhost:7749", token="tok")
    client = AsyncSketchLogClient(cfg)
    assert client._url("/health") == "health"


def test_url_builder_strips_double_slash() -> None:
    cfg = AsyncClientConfig(base_url="http://localhost:7749", token="tok")
    client = AsyncSketchLogClient(cfg)
    assert not client._url("/streams/foo").startswith("/")


# ---------------------------------------------------------------------------
# HTTP error mapping
# ---------------------------------------------------------------------------

async def test_auth_error_401() -> None:
    client = make_client(401)
    with pytest.raises(SketchLogAuthError):
        await client.health()


async def test_auth_error_403() -> None:
    client = make_client(403)
    with pytest.raises(SketchLogAuthError):
        await client.health()


async def test_rate_limit_error_429() -> None:
    client = make_client(429)
    with pytest.raises(SketchLogRateLimitError):
        await client.health()


async def test_server_error_500() -> None:
    cfg = AsyncClientConfig(base_url="http://localhost:7749", token="tok", max_retries=0)
    transport = make_transport(500)
    client = AsyncSketchLogClient(cfg)
    client._client = httpx.AsyncClient(
        base_url="http://localhost:7749",
        transport=transport,
        headers={"Authorization": "Bearer tok"},
    )
    with pytest.raises(SketchLogServerError):
        await client.health()


async def test_client_error_404() -> None:
    client = make_client(404)
    with pytest.raises(SketchLogClientError):
        await client.health()


async def test_client_error_400() -> None:
    client = make_client(400)
    with pytest.raises(SketchLogClientError):
        await client.ingest("s", [{"k": "v"}])


# ---------------------------------------------------------------------------
# Health / Info
# ---------------------------------------------------------------------------

async def test_health_ok() -> None:
    client = make_client(200, {"status": "ok"})
    result = await client.health()
    assert result["status"] == "ok"


async def test_info_ok() -> None:
    client = make_client(200, {"version": "1.0"})
    result = await client.info()
    assert result["version"] == "1.0"


# ---------------------------------------------------------------------------
# Ingest
# ---------------------------------------------------------------------------

async def test_ingest_ok() -> None:
    client = make_client(200, {"ingested": 1})
    result = await client.ingest("my-stream", [{"ts": 1, "val": 42}])
    assert result["ingested"] == 1


async def test_ingest_empty_raises() -> None:
    client = make_client(200)
    with pytest.raises(ValueError, match="empty"):
        await client.ingest("my-stream", [])


# ---------------------------------------------------------------------------
# Query
# ---------------------------------------------------------------------------

async def test_query_ok() -> None:
    client = make_client(200, {"events": []})
    result = await client.query("my-stream")
    assert "events" in result


async def test_query_with_limit() -> None:
    client = make_client(200, {"events": []})
    result = await client.query("my-stream", limit=10)
    assert "events" in result


async def test_query_cdf_ok() -> None:
    client = make_client(200, {"cdf": []})
    result = await client.query_cdf("my-stream")
    assert "cdf" in result


# ---------------------------------------------------------------------------
# Namespaces
# ---------------------------------------------------------------------------

async def test_list_namespaces_ok() -> None:
    client = make_client(200, {"namespaces": []})
    result = await client.list_namespaces()
    assert "namespaces" in result


async def test_create_namespace_ok() -> None:
    client = make_client(200, {"name": "ns1"})
    result = await client.create_namespace("ns1")
    assert result["name"] == "ns1"


async def test_delete_namespace_ok() -> None:
    client = make_client(200, {"deleted": True})
    result = await client.delete_namespace("ns1")
    assert result["deleted"] is True


# ---------------------------------------------------------------------------
# Streams
# ---------------------------------------------------------------------------

async def test_list_streams_ok() -> None:
    client = make_client(200, {"streams": []})
    result = await client.list_streams()
    assert "streams" in result


async def test_create_stream_ok() -> None:
    client = make_client(200, {"name": "s1"})
    result = await client.create_stream("s1")
    assert result["name"] == "s1"


async def test_delete_stream_ok() -> None:
    client = make_client(200, {"deleted": True})
    result = await client.delete_stream("s1")
    assert result["deleted"] is True


# ---------------------------------------------------------------------------
# Retry logic
# ---------------------------------------------------------------------------

async def test_retry_on_server_error_then_success() -> None:
    import json as _json
    call_count = 0
    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            return httpx.Response(500, content=b"{}", headers={"content-type": "application/json"})
        return httpx.Response(200, content=_json.dumps({"ok": True}).encode(),
                              headers={"content-type": "application/json"})
    cfg = AsyncClientConfig(base_url="http://localhost:7749", token="tok", max_retries=3)
    client = AsyncSketchLogClient(cfg)
    client._client = httpx.AsyncClient(
        base_url="http://localhost:7749",
        transport=httpx.MockTransport(handler),
        headers={"Authorization": "Bearer tok"},
    )
    with patch("asyncio.sleep", new_callable=AsyncMock):
        result = await client.health()
    assert result["ok"] is True
    assert call_count == 3


async def test_no_retry_on_4xx() -> None:
    call_count = 0
    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(400, content=b"{}", headers={"content-type": "application/json"})
    cfg = AsyncClientConfig(base_url="http://localhost:7749", token="tok", max_retries=3)
    client = AsyncSketchLogClient(cfg)
    client._client = httpx.AsyncClient(
        base_url="http://localhost:7749",
        transport=httpx.MockTransport(handler),
        headers={"Authorization": "Bearer tok"},
    )
    with pytest.raises(SketchLogClientError):
        await client.health()
    assert call_count == 1


async def test_exhausted_retries_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, content=b"{}", headers={"content-type": "application/json"})
    cfg = AsyncClientConfig(base_url="http://localhost:7749", token="tok", max_retries=2)
    client = AsyncSketchLogClient(cfg)
    client._client = httpx.AsyncClient(
        base_url="http://localhost:7749",
        transport=httpx.MockTransport(handler),
        headers={"Authorization": "Bearer tok"},
    )
    with patch("asyncio.sleep", new_callable=AsyncMock):
        with pytest.raises(SketchLogServerError):
            await client.health()


# ---------------------------------------------------------------------------
# Subscribe stream
# ---------------------------------------------------------------------------

async def test_subscribe_stream_yields_events() -> None:
    import json as _json
    call_count = 0
    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(200,
            content=_json.dumps({"events": [{"id": call_count}]}).encode(),
            headers={"content-type": "application/json"})
    cfg = AsyncClientConfig(base_url="http://localhost:7749", token="tok")
    client = AsyncSketchLogClient(cfg)
    client._client = httpx.AsyncClient(
        base_url="http://localhost:7749",
        transport=httpx.MockTransport(handler),
        headers={"Authorization": "Bearer tok"},
    )
    events = []
    with patch("asyncio.sleep", new_callable=AsyncMock):
        async for event in client.subscribe_stream("s", max_events=3, poll_interval=0.0):
            events.append(event)
    assert len(events) == 3


async def test_subscribe_stream_exits_on_error_budget() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, content=b"{}", headers={"content-type": "application/json"})
    cfg = AsyncClientConfig(base_url="http://localhost:7749", token="tok", max_retries=0)
    client = AsyncSketchLogClient(cfg)
    client._client = httpx.AsyncClient(
        base_url="http://localhost:7749",
        transport=httpx.MockTransport(handler),
        headers={"Authorization": "Bearer tok"},
    )
    with patch("asyncio.sleep", new_callable=AsyncMock):
        with pytest.raises(Exception):
            async for _ in client.subscribe_stream("s", max_consecutive_errors=2, poll_interval=0.0):
                pass
