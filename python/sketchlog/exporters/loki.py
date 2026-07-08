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
        if self.timeout <= 0:
            raise ValueError("timeout must be positive")


@dataclass
class LokiStream:
    """A single Loki log stream: a label set plus one or more log lines.

    Args:
        labels: Labels that identify this stream (merged with config labels
            by the exporter).
        lines: Non-empty list of log line strings.
        timestamps_ns: Optional per-line Unix timestamps in nanoseconds.
            Must have the same length as *lines* when provided.
    """

    labels: dict[str, str]
    lines: list[str]
    timestamps_ns: list[int] | None = None

    def __post_init__(self) -> None:
        if not self.lines:
            raise ValueError("lines must not be empty")
        if self.timestamps_ns is not None and len(self.timestamps_ns) != len(self.lines):
            raise ValueError("timestamps_ns length must match lines length")


class LokiExporter:
    """Forwards log lines to Grafana Loki via the HTTP push API.

    Use as a context manager to reuse a single HTTP connection::

        cfg = LokiConfig(url="http://loki:3100", labels={"env": "prod"})
        with LokiExporter(cfg) as exp:
            exp.push(["request received", "response 200"])

    Or call :meth:`push` directly without a context manager (a transient
    connection is created and closed per call)::

        exp = LokiExporter(cfg)
        exp.push(["standalone call"])
    """

    def __init__(self, config: LokiConfig) -> None:
        self._config = config
        self._client: httpx.Client | None = None

    # ── lifecycle ────────────────────────────────────────────────────────────

    def __enter__(self) -> "LokiExporter":
        self._client = self._make_client()
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    def close(self) -> None:
        """Close the underlying HTTP client and release resources."""
        if self._client is not None:
            self._client.close()
            self._client = None

    # ── internals ────────────────────────────────────────────────────────────

    def _make_client(self) -> httpx.Client:
        headers: dict[str, str] = {}
        if self._config.auth_token:
            headers["Authorization"] = f"Bearer {self._config.auth_token}"
        auth: tuple[str, str] | None = None
        if self._config.username and self._config.password:
            auth = (self._config.username, self._config.password)
        return httpx.Client(headers=headers, auth=auth, timeout=self._config.timeout)

    def _endpoint(self) -> str:
        return f"{self._config.url.rstrip('/')}/loki/api/v1/push"

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
        finally:
            if owned:
                client.close()

    # ── public API ───────────────────────────────────────────────────────────

    def push(self, lines: list[str], extra_labels: dict[str, str] | None = None) -> None:
        """Push a list of log lines to Loki.

        Args:
            lines: Non-empty list of log strings.
            extra_labels: Labels merged on top of the config labels for
                this push only.

        Raises:
            ValueError: If *lines* is empty.
            ExporterError: On HTTP error or network timeout.
        """
        if not lines:
            raise ValueError("lines must not be empty")
        stream = LokiStream(
            labels={**self._config.labels, **(extra_labels or {})},
            lines=lines,
        )
        self.push_stream(stream)

    def push_stream(self, stream: LokiStream) -> None:
        """Push a single :class:`LokiStream`.

        Args:
            stream: The stream to push.

        Raises:
            ExporterError: On HTTP error or network timeout.
        """
        self.push_streams([stream])

    def push_streams(self, streams: list[LokiStream]) -> None:
        """Push multiple :class:`LokiStream` objects in one HTTP request.

        Args:
            streams: Non-empty list of streams.

        Raises:
            ValueError: If *streams* is empty.
            ExporterError: On HTTP error or network timeout.
        """
        if not streams:
            raise ValueError("streams must not be empty")
        now_ns = time.time_ns()
        payload_streams = []
        for s in streams:
            ts_list = s.timestamps_ns if s.timestamps_ns is not None else [now_ns] * len(s.lines)
            values = [[str(ts), line] for ts, line in zip(ts_list, s.lines)]
            payload_streams.append({"stream": s.labels, "values": values})
        self._do_push({"streams": payload_streams})
