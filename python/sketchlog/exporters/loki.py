"""Loki HTTP push exporter for SketchLog."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import httpx

from .base import ExporterError


@dataclass(frozen=True)
class LokiConfig:
    """Configuration for the Loki exporter.

    Args:
        url: Base URL of the Loki instance (e.g. ``http://localhost:3100``).
        timeout: HTTP request timeout in seconds.
        username: Optional HTTP Basic auth username.
        password: Optional HTTP Basic auth password.
        bearer_token: Optional Bearer token for authentication.
        extra_headers: Additional HTTP headers to include in every request.
    """

    url: str
    timeout: float = 10.0
    username: Optional[str] = None
    password: Optional[str] = None
    bearer_token: Optional[str] = None
    extra_headers: Dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.url:
            raise ValueError("url must not be empty")
        if self.timeout <= 0:
            raise ValueError("timeout must be > 0")
        if self.username and not self.password:
            raise ValueError("password required when username is set")
        if self.bearer_token and self.username:
            raise ValueError("use either bearer_token or username/password, not both")


@dataclass
class LokiStream:
    """A single Loki stream with labels and log lines.

    Args:
        labels: Dict of label key-value pairs for this stream.
        lines: List of log line strings.
        timestamps_ns: Optional list of nanosecond timestamps (one per line).
    """

    labels: Dict[str, str]
    lines: List[str]
    timestamps_ns: Optional[List[int]] = None

    def to_payload(self) -> Dict[str, Any]:
        """Serialize this stream to a Loki push payload dict."""
        values: List[List[str]] = []
        for i, line in enumerate(self.lines):
            if self.timestamps_ns and i < len(self.timestamps_ns):
                ts = str(self.timestamps_ns[i])
            else:
                ts = str(time.time_ns())
            values.append([ts, line])
        return {"stream": dict(self.labels), "values": values}


class LokiExporter:
    """Push log streams to Grafana Loki.

    Can be used as a context manager or standalone with explicit ``close()``.

    Example::

        cfg = LokiConfig(url="http://localhost:3100")
        with LokiExporter(cfg) as exp:
            exp.push(["request completed", "response sent"],
                     labels={"app": "myapp", "env": "prod"})
    """

    def __init__(self, config: LokiConfig, client: Optional[httpx.Client] = None) -> None:
        self._config = config
        self._client: Optional[httpx.Client] = client
        self._owned = client is None

    def _make_client(self) -> httpx.Client:
        cfg = self._config
        auth: Optional[Tuple[str, str]] = None
        if cfg.username and cfg.password:
            auth = (cfg.username, cfg.password)
        headers: Dict[str, str] = {"Content-Type": "application/json"}
        if cfg.bearer_token:
            headers["Authorization"] = f"Bearer {cfg.bearer_token}"
        headers.update(cfg.extra_headers)
        return httpx.Client(auth=auth, headers=headers, timeout=cfg.timeout)

    def _endpoint(self) -> str:
        return self._config.url.rstrip("/") + "/loki/api/v1/push"

    def open(self) -> "LokiExporter":
        """Open the underlying HTTP client (called automatically by context manager)."""
        if self._client is None:
            self._client = self._make_client()
        return self

    def close(self) -> None:
        """Close the underlying HTTP client."""
        if self._client is not None and self._owned:
            self._client.close()
            self._client = None

    def __enter__(self) -> "LokiExporter":
        return self.open()

    def __exit__(self, *_: Any) -> None:
        self.close()

    def _do_push(self, payload: Dict[str, Any]) -> None:
        # Use local variable so mypy can narrow Optional[httpx.Client] -> httpx.Client
        client = self._client
        if client is None:
            client = self._make_client()
            owned = True
        else:
            owned = False
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
            raise ExporterError(f"Loki connection error: {exc}") from exc
        finally:
            if owned:
                client.close()

    def push(
        self,
        lines: List[str],
        labels: Optional[Dict[str, str]] = None,
        timestamps_ns: Optional[List[int]] = None,
    ) -> None:
        """Push log lines to Loki.

        Args:
            lines: List of log line strings.
            labels: Optional label dict. Defaults to ``{"source": "sketchlog"}``.
            timestamps_ns: Optional nanosecond timestamps (one per line).
        """
        stream = LokiStream(
            labels=labels or {"source": "sketchlog"},
            lines=lines,
            timestamps_ns=timestamps_ns,
        )
        self._do_push({"streams": [stream.to_payload()]})

    def push_stream(self, stream: LokiStream) -> None:
        """Push a single :class:`LokiStream` to Loki."""
        self._do_push({"streams": [stream.to_payload()]})

    def push_streams(self, streams: List[LokiStream]) -> None:
        """Push multiple :class:`LokiStream` objects in one request."""
        self._do_push({"streams": [s.to_payload() for s in streams]})
