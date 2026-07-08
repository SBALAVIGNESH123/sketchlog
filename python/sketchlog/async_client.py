"""Async Python client for SketchLog."""
from __future__ import annotations

import asyncio
import random
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Dict, List, Optional

import httpx


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class SketchLogError(Exception):
    """Base error for all SketchLog client errors."""


class SketchLogAuthError(SketchLogError):
    """Raised on HTTP 401 / 403."""


class SketchLogRateLimitError(SketchLogError):
    """Raised on HTTP 429."""
    def __init__(self, message: str, retry_after: Optional[int] = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class SketchLogServerError(SketchLogError):
    """Raised on HTTP 5xx."""
    def __init__(self, message: str, status_code: int = 500) -> None:
        super().__init__(message)
        self.status_code = status_code


class SketchLogClientError(SketchLogError):
    """Raised on HTTP 4xx (non-auth, non-rate-limit)."""
    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


class SketchLogTimeoutError(SketchLogError):
    """Raised when a request times out."""


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AsyncClientConfig:
    """Configuration for AsyncSketchLogClient."""
    base_url: str
    token: str
    timeout: float = 30.0
    max_retries: int = 3
    backoff_base: float = 0.5
    backoff_cap: float = 30.0
    max_connections: int = 100

    def __post_init__(self) -> None:
        if not self.base_url:
            raise ValueError("base_url must not be empty")
        if not self.token:
            raise ValueError("token must not be empty")
        if self.timeout <= 0:
            raise ValueError("timeout must be positive")
        if self.max_retries < 0:
            raise ValueError("max_retries must be >= 0")
        if self.backoff_base <= 0:
            raise ValueError("backoff_base must be positive")
        if self.backoff_cap <= 0:
            raise ValueError("backoff_cap must be positive")
        # Strip trailing slash
        object.__setattr__(self, "base_url", self.base_url.rstrip("/"))


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

class AsyncSketchLogClient:
    """Production-grade async client for SketchLog using httpx."""

    def __init__(self, config: AsyncClientConfig) -> None:
        self._config = config
        self._client: Optional[httpx.AsyncClient] = None

    # -----------------------------------------------------------------------
    # Lifecycle
    # -----------------------------------------------------------------------

    async def open(self) -> None:
        """Open the underlying HTTP client."""
        if self._client is None:
            limits = httpx.Limits(
                max_connections=self._config.max_connections,
                max_keepalive_connections=self._config.max_connections,
            )
            self._client = httpx.AsyncClient(
                base_url=self._config.base_url,
                headers={"Authorization": f"Bearer {self._config.token}"},
                timeout=self._config.timeout,
                limits=limits,
            )

    async def close(self) -> None:
        """Close the underlying HTTP client (idempotent)."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> "AsyncSketchLogClient":
        await self.open()
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.close()

    # -----------------------------------------------------------------------
    # Internal helpers
    # -----------------------------------------------------------------------

    def _url(self, path: str) -> str:
        """Build a full URL, collapsing double slashes."""
        base = self._config.base_url.rstrip("/")
        path = path.lstrip("/")
        return f"{base}/{path}"

    def _backoff(self, attempt: int) -> float:
        """Exponential backoff with full jitter."""
        cap = self._config.backoff_cap
        base = self._config.backoff_base
        ceiling = min(cap, base * (2 ** attempt))
        return random.uniform(0, ceiling)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        params: Optional[Dict[str, Any]] = None,
        timeout: Optional[float] = None,
    ) -> Any:
        if self._client is None:
            raise SketchLogError("Client is not open. Use 'async with' or call open() first.")

        retries = self._config.max_retries
        last_exc: Exception = SketchLogError("No attempts made")

        for attempt in range(retries + 1):
            try:
                resp = await self._client.request(
                    method,
                    path,
                    json=json,
                    params=params,
                    timeout=timeout or self._config.timeout,
                )

                # Auth errors — no retry
                if resp.status_code in (401, 403):
                    raise SketchLogAuthError(
                        f"Authentication failed: HTTP {resp.status_code}"
                    )

                # Rate limit — no retry
                if resp.status_code == 429:
                    retry_after = int(resp.headers.get("Retry-After", 0)) or None
                    raise SketchLogRateLimitError(
                        f"Rate limited: HTTP 429",
                        retry_after=retry_after,
                    )

                # Server errors — retry
                if resp.status_code >= 500:
                    last_exc = SketchLogServerError(
                        f"Server error: HTTP {resp.status_code}",
                        status_code=resp.status_code,
                    )
                    if attempt < retries:
                        await asyncio.sleep(self._backoff(attempt))
                        continue
                    raise last_exc

                # Other 4xx — no retry
                if resp.status_code >= 400:
                    raise SketchLogClientError(
                        f"Client error: HTTP {resp.status_code}",
                        status_code=resp.status_code,
                    )

                return resp.json()

            except (SketchLogAuthError, SketchLogRateLimitError, SketchLogClientError):
                raise
            except httpx.TimeoutException as exc:
                last_exc = SketchLogTimeoutError(str(exc))
                if attempt < retries:
                    await asyncio.sleep(self._backoff(attempt))
            except SketchLogError:
                raise
            except Exception as exc:
                last_exc = exc
                if attempt < retries:
                    await asyncio.sleep(self._backoff(attempt))

        raise last_exc

    # -----------------------------------------------------------------------
    # Health / Info
    # -----------------------------------------------------------------------

    async def health(self, *, timeout: Optional[float] = None) -> Dict[str, Any]:
        """GET /health"""
        result = await self._request("GET", "/health", timeout=timeout)
        return dict(result) if isinstance(result, dict) else {"data": result}

    async def info(self, *, timeout: Optional[float] = None) -> Dict[str, Any]:
        """GET /info"""
        result = await self._request("GET", "/info", timeout=timeout)
        return dict(result) if isinstance(result, dict) else {"data": result}

    # -----------------------------------------------------------------------
    # Ingest
    # -----------------------------------------------------------------------

    async def ingest(
        self,
        namespace: str,
        stream: str,
        events: List[Dict[str, Any]],
        *,
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        """POST /namespaces/{namespace}/streams/{stream}/ingest"""
        if not events:
            raise ValueError("events must not be empty")
        result = await self._request(
            "POST",
            f"/namespaces/{namespace}/streams/{stream}/ingest",
            json={"events": events},
            timeout=timeout,
        )
        return dict(result) if isinstance(result, dict) else {"data": result}

    # -----------------------------------------------------------------------
    # Query
    # -----------------------------------------------------------------------

    async def query(
        self,
        namespace: str,
        stream: str,
        *,
        limit: Optional[int] = None,
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        """GET /namespaces/{namespace}/streams/{stream}/query"""
        params: Dict[str, Any] = {}
        if limit is not None:
            params["limit"] = limit
        result = await self._request(
            "GET",
            f"/namespaces/{namespace}/streams/{stream}/query",
            params=params or None,
            timeout=timeout,
        )
        return dict(result) if isinstance(result, dict) else {"data": result}

    async def query_cdf(
        self,
        namespace: str,
        stream: str,
        *,
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        """GET /namespaces/{namespace}/streams/{stream}/query/cdf"""
        result = await self._request(
            "GET",
            f"/namespaces/{namespace}/streams/{stream}/query/cdf",
            timeout=timeout,
        )
        return dict(result) if isinstance(result, dict) else {"data": result}

    # -----------------------------------------------------------------------
    # Namespaces
    # -----------------------------------------------------------------------

    async def list_namespaces(self, *, timeout: Optional[float] = None) -> Dict[str, Any]:
        """GET /namespaces"""
        result = await self._request("GET", "/namespaces", timeout=timeout)
        return dict(result) if isinstance(result, dict) else {"data": result}

    async def create_namespace(
        self,
        namespace: str,
        *,
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        """POST /namespaces"""
        result = await self._request(
            "POST",
            "/namespaces",
            json={"name": namespace},
            timeout=timeout,
        )
        return dict(result) if isinstance(result, dict) else {"data": result}

    async def delete_namespace(
        self,
        namespace: str,
        *,
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        """DELETE /namespaces/{namespace}"""
        result = await self._request(
            "DELETE",
            f"/namespaces/{namespace}",
            timeout=timeout,
        )
        return dict(result) if isinstance(result, dict) else {"data": result}

    # -----------------------------------------------------------------------
    # Streams
    # -----------------------------------------------------------------------

    async def list_streams(
        self,
        namespace: str,
        *,
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        """GET /namespaces/{namespace}/streams"""
        result = await self._request(
            "GET",
            f"/namespaces/{namespace}/streams",
            timeout=timeout,
        )
        return dict(result) if isinstance(result, dict) else {"data": result}

    async def create_stream(
        self,
        namespace: str,
        stream: str,
        *,
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        """POST /namespaces/{namespace}/streams"""
        result = await self._request(
            "POST",
            f"/namespaces/{namespace}/streams",
            json={"name": stream},
            timeout=timeout,
        )
        return dict(result) if isinstance(result, dict) else {"data": result}

    async def delete_stream(
        self,
        namespace: str,
        stream: str,
        *,
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        """DELETE /namespaces/{namespace}/streams/{stream}"""
        result = await self._request(
            "DELETE",
            f"/namespaces/{namespace}/streams/{stream}",
            timeout=timeout,
        )
        return dict(result) if isinstance(result, dict) else {"data": result}

    # -----------------------------------------------------------------------
    # Streaming subscription
    # -----------------------------------------------------------------------

    @asynccontextmanager
    async def subscribe_stream(
        self,
        namespace: str,
        stream: str,
        *,
        poll_interval: float = 1.0,
        max_events: Optional[int] = None,
        max_consecutive_errors: int = 10,
        timeout: Optional[float] = None,
    ) -> AsyncIterator[Dict[str, Any]]:
        """Async context manager that yields events from a stream."""
        count = 0
        consecutive_errors = 0

        async def _generator() -> AsyncIterator[Dict[str, Any]]:
            nonlocal count, consecutive_errors
            while True:
                if max_events is not None and count >= max_events:
                    return
                try:
                    result = await self.query(
                        namespace, stream, timeout=timeout
                    )
                    consecutive_errors = 0
                    events = result.get("events", [])
                    for event in events:
                        yield event
                        count += 1
                        if max_events is not None and count >= max_events:
                            return
                except asyncio.CancelledError:
                    raise
                except Exception:
                    consecutive_errors += 1
                    if consecutive_errors >= max_consecutive_errors:
                        return
                await asyncio.sleep(poll_interval)

        yield _generator()
