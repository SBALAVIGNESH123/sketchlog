from __future__ import annotations

"""
async_client.py — AsyncSketchLogClient
=======================================
Production-grade async Python client for SketchLog.

Features
--------
- Full async/await API (asyncio + aiohttp)
- Context-manager lifecycle (async with AsyncSketchLogClient(...) as c:)
- Connection pooling with configurable limits
- Exponential backoff with jitter on transient errors (408, 429, 5xx)
- Per-request and per-operation deadlines (timeout= arg)
- Async streaming subscription (subscribe_stream)
- Cancellation-safe: all operations honour asyncio.CancelledError
- Thread-safe: safe to share across tasks
- Typed: full PEP 484 / mypy-clean
- stdlib + aiohttp only (no other new runtime deps)

Quick start
-----------
    async with AsyncSketchLogClient("http://localhost:7700", token="tok") as c:
        await c.ingest("web", "latency_ms", [12.0, 34.0, 56.0])
        p99 = await c.query_percentile("web", "latency_ms", 0.99)
"""


import asyncio
import json
import logging
import random
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import (
    Any,
    AsyncIterator,
    Dict,
    List,
    Optional,
    Sequence,
    Tuple,
)

__all__ = [
    "AsyncSketchLogClient",
    "AsyncClientConfig",
    "SketchLogError",
    "SketchLogAuthError",
    "SketchLogRateLimitError",
    "SketchLogTimeoutError",
    "SketchLogServerError",
]

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class SketchLogError(Exception):
    """Base exception for all SketchLog client errors."""
    def __init__(self, message: str, status_code: Optional[int] = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class SketchLogAuthError(SketchLogError):
    """Raised on 401/403 responses."""


class SketchLogRateLimitError(SketchLogError):
    """Raised on 429 responses. retry_after_seconds may be set."""
    def __init__(self, message: str, retry_after_seconds: Optional[float] = None) -> None:
        super().__init__(message, status_code=429)
        self.retry_after_seconds = retry_after_seconds


class SketchLogTimeoutError(SketchLogError):
    """Raised when a request exceeds the configured deadline."""


class SketchLogServerError(SketchLogError):
    """Raised on 5xx responses."""


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AsyncClientConfig:
    """Immutable configuration for AsyncSketchLogClient.

    Parameters
    ----------
    base_url:
        SketchLog server base URL, e.g. ``"http://localhost:7700"``.
    token:
        Bearer token for authentication. Pass ``None`` for unauthenticated
        (development) servers.
    timeout_seconds:
        Default per-request timeout in seconds. Default: 30.
    connect_timeout_seconds:
        TCP connection timeout in seconds. Default: 10.
    max_connections:
        Maximum simultaneous connections in the pool. Default: 100.
    max_keepalive_connections:
        Maximum idle keep-alive connections. Default: 20.
    max_retries:
        Maximum number of retry attempts on transient errors. Default: 3.
    retry_backoff_base:
        Base backoff in seconds for exponential retry. Default: 0.5.
    retry_backoff_max:
        Maximum backoff cap in seconds. Default: 30.0.
    retry_jitter:
        Whether to add random jitter to retry delays. Default: True.
    verify_ssl:
        Whether to verify SSL certificates. Default: True.
    """
    base_url: str
    token: Optional[str] = None
    timeout_seconds: float = 30.0
    connect_timeout_seconds: float = 10.0
    max_connections: int = 100
    max_keepalive_connections: int = 20
    max_retries: int = 3
    retry_backoff_base: float = 0.5
    retry_backoff_max: float = 30.0
    retry_jitter: bool = True
    verify_ssl: bool = True

    def __post_init__(self) -> None:
        errors: List[str] = []
        if not self.base_url or not self.base_url.startswith(("http://", "https://")):
            errors.append("base_url must start with http:// or https://")
        if self.timeout_seconds <= 0:
            errors.append("timeout_seconds must be > 0")
        if self.connect_timeout_seconds <= 0:
            errors.append("connect_timeout_seconds must be > 0")
        if self.max_connections < 1:
            errors.append("max_connections must be >= 1")
        if self.max_retries < 0:
            errors.append("max_retries must be >= 0")
        if self.retry_backoff_base < 0:
            errors.append("retry_backoff_base must be >= 0")
        if errors:
            raise ValueError("AsyncClientConfig errors: " + "; ".join(errors))

    @property
    def _base_url_stripped(self) -> str:
        return self.base_url.rstrip("/")


# ---------------------------------------------------------------------------
# Retry helpers
# ---------------------------------------------------------------------------

_RETRYABLE_STATUS = frozenset({408, 429, 500, 502, 503, 504})


def _backoff(attempt: int, base: float, cap: float, jitter: bool) -> float:
    delay = min(base * (2 ** attempt), cap)
    if jitter:
        delay = delay * (0.5 + random.random() * 0.5)
    return delay


# ---------------------------------------------------------------------------
# Response helpers
# ---------------------------------------------------------------------------

async def _raise_for_status(response: Any, body: bytes) -> None:
    """Raise the appropriate SketchLogError for non-2xx responses."""
    status = response.status
    try:
        detail = json.loads(body).get("detail", body.decode(errors="replace"))
    except Exception:
        detail = body.decode(errors="replace")

    if status in (401, 403):
        raise SketchLogAuthError(f"HTTP {status}: {detail}", status_code=status)
    if status == 429:
        retry_after: Optional[float] = None
        raw_ra = response.headers.get("Retry-After")
        if raw_ra:
            try:
                retry_after = float(raw_ra)
            except ValueError:
                pass
        raise SketchLogRateLimitError(
            f"Rate limited: {detail}", retry_after_seconds=retry_after
        )
    if status >= 500:
        raise SketchLogServerError(f"HTTP {status}: {detail}", status_code=status)
    if status >= 400:
        raise SketchLogError(f"HTTP {status}: {detail}", status_code=status)


# ---------------------------------------------------------------------------
# Main client
# ---------------------------------------------------------------------------

class AsyncSketchLogClient:
    """Production-grade async client for SketchLog.

    Usage
    -----
    Preferred: context manager (handles session lifecycle automatically)::

        async with AsyncSketchLogClient("http://localhost:7700", token="tok") as c:
            await c.ingest("ns", "stream", [1.0, 2.0, 3.0])
            p99 = await c.query_percentile("ns", "stream", 0.99)

    Manual lifecycle::

        c = AsyncSketchLogClient("http://localhost:7700")
        await c.start()
        try:
            await c.ingest(...)
        finally:
            await c.close()
    """

    def __init__(
        self,
        base_url: str,
        *,
        token: Optional[str] = None,
        timeout_seconds: float = 30.0,
        connect_timeout_seconds: float = 10.0,
        max_connections: int = 100,
        max_keepalive_connections: int = 20,
        max_retries: int = 3,
        retry_backoff_base: float = 0.5,
        retry_backoff_max: float = 30.0,
        retry_jitter: bool = True,
        verify_ssl: bool = True,
        config: Optional[AsyncClientConfig] = None,
    ) -> None:
        if config is not None:
            self._cfg = config
        else:
            self._cfg = AsyncClientConfig(
                base_url=base_url,
                token=token,
                timeout_seconds=timeout_seconds,
                connect_timeout_seconds=connect_timeout_seconds,
                max_connections=max_connections,
                max_keepalive_connections=max_keepalive_connections,
                max_retries=max_retries,
                retry_backoff_base=retry_backoff_base,
                retry_backoff_max=retry_backoff_max,
                retry_jitter=retry_jitter,
                verify_ssl=verify_ssl,
            )
        self._session: Optional[Any] = None
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Open the connection pool. Called automatically by __aenter__."""
        try:
            import aiohttp
        except ImportError as exc:
            raise ImportError(
                "aiohttp is required for AsyncSketchLogClient. "
                "Install it with: pip install 'sketchlog[async]'"
            ) from exc

        async with self._lock:
            if self._session is not None:
                return
            connector = aiohttp.TCPConnector(
                limit=self._cfg.max_connections,
                keepalive_timeout=30.0,
                ssl=self._cfg.verify_ssl,
            )
            timeout = aiohttp.ClientTimeout(
                total=self._cfg.timeout_seconds,
                connect=self._cfg.connect_timeout_seconds,
            )
            self._session = aiohttp.ClientSession(
                connector=connector,
                timeout=timeout,
                headers=self._default_headers(),
            )
            logger.debug("AsyncSketchLogClient: session started → %s", self._cfg.base_url)

    async def close(self) -> None:
        """Close the connection pool. Called automatically by __aexit__."""
        async with self._lock:
            if self._session is not None:
                await self._session.close()
                self._session = None
                logger.debug("AsyncSketchLogClient: session closed")

    async def __aenter__(self) -> "AsyncSketchLogClient":
        await self.start()
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.close()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _default_headers(self) -> Dict[str, str]:
        headers: Dict[str, str] = {"Content-Type": "application/json"}
        if self._cfg.token:
            headers["Authorization"] = f"Bearer {self._cfg.token}"
        return headers

    def _url(self, path: str) -> str:
        return f"{self._cfg._base_url_stripped}{path}"

    def _session_or_raise(self) -> Any:
        if self._session is None:
            raise RuntimeError(
                "AsyncSketchLogClient is not started. "
                "Use 'async with AsyncSketchLogClient(...) as c:' or call await c.start() first."
            )
        return self._session

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: Optional[Any] = None,
        params: Optional[Dict[str, Any]] = None,
        timeout: Optional[float] = None,
    ) -> Any:
        """Execute an HTTP request with retries and exponential backoff."""
        session = self._session_or_raise()
        url = self._url(path)
        cfg = self._cfg
        last_exc: Optional[Exception] = None

        import aiohttp

        request_timeout = aiohttp.ClientTimeout(
            total=timeout or cfg.timeout_seconds,
            connect=cfg.connect_timeout_seconds,
        )

        for attempt in range(cfg.max_retries + 1):
            try:
                async with session.request(
                    method,
                    url,
                    json=json_body,
                    params=params,
                    timeout=request_timeout,
                ) as resp:
                    body = await resp.read()
                    if resp.status in _RETRYABLE_STATUS and attempt < cfg.max_retries:
                        delay = _backoff(
                            attempt,
                            cfg.retry_backoff_base,
                            cfg.retry_backoff_max,
                            cfg.retry_jitter,
                        )
                        logger.debug(
                            "AsyncSketchLogClient: %s %s → %d, retry in %.2fs (attempt %d/%d)",
                            method, path, resp.status, delay, attempt + 1, cfg.max_retries,
                        )
                        await asyncio.sleep(delay)
                        continue
                    await _raise_for_status(resp, body)
                    try:
                        return json.loads(body)
                    except json.JSONDecodeError:
                        return body.decode(errors="replace")

            except asyncio.TimeoutError as exc:
                last_exc = SketchLogTimeoutError(
                    f"Request timed out after {timeout or cfg.timeout_seconds}s: {method} {path}"
                )
                if attempt < cfg.max_retries:
                    delay = _backoff(attempt, cfg.retry_backoff_base, cfg.retry_backoff_max, cfg.retry_jitter)
                    await asyncio.sleep(delay)
            except asyncio.CancelledError:
                raise
            except (SketchLogAuthError, SketchLogRateLimitError):
                raise
            except SketchLogError:
                raise
            except Exception as exc:
                last_exc = exc
                if attempt < cfg.max_retries:
                    delay = _backoff(attempt, cfg.retry_backoff_base, cfg.retry_backoff_max, cfg.retry_jitter)
                    await asyncio.sleep(delay)

        raise last_exc or SketchLogError(f"Request failed after {cfg.max_retries} retries: {method} {path}")

    # ------------------------------------------------------------------
    # Public API — Ingest
    # ------------------------------------------------------------------

    async def ingest(
        self,
        namespace: str,
        stream: str,
        values: Sequence[float],
        *,
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Ingest a batch of telemetry values into a stream.

        Parameters
        ----------
        namespace:
            Target namespace (e.g. ``"production"``).
        stream:
            Target stream name (e.g. ``"latency_ms"``).
        values:
            Sequence of numeric telemetry values to ingest.
        timeout:
            Per-call timeout override in seconds.

        Returns
        -------
        dict
            Server response with ingestion confirmation.
        """
        return await self._request(
            "POST",
            f"/api/v1/ingest/{namespace}/{stream}",
            json_body={"values": list(values)},
            timeout=timeout,
        )

    async def ingest_event(
        self,
        namespace: str,
        stream: str,
        event: str,
        *,
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Record a discrete event for frequency/cardinality tracking.

        Parameters
        ----------
        namespace:
            Target namespace.
        stream:
            Target stream name.
        event:
            Event identifier string (e.g. user ID, URL, error code).
        timeout:
            Per-call timeout override in seconds.
        """
        return await self._request(
            "POST",
            f"/api/v1/event/{namespace}/{stream}",
            json_body={"event": event},
            timeout=timeout,
        )

    # ------------------------------------------------------------------
    # Public API — Query
    # ------------------------------------------------------------------

    async def query_percentile(
        self,
        namespace: str,
        stream: str,
        quantile: float,
        *,
        timeout: Optional[float] = None,
    ) -> float:
        """Query a percentile from a stream.

        Parameters
        ----------
        namespace:
            Source namespace.
        stream:
            Source stream name.
        quantile:
            Percentile as a fraction 0.0–1.0 (e.g. ``0.99`` for p99).
        timeout:
            Per-call timeout override in seconds.

        Returns
        -------
        float
            Estimated percentile value with bounded relative error.

        Raises
        ------
        ValueError
            If quantile is not in [0.0, 1.0].
        """
        if not 0.0 <= quantile <= 1.0:
            raise ValueError(f"quantile must be in [0.0, 1.0], got {quantile}")
        result = await self._request(
            "GET",
            f"/api/v1/query/{namespace}/{stream}/percentile",
            params={"q": quantile},
            timeout=timeout,
        )
        return float(result["value"])

    async def query_count(
        self,
        namespace: str,
        stream: str,
        *,
        timeout: Optional[float] = None,
    ) -> int:
        """Query total event count for a stream."""
        result = await self._request(
            "GET",
            f"/api/v1/query/{namespace}/{stream}/count",
            timeout=timeout,
        )
        return int(result["count"])

    async def query_cardinality(
        self,
        namespace: str,
        stream: str,
        *,
        timeout: Optional[float] = None,
    ) -> int:
        """Query estimated unique event cardinality (HyperLogLog)."""
        result = await self._request(
            "GET",
            f"/api/v1/query/{namespace}/{stream}/cardinality",
            timeout=timeout,
        )
        return int(result["cardinality"])

    async def query_frequency(
        self,
        namespace: str,
        stream: str,
        event: str,
        *,
        timeout: Optional[float] = None,
    ) -> int:
        """Query estimated event frequency (Count-Min Sketch)."""
        result = await self._request(
            "GET",
            f"/api/v1/query/{namespace}/{stream}/frequency",
            params={"event": event},
            timeout=timeout,
        )
        return int(result["frequency"])

    async def query_summary(
        self,
        namespace: str,
        stream: str,
        *,
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Query full stream summary (p50, p95, p99, count, cardinality)."""
        return await self._request(
            "GET",
            f"/api/v1/query/{namespace}/{stream}/summary",
            timeout=timeout,
        )

    # ------------------------------------------------------------------
    # Public API — Namespaces & Streams
    # ------------------------------------------------------------------

    async def list_namespaces(self, *, timeout: Optional[float] = None) -> List[str]:
        """List all available namespaces."""
        result = await self._request("GET", "/api/v1/namespaces", timeout=timeout)
        return list(result.get("namespaces", []))

    async def list_streams(
        self,
        namespace: str,
        *,
        timeout: Optional[float] = None,
    ) -> List[str]:
        """List all streams in a namespace."""
        result = await self._request(
            "GET", f"/api/v1/namespaces/{namespace}/streams", timeout=timeout
        )
        return list(result.get("streams", []))

    async def delete_stream(
        self,
        namespace: str,
        stream: str,
        *,
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Delete a stream and all its data."""
        return await self._request(
            "DELETE",
            f"/api/v1/namespaces/{namespace}/streams/{stream}",
            timeout=timeout,
        )

    # ------------------------------------------------------------------
    # Public API — Health & Info
    # ------------------------------------------------------------------

    async def health(self, *, timeout: Optional[float] = None) -> Dict[str, Any]:
        """Check server health. Returns status, version, uptime."""
        return await self._request("GET", "/health", timeout=timeout)

    async def info(self, *, timeout: Optional[float] = None) -> Dict[str, Any]:
        """Get server info (version, build, config summary)."""
        return await self._request("GET", "/info", timeout=timeout)

    # ------------------------------------------------------------------
    # Public API — Streaming subscription
    # ------------------------------------------------------------------

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
        """Subscribe to a live stream, yielding summaries at each interval.

        Usage
        -----
        ::

            async with client.subscribe_stream("prod", "latency_ms") as events:
                async for summary in events:
                    print(summary["p99"])

        Parameters
        ----------
        namespace:
            Source namespace.
        stream:
            Source stream name.
        interval_seconds:
            How often to poll for new summaries. Default: 1.0.
        max_events:
            Stop after this many summary events. Default: unlimited.
        timeout:
            Per-poll timeout override in seconds.
        """
        async def _generator() -> AsyncIterator[Dict[str, Any]]:
            count = 0
            consecutive_errors = 0
            max_consecutive_errors = 10
            while max_events is None or count < max_events:
                try:
                    summary = await self.query_summary(namespace, stream, timeout=timeout)
                    summary["_ts"] = time.time()
                    consecutive_errors = 0
                    yield summary
                    count += 1
                    await asyncio.sleep(interval_seconds)
                except asyncio.CancelledError:
                    return
                except SketchLogError as exc:
                    consecutive_errors += 1
                    logger.warning(
                        "subscribe_stream: error polling %s/%s: %s (consecutive=%d/%d)",
                        namespace, stream, exc, consecutive_errors, max_consecutive_errors,
                    )
                    if consecutive_errors >= max_consecutive_errors:
                        raise
                    await asyncio.sleep(interval_seconds)

        yield _generator()

    # ------------------------------------------------------------------
    # Repr
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        status = "open" if self._session is not None else "closed"
        return (
            f"AsyncSketchLogClient("
            f"base_url={self._cfg.base_url!r}, "
            f"status={status!r})"
        )