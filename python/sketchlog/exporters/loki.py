"""Grafana Loki export integration for SketchLog."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

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
    labels: Dict[str, str] = field(default_factory=dict)
    auth_token: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None
    timeout: float = 10.0

    def __post_init__(self) -> None:
        if not self.url:
            raise ValueError("url must not be empty")
        if (self.username is None) != (self.password is None):
            raise ValueError("username and password must both be set or both be None")


@dataclass
class LokiStream:
    """A single labelled stream of log lines.

    Args:
        labels: Label key-value pairs identifying this stream.
        lines: Log line strings to push.
        timestamps_ns: Optional per-line timestamps in nanoseconds since epoch.
            If omitted, the current time is used for all lines.
    """

    labels: Dict[str, str]
    lines: List[str]
    timestamps_ns: Optional[List[int]] = None


class LokiExporter:
    """Pushes log streams to Grafana Loki via the HTTP push API.

    Can be used standalone or as a context manager::

        with LokiExporter(cfg) as exp:
            exp.push(["line one", "line two"])

    Args:
        config: :class:`LokiConfig` instance.
        client: Optional pre-configured :class:`httpx.Client`.  If *None* a
            short-lived client is created for each request.
    """

    def __init__(self, config: LokiConfig, client: Optional[httpx.Client] = None) -> None:
        self._config = config
        self._client = client

    # ── context manager ──────────────────────────────────────────────────────

    def __enter__(self) -> "LokiExporter":
        self.open()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def open(self) -> None:
        """Open a persistent HTTP connection."""
        if self._client is None:
            self._client = self._make_client()

    def close(self) -> None:
        """Close the underlying HTTP client (idempotent)."""
        if self._client is not None:
            self._client.close()
            self._client = None

    # ── public API ───────────────────────────────────────────────────────────

    def push(self, lines: List[str], timestamps_ns: Optional[List[int]] = None) -> None:
        """Push *lines* using the exporter's default label set."""
        stream = LokiStream(labels=self._config.labels, lines=lines,
                            timestamps_ns=timestamps_ns)
        self.push_stream(stream)

    def push_stream(self, stream: LokiStream) -> None:
        """Push a single :class:`LokiStream`."""
        self.push_streams([stream])

    def push_streams(self, streams: List[LokiStream]) -> None:
        """Push multiple :class:`LokiStream` objects in one request."""
        now_ns = str(time.time_ns())
        payload: Dict[str, object] = {"streams": []}
        for s in streams:
            ts_list = s.timestamps_ns or [None] * len(s.lines)
            values = [
                [str(ts) if ts is not None else now_ns, line]
                for ts, line in zip(ts_list, s.lines)
            ]
            stream_entry: Dict[str, object] = {"stream": s.labels, "values": values}
            assert isinstance(payload["streams"], list)
            payload["streams"].append(stream_entry)
        self._do_push(payload)

    # ── internals ────────────────────────────────────────────────────────────

    def _endpoint(self) -> str:
        return self._config.url.rstrip("/") + "/loki/api/v1/push"

    def _make_client(self) -> httpx.Client:
        cfg = self._config
        headers: Dict[str, str] = {"Content-Type": "application/json"}
        if cfg.auth_token:
            headers["Authorization"] = f"Bearer {cfg.auth_token}"
        auth: Optional[Tuple[str, str]] = None
        if cfg.username and cfg.password:
            auth = (cfg.username, cfg.password)
        return httpx.Client(headers=headers, auth=auth, timeout=cfg.timeout)

    def _do_push(self, payload: Dict[str, object]) -> None:
        owned = self._client is None
        _client: httpx.Client = self._client if self._client is not None else self._make_client()
        try:
            resp = _client.post(self._endpoint(), json=payload)
            try:
                resp.raise_for_status()
            except httpx.HTTPStatusError as exc:
                raise ExporterError(str(exc), status_code=exc.response.status_code) from exc
        except ExporterError:
            raise
        except httpx.TimeoutException as exc:
            raise ExporterError(f"Loki push timed out: {exc}") from exc
        except httpx.RequestError as exc:
            raise ExporterError(f"Loki push failed: {exc}") from exc
        finally:
            if owned:
                _client.close()
