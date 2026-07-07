"""
Tests for AsyncSketchLogClient — httpx-based, zero network, zero sleep.
All mocks use httpx.Response — deterministic on every platform.
"""
from __future__ import annotations

import asyncio
import pytest
import httpx
from unittest.mock import AsyncMock, MagicMock, patch
from sketchlog.async_client import (
    AsyncClientConfig,
    AsyncSketchLogClient,
    SketchLogAuthError,
    SketchLogRateLimitError,
    SketchLogServerError,
    SketchLogTimeoutError,
    SketchLogError,
)


# ---------------------------------------------------------------------------
# Config validation
# ---------------------------------------------------------------------------

def test_config_defaults():
    cfg = AsyncClientConfig(base_url="http://localhost:7749", token="tok")
    assert cfg.timeout == 30.0
    assert cfg.max_retries == 3
    assert cfg.max_connections == 100


def test_config_invalid_url():
    with pytest.raises(ValueError, match="base_url"):
        AsyncClientConfig(base_url="", token="tok")


def test_config_invalid_token():
    with pytest.raises(ValueError, match="token"):
        AsyncClientConfig(base_url="http://localhost:7749", token="")


def test_config_invalid_timeout():
    with pytest.raises(ValueError, match="timeout"):
        AsyncClientConfig(base_url="http://localhost:7749", token="tok", timeout=0)


def test_config_invalid_retries():
    with pytest.raises(ValueError, match="max_retries"):
        AsyncClientConfig(base_url="http://localhost:7749", token="tok", max_retries=-1)


def test_config_trailing_slash_stripped():
    cfg = AsyncClientConfig(base_url="http://localhost:7749/", token="tok")
    assert not cfg.base_url.endswith("/")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_client() -> AsyncSketchLogClient:
    cfg = AsyncClientConfig(base_url="http://localhost:7749", token="test-token")
    return AsyncSketchLogClient(cfg)


def mock_response(status: int, body: dict) -> httpx.Response:
    return httpx.Response(status, json=body)


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_context_manager_opens_and_closes():
    client = make_client()
    async with client:
        assert client._client is not None
    assert client._client is None


@pytest.mark.asyncio
async def test_double_close_is_safe():
    client = make_client()
    await client.open()
    await client.close()
    await client.close()  # should not raise


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_health_ok():
    client = make_client()
    async with client:
        with patch.object(client._client, "request", new_callable=AsyncMock) as m:
            m.return_value = mock_response(200, {"status": "ok"})
            result = await client.health()
    assert result["status"] == "ok"


@pytest.mark.asyncio
async def test_health_auth_error():
    client = make_client()
    async with client:
        with patch.object(client._client, "request", new_callable=AsyncMock) as m:
            m.return_value = mock_response(401, {"error": "unauthorized"})
            with pytest.raises(SketchLogAuthError):
                await client.health()


@pytest.mark.asyncio
async def test_health_rate_limit():
    client = make_client()
    async with client:
        with patch.object(client._client, "request", new_callable=AsyncMock) as m:
            m.return_value = mock_response(429, {"error": "rate limited"})
            with pytest.raises(SketchLogRateLimitError):
                await client.health()


@pytest.mark.asyncio
async def test_health_server_error():
    client = make_client()
    async with client:
        with patch.object(client._client, "request", new_callable=AsyncMock) as m:
            m.return_value = mock_response(500, {"error": "server error"})
            with pytest.raises(SketchLogServerError):
                await client.health()


# ---------------------------------------------------------------------------
# Ingest
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ingest_ok():
    client = make_client()
    async with client:
        with patch.object(client._client, "request", new_callable=AsyncMock) as m:
            m.return_value = mock_response(200, {"inserted": 1})
            result = await client.ingest("ns", "stream", [{"v": 1.0}])
    assert result["inserted"] == 1


@pytest.mark.asyncio
async def test_ingest_empty_raises():
    client = make_client()
    async with client:
        with pytest.raises(ValueError, match="values"):
            await client.ingest("ns", "stream", [])


