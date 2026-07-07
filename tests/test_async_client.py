"""Deterministic async client tests — zero network, zero sleep."""
from __future__ import annotations

import asyncio
import json
from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from sketchlog.async_client import (
    AsyncClientConfig,
    AsyncSketchLogClient,
    SketchLogAuthError,
    SketchLogError,
    SketchLogRateLimitError,
    SketchLogServerError,
    SketchLogTimeoutError,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resp(status: int = 200, body: Dict[str, Any] | None = None) -> httpx.Response:
    payload = json.dumps(body or {"ok": True}).encode()
    return httpx.Response(status, content=payload, headers={"content-type": "application/json"})


def _cfg(**kw: Any) -> AsyncClientConfig:
    defaults: Dict[str, Any] = {"base_url": "http://localhost:7700", "token": "test-token"}
    defaults.update(kw)
    return AsyncClientConfig(**defaults)


# ---------------------------------------------------------------------------
# Config validation
# ---------------------------------------------------------------------------

class TestAsyncClientConfig:
    def test_valid(self) -> None:
        cfg = _cfg()
        assert cfg.base_url == "http://localhost:7700"
        assert cfg.token == "test-token"

    def test_empty_base_url(self) -> None:
        with pytest.raises(ValueError, match="base_url"):
            _cfg(base_url="")

    def test_empty_token(self) -> None:
        with pytest.raises(ValueError, match="token"):
            _cfg(token="")

    def test_negative_timeout(self) -> None:
        with pytest.raises(ValueError, match="timeout"):
            _cfg(timeout=-1.0)

    def test_negative_max_retries(self) -> None:
        with pytest.raises(ValueError, match="max_retries"):
            _cfg(max_retries=-1)

    def test_defaults(self) -> None:
        cfg = _cfg()
        assert cfg.timeout == 30.0
        assert cfg.max_retries == 3
        assert cfg.max_connections == 100
        assert cfg.verify_ssl is True


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------

class TestLifecycle:
    @pytest.mark.asyncio
    async def test_context_manager(self) -> None:
        cfg = _cfg()
        async with AsyncSketchLogClient(cfg) as client:
            assert client._client is not None
        assert client._client is None

    @pytest.mark.asyncio
    async def test_no_client_raises(self) -> None:
        cfg = _cfg()
        client = AsyncSketchLogClient(cfg)
        with pytest.raises(RuntimeError, match="not started"):
            await client._request("GET", "/health")


# ---------------------------------------------------------------------------
# Request handling
# ---------------------------------------------------------------------------

class TestRequestHandling:
    @pytest.mark.asyncio
    async def test_success(self) -> None:
        cfg = _cfg()
        async with AsyncSketchLogClient(cfg) as client:
            client._client = MagicMock()
            client._client.request = AsyncMock(return_value=_resp(200, {"result": "ok"}))
            result = await client._request("GET", "/health")
            assert result == {"result": "ok"}

    @pytest.mark.asyncio
    async def test_auth_error_401(self) -> None:
        cfg = _cfg()
        async with AsyncSketchLogClient(cfg) as client:
            client._client = MagicMock()
            client._client.request = AsyncMock(return_value=_resp(401))
            with pytest.raises(SketchLogAuthError):
                await client._request("GET", "/health")

    @pytest.mark.asyncio
    async def test_auth_error_403(self) -> None:
        cfg = _cfg()
        async with AsyncSketchLogClient(cfg) as client:
            client._client = MagicMock()
            client._client.request = AsyncMock(return_value=_resp(403))
            with pytest.raises(SketchLogAuthError):
                await client._request("GET", "/health")

    @pytest.mark.asyncio
    async def test_rate_limit_429(self) -> None:
        cfg = _cfg()
        async with AsyncSketchLogClient(cfg) as client:
            client._client = MagicMock()
            resp = httpx.Response(429, content=b'{"error":"rate limited"}',
                                  headers={"content-type": "application/json", "Retry-After": "5"})
            client._client.request = AsyncMock(return_value=resp)
            with pytest.raises(SketchLogRateLimitError) as exc_info:
                await client._request("GET", "/health")
            assert exc_info.value.retry_after == 5.0

    @pytest.mark.asyncio
    async def test_server_error_no_retry(self) -> None:
        cfg = _cfg(max_retries=0)
        async with AsyncSketchLogClient(cfg) as client:
            client._client = MagicMock()
            client._client.request = AsyncMock(return_value=_resp(500))
            with pytest.raises(SketchLogServerError):
                await client._request("GET", "/health")

    @pytest.mark.asyncio
    async def test_timeout_raises(self) -> None:
        cfg = _cfg(max_retries=0)
        async with AsyncSketchLogClient(cfg) as client:
            client._client = MagicMock()
            client._client.request = AsyncMock(
                side_effect=httpx.ReadTimeout("timed out", request=MagicMock())
            )
            with pytest.raises(Exception):
                await client._request("GET", "/health")


# ---------------------------------------------------------------------------
# Backoff
# ---------------------------------------------------------------------------

class TestBackoff:
    def test_backoff_range(self) -> None:
        cfg = _cfg(backoff_base=1.0, backoff_cap=10.0)
        client = AsyncSketchLogClient(cfg)
        for attempt in range(5):
            b = client._backoff(attempt)
            assert 0.0 <= b <= 10.0

    def test_backoff_cap(self) -> None:
        cfg = _cfg(backoff_base=1.0, backoff_cap=5.0)
        client = AsyncSketchLogClient(cfg)
        for _ in range(20):
            assert client._backoff(100) <= 5.0


# ---------------------------------------------------------------------------
# API methods
# ---------------------------------------------------------------------------

class TestAPIMethods:
    @pytest.mark.asyncio
    async def test_ingest(self) -> None:
        cfg = _cfg()
        async with AsyncSketchLogClient(cfg) as client:
            client._client = MagicMock()
            client._client.request = AsyncMock(return_value=_resp(200, {"ingested": 1}))
            result = await client.ingest("ns", "stream", [{"value": 1.0}])
            assert result["ingested"] == 1

    @pytest.mark.asyncio
    async def test_query_summary(self) -> None:
        cfg = _cfg()
        async with AsyncSketchLogClient(cfg) as client:
            client._client = MagicMock()
            client._client.request = AsyncMock(return_value=_resp(200, {"p99": 42.0}))
            result = await client.query_summary("ns", "stream")
            assert result["p99"] == 42.0

    @pytest.mark.asyncio
    async def test_health(self) -> None:
        cfg = _cfg()
        async with AsyncSketchLogClient(cfg) as client:
            client._client = MagicMock()
            client._client.request = AsyncMock(return_value=_resp(200, {"status": "ok"}))
            result = await client.health()
            assert result["status"] == "ok"

    @pytest.mark.asyncio
    async def test_info(self) -> None:
        cfg = _cfg()
        async with AsyncSketchLogClient(cfg) as client:
            client._client = MagicMock()
            client._client.request = AsyncMock(return_value=_resp(200, {"version": "1.0"}))
            result = await client.info()
            assert result["version"] == "1.0"

    @pytest.mark.asyncio
    async def test_create_namespace(self) -> None:
        cfg = _cfg()
        async with AsyncSketchLogClient(cfg) as client:
            client._client = MagicMock()
            client._client.request = AsyncMock(return_value=_resp(200, {"name": "ns"}))
            result = await client.create_namespace("ns")
            assert result["name"] == "ns"

    @pytest.mark.asyncio
    async def test_delete_namespace(self) -> None:
        cfg = _cfg()
        async with AsyncSketchLogClient(cfg) as client:
            client._client = MagicMock()
            client._client.request = AsyncMock(return_value=_resp(200, {"deleted": True}))
            result = await client.delete_namespace("ns")
            assert result["deleted"] is True

    @pytest.mark.asyncio
    async def test_create_stream(self) -> None:
        cfg = _cfg()
        async with AsyncSketchLogClient(cfg) as client:
            client._client = MagicMock()
            client._client.request = AsyncMock(return_value=_resp(200, {"name": "s"}))
            result = await client.create_stream("ns", "s")
            assert result["name"] == "s"

    @pytest.mark.asyncio
    async def test_delete_stream(self) -> None:
        cfg = _cfg()
        async with AsyncSketchLogClient(cfg) as client:
            client._client = MagicMock()
            client._client.request = AsyncMock(return_value=_resp(200, {"deleted": True}))
            result = await client.delete_stream("ns", "s")
            assert result["deleted"] is True


# ---------------------------------------------------------------------------
# Subscribe stream
# ---------------------------------------------------------------------------

class TestSubscribeStream:
    @pytest.mark.asyncio
    async def test_subscribe_yields_summaries(self) -> None:
        cfg = _cfg()
        async with AsyncSketchLogClient(cfg) as client:
            client._client = MagicMock()
            client._client.request = AsyncMock(return_value=_resp(200, {"p99": 1.0}))
            results = []
            async with client.subscribe_stream("ns", "s", max_events=3, interval_seconds=0.0) as events:
                async for summary in events:
                    results.append(summary)
            assert len(results) == 3
            assert all("_ts" in r for r in results)

    @pytest.mark.asyncio
    async def test_subscribe_error_budget(self) -> None:
        cfg = _cfg(max_retries=0)
        async with AsyncSketchLogClient(cfg) as client:
            client._client = MagicMock()
            client._client.request = AsyncMock(return_value=_resp(500))
            with pytest.raises(SketchLogError):
                async with client.subscribe_stream("ns", "s", interval_seconds=0.0) as events:
                    async for _ in events:
                        pass
