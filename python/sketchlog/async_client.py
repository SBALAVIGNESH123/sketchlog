"""Async Python client for SketchLog."""
from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass
from typing import Any, AsyncGenerator, Dict, List, Optional, Type, cast

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
    """Raised on HTTP 4xx (except 401/403/429)."""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


class SketchLogTimeoutError(SketchLogError):
    """Raised when a request times out."""


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass
class AsyncClientConfig:
    """Configuration for AsyncSketchLogClient."""

    base_url: str
    token: str
    timeout: float = 30.0
    max_retries: int = 3
    max_connections: int = 10

    def __post_init__(self) -> None:
        self.base_url = self.base_url.rstrip("/")
        if not self.base_url:
            raise ValueError("base_url is required")
        if not self.token:
            raise ValueError("token is required")
        if self.timeout <= 0:
            raise ValueError("timeout must be positive")
        if self.max_retries < 0:
            raise ValueError("max_retries must be >= 0")


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class AsyncSketchLogClient:
    """Production-grade async Python client for SketchLog."""

    def __init__(self, config: AsyncClientConfig) -> None:
        self._config = config
        self._client: Optional[httpx.AsyncClient] = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def open(self) -> None:
        """Open the HTTP connection pool."""
        self._client = httpx.AsyncClient(
            base_url=self._config.base_url,
            headers={"Authorization": f"Bearer {self._config.token}"},
            timeout=self._config.timeout,
            limits=httpx.Limits(max_connections=self._config.max_connections),
        )

    async def close(self) -> None:
        """Close the HTTP connection pool (idempotent)."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> "AsyncSketchLogClient":
        await self.open()
        return self

    async def __aexit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc_val: Optional[BaseException],
        exc_tb: Optional[object],
    ) -> None:
        await self.close()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _url(self, path: str) -> str:
        """Strip leading slash so path is relative to base_url."""
        return path.lstrip("/")

    def _backoff(self, attempt: int) -> float:
        """Exponential backoff with jitter, capped at 30 s."""
        return float(min(0.1 * (2 ** attempt) + random.uniform(0.0, 0.1), 30.0))

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Send an HTTP request with retry / backoff on transient errors."""
        if self._client is None:
            raise RuntimeError(
                "Client is not open. Use `async with` or call `open()` first."
            )

        retries: int = self._config.max_retries
        eff_timeout: float = timeout if timeout is not None else self._config.timeout
        last_exc: Optional[BaseException] = None

        for attempt in range(retries + 1):
            try:
                resp = await self._client.request(
                    method,
                    self._url(path),
                    json=json,
                    params=params,
                    timeout=eff_timeout,
                )

                if resp.status_code in (401, 403):
                    raise SketchLogAuthError(
                        f"Authentication failed (HTTP {resp.status_code})"
                    )
                if resp.status_code == 429:
                    hdr: Optional[str] = resp.headers.get("Retry-After")
                    retry_after: Optional[int] = int(hdr) if hdr is not None else None
                    raise SketchLogRateLimitError(
                        "Rate limit exceeded", retry_after=retry_after
                    )
                if resp.status_code >= 500:
                    raise SketchLogServerError(
                        f"Server error (HTTP {resp.status_code})",
                        status_code=resp.status_code,
                    )
                if resp.status_code >= 400:
                    raise SketchLogClientError(
                        f"Client error (HTTP {resp.status_code})",
                        status_code=resp.status_code,
                    )

                body: Any = resp.json()
                if isinstance(body, dict):
                    return cast(Dict[str, Any], body)
                return {"data": body}

            except (SketchLogAuthError, SketchLogRateLimitError, SketchLogClientError):
                raise
            except SketchLogServerError as exc:
                last_exc = exc
                if attempt < retries:
                    await asyncio.sleep(self._backoff(attempt))
            except httpx.TimeoutException as exc:
                last_exc = SketchLogTimeoutError(str(exc))
                if attempt < retries:
                    await asyncio.sleep(self._backoff(attempt))
            except httpx.RequestError as exc:
                last_exc = exc
                if attempt < retries:
                    await asyncio.sleep(self._backoff(attempt))

        if last_exc is not None:
            raise last_exc
        raise SketchLogError("Request failed after exhausting all retries")

    # ------------------------------------------------------------------
    # API — Ingestion
    # ------------------------------------------------------------------

    async def ingest(
        self,
        stream: str,
        events: List[Dict[str, Any]],
        *,
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Ingest events into stream."""
        if not events:
            raise ValueError("events must not be empty")
        return await self._request(
            "POST",
            f"/streams/{stream}/ingest",
            json={"events": events},
            timeout=timeout,
        )

    # ------------------------------------------------------------------
    # API — Query
    # ------------------------------------------------------------------

    async def query(
        self,
        stream: str,
        *,
        limit: Optional[int] = None,
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Query events from stream."""
        qparams: Optional[Dict[str, Any]] = (
            {"limit": limit} if limit is not None else None
        )
        return await self._request(
            "GET", f"/streams/{stream}/query", params=qparams, timeout=timeout
        )

    async def query_cdf(
        self,
        stream: str,
        *,
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Query the CDF of stream."""
        return await self._request(
            "GET", f"/streams/{stream}/cdf", timeout=timeout
        )

    # ------------------------------------------------------------------
    # API — Namespaces
    # ------------------------------------------------------------------

    async def list_namespaces(
        self, *, timeout: Optional[float] = None
    ) -> Dict[str, Any]:
        """List all namespaces."""
        return await self._request("GET", "/namespaces", timeout=timeout)

    async def create_namespace(
        self, name: str, *, timeout: Optional[float] = None
    ) -> Dict[str, Any]:
        """Create a namespace."""
        return await self._request(
            "POST", "/namespaces", json={"name": name}, timeout=timeout
        )

    async def delete_namespace(
        self, name: str, *, timeout: Optional[float] = None
    ) -> Dict[str, Any]:
        """Delete a namespace."""
        return await self._request(
            "DELETE", f"/namespaces/{name}", timeout=timeout
        )

    # ------------------------------------------------------------------
    # API — Streams
    # ------------------------------------------------------------------

    async def list_streams(
        self, *, timeout: Optional[float] = None
    ) -> Dict[str, Any]:
        """List all streams."""
        return await self._request("GET", "/streams", timeout=timeout)

    async def create_stream(
        self, name: str, *, timeout: Optional[float] = None
    ) -> Dict[str, Any]:
        """Create a stream."""
        return await self._request(
            "POST", "/streams", json={"name": name}, timeout=timeout
        )

    async def delete_stream(
        self, name: str, *, timeout: Optional[float] = None
    ) -> Dict[str, Any]:
        """Delete a stream."""
        return await self._request(
            "DELETE", f"/streams/{name}", timeout=timeout
        )

    # ------------------------------------------------------------------
    # API — Health / Info
    # ------------------------------------------------------------------

    async def health(self, *, timeout: Optional[float] = None) -> Dict[str, Any]:
        """Check server health."""
        return await self._request("GET", "/health", timeout=timeout)

    async def info(self, *, timeout: Optional[float] = None) -> Dict[str, Any]:
        """Get server info."""
        return await self._request("GET", "/info", timeout=timeout)

    # ------------------------------------------------------------------
    # API — Streaming subscription
    # ------------------------------------------------------------------

    async def subscribe_stream(
        self,
        stream: str,
        *,
        poll_interval: float = 1.0,
        max_events: Optional[int] = None,
        max_consecutive_errors: int = 3,
        timeout: Optional[float] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Async generator: poll stream and yield events until cancelled."""
        count: int = 0
        consecutive_errors: int = 0

        while True:
            try:
                result = await self.query(stream, timeout=timeout)
                consecutive_errors = 0
                raw_events: Any = result.get("events", [])
                for raw_event in raw_events:
                    yield cast(Dict[str, Any], raw_event)
                    count += 1
                    if max_events is not None and count >= max_events:
                        return
            except asyncio.CancelledError:
                raise
            except Exception:
                consecutive_errors += 1
                if consecutive_errors >= max_consecutive_errors:
                    raise
            await asyncio.sleep(poll_interval)
