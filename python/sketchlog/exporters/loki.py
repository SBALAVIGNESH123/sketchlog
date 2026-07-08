"""Grafana Loki export integration for SketchLog.

Pushes log streams to a Loki instance via the HTTP push API
(``POST /loki/api/v1/push``).  Supports bearer-token and
HTTP basic authentication, arbitrary label sets, custom per-line
timestamps, and both standalone and context-manager usage patterns.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from sketchlog.exporters.base import ExporterError


@dataclass(frozen=True)
class LokiConfig:
    """Immutable configuration for :class:`LokiExporter`.

    Args:
        url: Base URL of the Loki instance, e.g. ``http://loki:3100``.
        labels: Default label set applied to every pushed stream.
        auth_token: Optional Bearer token for authentication.
        username: HTTP basic-auth username (requires *password*).
        password: HTTP basic-auth password (requires *username*).
        timeout: Request timeout in seconds (default ``10.0``).
    """

    url: str
    labels: dict[str, str] = field(default_factory=dict)
    auth_token: str | None = None
    username: str | None = None
    password: str | None = None
    timeout: float = 10.0

    def __post_init__(self) -> None:
        if not self.url:
            raise ValueError("url must not be empty")
        if (self.username is None) != (self.password is None):
            raise ValueError("username and password must both be set or both be None")
        object.__setattr__(self, "url", self.url.rstrip("/"))


@dataclass
class LokiStream:
    """A single Loki stream with a label set and log lines.

    Args:
        labels: Label set that identifies this stream.
        lines: Log lines to push.  Each entry is either a plain string
            (timestamp auto-assigned) or a ``(ns_timestamp, line)`` tuple.
    """

    labels: dict[str, str]
    lines: list[str | tuple[int, str]] = field(default_factory=list)


class LokiExporter:
    """Pushes log lines to Grafana Loki.

    Can be used standalone or as a context manager::

        with LokiExporter(cfg) as exp:
            exp.push(["something happened"])
    """

    def __init__(self, config: LokiConfig, client: httpx.Client | None = None) -> None:
        self._cfg = config
        self._client = client
        self._owned = client is None

    # ── lifecycle ──────────────────────────────────────────────────────────

    def open(self) -> None:
        """Open a persistent HTTP connection pool."""
        if self._client is None:
            self._client = self._make_client()

    def close(self) -> None:
        """Close the persistent connection pool (idempotent)."""
        if self._client is not None and self._owned:
            self._client.close()
            self._client = None

    def __enter__(self) -> LokiExporter:
        self.open()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    # ── public API ─────────────────────────────────────────────────────────

    def push(
        self,
        lines: list[str | tuple[int, str]],
        labels: dict[str, str] | None = None,
    ) -> None:
        """Push *lines* as a single stream using *labels* (or the config defaults).

        Args:
            lines: Log lines.  Each entry is a plain string or a
                ``(nanosecond_timestamp, line)`` tuple.
            labels: Override the config default labels for this push.
        """
        merged = {**self._cfg.labels, **(labels or {})}
        stream = LokiStream(labels=merged, lines=lines)
        self.push_stream(stream)

    def push_stream(self, stream: LokiStream) -> None:
        """Push a single :class:`LokiStream`."""
        self.push_streams([stream])

    def push_streams(self, streams: list[LokiStream]) -> None:
        """Push multiple :class:`LokiStream` objects in one request."""
        now_ns = time.time_ns()
        payload: dict[str, Any] = {"streams": []}
        for s in streams:
            values = []
            for ln in s.lines:
                if isinstance(ln, tuple):
                    ts, msg = ln
                else:
                    ts, msg = now_ns, ln
                values.append([str(ts), msg])
            payload["streams"].append({"stream": s.labels, "values": values})
        self._do_push(payload)

    # ── internal ───────────────────────────────────────────────────────────

    def _endpoint(self) -> str:
        return f"{self._cfg.url}/loki/api/v1/push"

    def _make_client(self) -> httpx.Client:
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self._cfg.auth_token:
            headers["Authorization"] = f"Bearer {self._cfg.auth_token}"
        auth: tuple[str, str] | None = None
        if self._cfg.username and self._cfg.password:
            auth = (self._cfg.username, self._cfg.password)
        return httpx.Client(
            headers=headers,
            auth=auth,
            timeout=self._cfg.timeout,
        )

    def _do_push(self, payload: dict[str, Any]) -> None:
        client = self._client
        owned = client is None
        if owned:
            client = self._make_client()
        try:
            resp = client.post(self._endpoint(), json=payload)
            try:
                resp.raise_for_status()
            except httpx.HTTPStatusError as exc:
                raise ExporterError(str(exc), status_code=exc.response.status_code) from exc
        except ExporterError:
            raise
        except httpx.TimeoutException as exc:
            raise ExporterError(f"Loki push timed out: {exc}") from exc
        except httpx.RequestError as exc:
            raise ExporterError(f"Loki transport error: {exc}") from exc
        finally:
            if owned:
                client.close()