# ---------------------------------------------------------------------------
# Query
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_query_summary_ok():
    client = make_client()
    async with client:
        with patch.object(client._client, "request", new_callable=AsyncMock) as m:
            m.return_value = mock_response(200, {"p50": 1.0, "p99": 9.9, "count": 100})
            result = await client.query_summary("ns", "stream")
    assert result["p99"] == 9.9


@pytest.mark.asyncio
async def test_query_cdf_ok():
    client = make_client()
    async with client:
        with patch.object(client._client, "request", new_callable=AsyncMock) as m:
            m.return_value = mock_response(200, {"cdf": [[1.0, 0.5]]})
            result = await client.query_cdf("ns", "stream")
    assert "cdf" in result


# ---------------------------------------------------------------------------
# Namespace / Stream management
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_namespace_ok():
    client = make_client()
    async with client:
        with patch.object(client._client, "request", new_callable=AsyncMock) as m:
            m.return_value = mock_response(200, {"created": True})
            result = await client.create_namespace("ns")
    assert result["created"] is True


@pytest.mark.asyncio
async def test_list_namespaces_ok():
    client = make_client()
    async with client:
        with patch.object(client._client, "request", new_callable=AsyncMock) as m:
            m.return_value = mock_response(200, {"namespaces": ["ns1", "ns2"]})
            result = await client.list_namespaces()
    assert "namespaces" in result


@pytest.mark.asyncio
async def test_create_stream_ok():
    client = make_client()
    async with client:
        with patch.object(client._client, "request", new_callable=AsyncMock) as m:
            m.return_value = mock_response(200, {"created": True})
            result = await client.create_stream("ns", "stream")
    assert result["created"] is True


@pytest.mark.asyncio
async def test_list_streams_ok():
    client = make_client()
    async with client:
        with patch.object(client._client, "request", new_callable=AsyncMock) as m:
            m.return_value = mock_response(200, {"streams": ["s1", "s2"]})
            result = await client.list_streams("ns")
    assert "streams" in result


# ---------------------------------------------------------------------------
# Retry logic
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_retries_on_503():
    client = make_client()
    call_count = 0

    async def side_effect(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            return mock_response(503, {"error": "unavailable"})
        return mock_response(200, {"status": "ok"})

    async with client:
        with patch.object(client._client, "request", side_effect=side_effect):
            with patch("asyncio.sleep", new_callable=AsyncMock):
                result = await client.health()
    assert call_count == 3
    assert result["status"] == "ok"


@pytest.mark.asyncio
async def test_exhausted_retries_raises():
    client = make_client()
    async with client:
        with patch.object(client._client, "request", new_callable=AsyncMock) as m:
            m.return_value = mock_response(503, {"error": "unavailable"})
            with patch("asyncio.sleep", new_callable=AsyncMock):
                with pytest.raises(SketchLogServerError):
                    await client.health()


# ---------------------------------------------------------------------------
# subscribe_stream error budget
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_subscribe_stream_exits_on_error_budget():
    client = make_client()
    async with client:
        with patch.object(client, "query_summary", new_callable=AsyncMock) as m:
            m.side_effect = SketchLogServerError("boom")
            with patch("asyncio.sleep", new_callable=AsyncMock):
                events = []
                async with client.subscribe_stream("ns", "stream", interval_seconds=0.0, max_consecutive_errors=3) as gen:
                    async for e in gen:
                        events.append(e)
    assert len(events) == 0


# ---------------------------------------------------------------------------
# URL building
# ---------------------------------------------------------------------------

def test_url_builder():
    client = make_client()
    url = client._url("/namespaces/ns/streams/s")
    assert url == "http://localhost:7749/namespaces/ns/streams/s"


def test_url_builder_strips_double_slash():
    client = make_client()
    url = client._url("//namespaces")
    assert "//" not in url
