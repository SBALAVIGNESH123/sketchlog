"""Loki HTTP push exporter for SketchLog."""
from __future__ import annotations
import time
from dataclasses import dataclass, field
from typing import Any
import httpx
from .base import ExporterError


@dataclass(frozen=True)
class LokiConfig:
    """Configuration for the Loki exporter.

    Args:
        url: Base URL of the Loki instance, e.g. ``http://loki:3100``.
        timeout: HTTP request timeout in seconds (default 10).
        username: Optional HTTP Basic-auth username.
        password: Optional HTTP Basic-auth password.
        bearer_token: Optional Bearer token (mutually exclusive with Basic auth).
        tenant_id: Optional Loki tenant / org-id header value.
    """

    url: str
    timeout: float = 10.0
    username: str | None = None
    password: str | None = None
    bearer_token: str | None = None
    tenant_id: str | None = None

    def __post_init__(self) -> None:
        if not self.url:
            raise ValueError("url must not be empty")
        if self.bearer_token and (self.username or self.password):
            raise ValueError("bearer_token and Basic auth are mutually exclusive")
        object.__setattr__(self, "url", self.url.rstrip("/"))


@dataclass
class LokiStream:
    """A single Loki stream with its label-set and log lines.

    Args:
        labels: Mapping of label name → value, e.g. ``{"app": "myapp"}``.
        lines: List of log-line strings to push.
        timestamps: Optional list of nanosecond UNIX timestamps (one per line).
                    If omitted the current time is used for every line.
    """

    labels: dict[str, str]
    lines: list[str]
    timestamps: list[int] = field(default_factory=list)


class LokiExporter:
    """Synchronous Loki log exporter.

    Supports context-manager usage::

        with LokiExporter(cfg) as exp:
            exp.push(["log line"])

    Args:
        config: :class:`LokiConfig` instance.
        client: Optional pre-built ``httpx.Client`` (for testing / reuse).
    """

    def __init__(self, config: LokiConfig, client: httpx.Client | None = None) -> None:
        self._cfg = config
        self._client = client
        self._owned = client is None

    # ── context manager ───────────────────────────────────────────────────────
    def __enter__(self) -> "LokiExporter":
        if self._owned and self._client is None:
            self._client = self._make_client()
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    def close(self) -> None:
        """Close the underlying HTTP client if it was created by this exporter."""
        if self._owned and self._client is not None:
            self._client.close()
            self._client = None

    # ── public API ────────────────────────────────────────────────────────────
    def push(self, lines: list[str], labels: dict[str, str] | None = None) -> None:
        """Push a list of log lines with optional labels.

        Args:
            lines: Log lines to push.
            labels: Label key/value pairs (default ``{"source": "sketchlog"}``).

        Raises:
            ExporterError: On HTTP or transport failure.
        """
        stream = LokiStream(labels=labels or {"source": "sketchlog"}, lines=lines)
        self.push_stream(stream)

    def push_stream(self, stream: LokiStream) -> None:
        """Push a single :class:`LokiStream`.

        Raises:
            ExporterError: On HTTP or transport failure.
        """
        self.push_streams([stream])

    def push_streams(self, streams: list[LokiStream]) -> None:
        """Push multiple :class:`LokiStream` objects in one request.

        Raises:
            ExporterError: On HTTP or transport failure.
        """
        now_ns = str(int(time.time() * 1e9))
        payload: dict[str, Any] = {"streams": []}
        for s in streams:
            values = []
            for i, line in enumerate(s.lines):
                ts = str(s.timestamps[i]) if i < len(s.timestamps) else now_ns
                values.append([ts, line])
            payload["streams"].append({"stream": s.labels, "values": values})
        self._do_push(payload)

    # ── internals ─────────────────────────────────────────────────────────────
    def _endpoint(self) -> str:
        return f"{self._cfg.url}/loki/api/v1/push"

    def _make_client(self) -> httpx.Client:
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self._cfg.tenant_id:
            headers["X-Scope-OrgID"] = self._cfg.tenant_id
        if self._cfg.bearer_token:
            headers["Authorization"] = f"Bearer {self._cfg.bearer_token}"
        auth = None
        if self._cfg.username:
            auth = (self._cfg.username, self._cfg.password or "")
        return httpx.Client(headers=headers, auth=auth, timeout=self._cfg.timeout)

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
