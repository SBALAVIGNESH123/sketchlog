"""Async Python client for SketchLog — production-grade, httpx-based."""
from __future__ import annotations

import asyncio
import logging
import random
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

__all__ = [
    "AsyncClientConfig",
    "AsyncSketchLogClient",
    "SketchLogError",
    "SketchLogAuthError",
    "SketchLogRateLimitError",
    "SketchLogServerError",
    "SketchLogTimeoutError",
]


class SketchLogError(Exception):
    """Base exception for all SketchLog client errors."""


class SketchLogAuthError(SketchLogError):
    """Authentication / authorisation failure (HTTP 401 / 403)."""


class SketchLogRateLimitError(SketchLogError):
    """Rate-limit exceeded (HTTP 429)."""
    def __init__(self, message: str, retry_after: Optional[float] = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class SketchLogServerError(SketchLogError):
    """Unrecoverable server error (HTTP 5xx)."""


class SketchLogTimeoutError(SketchLogError):
    """Request deadline exceeded."""


@dataclass(frozen=True)
class AsyncClientConfig:
    """Immutable configuration for AsyncSketchLogClient."""
    base_url: str
    token: str
    timeout: float = 30.0
    max_retries: int = 3
    backoff_base: float = 0.5
    backoff_cap: float = 30.0
    max_connections: int = 100
    max_keepalive_connections: int = 20
    verify_ssl: bool = True
    extra_headers: Dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.base_url:
            raise ValueError("base_url must not be empty")
        if not self.token:
            raise ValueError("token must not be empty")
        if self.timeout <= 0:
            raise ValueError("timeout must be positive")
        if self.max_retries < 0:
            raise ValueError("max_retries must be >= 0")


class AsyncSketchLogClient:
    """Production-grade async client for SketchLog.

    Usage::

        async with AsyncSketchLogClient(cfg) as client:
            await client.ingest("prod", "latency_ms", [{"value": 42.0}])
    """

    def __init__(self, config: AsyncClientConfig) -> None:
        self._cfg = config
        self._client: Optional[httpx.AsyncClient] = None

    async def __aenter__(self) -> "AsyncSketchLogClient":
        limits = httpx.Limits(
            max_connections=self._cfg.max_connections,
            max_keepalive_connections=self._cfg.max_keepalive_connections,
        )
        self._client = httpx.AsyncClient(
            base_url=self._cfg.base_url,
            limits=limits,
            verify=self._cfg.verify_ssl,
            timeout=self._cfg.timeout,
            headers={
                "Authorization": f"Bearer {self._cfg.token}",
                "Content-Type": "application/json",
                **self._cfg.extra_headers,
            },
        )
        return self

    async def __aexit__(self, *_: Any) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _backoff(self, attempt: int) -> float:
        """Exponential backoff with full jitter."""
        cap: float = self._cfg.backoff_cap
        base: float = self._cfg.backoff_base
        raw: float = min(cap, base * (2 ** attempt))
        return float(random.uniform(0, raw))

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: Optional[Any] = None,
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        if self._client is None:
            raise RuntimeError("Client not started — use async with")
        t: float = timeout if timeout is not None else self._cfg.timeout
        retries = self._cfg.max_retries
        last_exc: Exception = RuntimeError("no attempts made")
        for attempt in range(retries + 1):
            try:
                resp = await self._client.request(
                    method, path, json=json, timeout=t
                )
                if resp.status_code in (401, 403):
                    raise SketchLogAuthError(
                        f"Auth error {resp.status_code}: {resp.text}"
                    )
                if resp.status_code == 429:
                    retry_after: Optional[float] = None
                    ra = resp.headers.get("Retry-After")
                    if ra is not None:
                        try:
                            retry_after = float(ra)
                        except ValueError:
                            pass
                    raise SketchLogRateLimitError(
                        f"Rate limited: {resp.text}", retry_after=retry_after
                    )
                if resp.status_code >= 500:
                    if attempt < retries:
                        await asyncio.sleep(self._backoff(attempt))
                        continue
                    raise SketchLogServerError(
                        f"Server error {resp.status_code}: {resp.text}"
                    )
                resp.raise_for_status()
                body = resp.json()
                return dict(body) if isinstance(body, dict) else {"data": body}
            except (SketchLogAuthError, SketchLogRateLimitError):
                raise
            except httpx.TimeoutException as exc:
                last_exc = SketchLogTimeoutError(str(exc))
                if attempt < retries:
                    await asyncio.sleep(self._backoff(attempt))
            except SketchLogServerError:
                raise
            except Exception as exc:
                last_exc = exc
                if attempt < retries:
                    await asyncio.sleep(self._backoff(attempt))
        raise last_exc

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def ingest(
        self,
        namespace: str,
        stream: str,
        events: List[Dict[str, Any]],
        *,
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Ingest events into a stream."""
        return await self._request(
            "POST",
            f"/v1/namespaces/{namespace}/streams/{stream}/events",
            json={"events": events},
            timeout=timeout,
        )

    async def query_summary(
        self,
        namespace: str,
        stream: str,
        *,
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Query the summary statistics for a stream."""
        return await self._request(
            "GET",
            f"/v1/namespaces/{namespace}/streams/{stream}/summary",
            timeout=timeout,
        )

    async def create_namespace(
        self,
        namespace: str,
        *,
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Create a namespace."""
        return await self._request(
            "POST",
            "/v1/namespaces",
            json={"name": namespace},
            timeout=timeout,
        )

    async def delete_namespace(
        self,
        namespace: str,
        *,
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Delete a namespace."""
        return await self._request(
            "DELETE",
            f"/v1/namespaces/{namespace}",
            timeout=timeout,
        )

    async def create_stream(
        self,
        namespace: str,
        stream: str,
        *,
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Create a stream inside a namespace."""
        return await self._request(
            "POST",
            f"/v1/namespaces/{namespace}/streams",
            json={"name": stream},
            timeout=timeout,
        )

    async def delete_stream(
        self,
        namespace: str,
        stream: str,
        *,
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Delete a stream."""
        return await self._request(
            "DELETE",
            f"/v1/namespaces/{namespace}/streams/{stream}",
            timeout=timeout,
        )

    async def health(self, *, timeout: Optional[float] = None) -> Dict[str, Any]:
        """Health check."""
        return await self._request("GET", "/health", timeout=timeout)

    async def info(self, *, timeout: Optional[float] = None) -> Dict[str, Any]:
        """Server info."""
        return await self._request("GET", "/v1/info", timeout=timeout)

    @asynccontextmanager
    async def subscribe_stream(
        self,
        namespace: str,
        stream: str,
        *,
        interval_seconds: float = 1.0,
        max_events: Optional[int] = None,
        timeout: Optional[float] = None,
    ) -> AsyncIterator[AsyncIterator[Dict[str, Any]]]:
        """Async streaming subscription via polling.

        Usage::

            cfg = AsyncClientConfig(base_url="http://localhost:7700", token="tok")
            async with AsyncSketchLogClient(cfg) as client:
                async with client.subscribe_stream("prod", "latency_ms") as events:
                    async for summary in events:
                        print(summary)
        """
        async def _generator() -> AsyncIterator[Dict[str, Any]]:
            count = 0
            consecutive_errors = 0
            max_consecutive_errors = 10
            while max_events is None or count < max_events:
                try:
                    summary = await self.query_summary(
                        namespace, stream, timeout=timeout
                    )
                    summary["_ts"] = time.time()
                    yield summary
                    count += 1
                    consecutive_errors = 0
                    await asyncio.sleep(interval_seconds)
                except asyncio.CancelledError:
                    return
                except SketchLogError as exc:
                    consecutive_errors += 1
                    logger.warning(
                        "subscribe_stream error %d/%d for %s/%s: %s",
                        consecutive_errors,
                        max_consecutive_errors,
                        namespace,
                        stream,
                        exc,
                    )
                    if consecutive_errors >= max_consecutive_errors:
                        raise
                    await asyncio.sleep(interval_seconds)

        yield _generator()
