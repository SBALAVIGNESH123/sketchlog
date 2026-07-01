"""Continuously expose health only after the complete launch demo passes."""

from __future__ import annotations

import json
import os
import base64
import hashlib
import socket
import sys
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlsplit

SERVER_URL = os.getenv("SKETCHLOG_SERVER_URL", "http://server:8000").rstrip("/")
DASHBOARD_URL = os.getenv("SKETCHLOG_DASHBOARD_URL", "http://dashboard:8080").rstrip("/")


def get(url: str) -> tuple[int, bytes, str]:
    """Fetch a URL and return status, body, and content type."""
    with urllib.request.urlopen(url, timeout=10) as response:
        return response.status, response.read(), response.headers.get("Content-Type", "")


def json_request(path: str, payload: dict[str, Any] | None = None) -> Any:
    """Send a JSON request directly to the SketchLog server."""
    body = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(
        f"{SERVER_URL}{path}",
        data=body,
        headers={"Content-Type": "application/json"} if body else {},
        method="POST" if body else "GET",
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read())


def read_exact(connection: socket.socket, length: int) -> bytes:
    """Read exactly length bytes or fail when the socket closes."""
    chunks = bytearray()
    while len(chunks) < length:
        chunk = connection.recv(length - len(chunks))
        if not chunk:
            raise ConnectionError("WebSocket closed before a complete frame arrived")
        chunks.extend(chunk)
    return bytes(chunks)


def recv_some(connection: socket.socket, length: int) -> bytes:
    """Read a non-empty socket chunk."""
    chunk = connection.recv(length)
    if not chunk:
        raise ConnectionError("WebSocket closed unexpectedly")
    return chunk


def websocket_state() -> dict[str, Any]:
    """Perform an RFC 6455 handshake and decode one live state frame."""
    target = urlsplit(DASHBOARD_URL)
    host = target.hostname or "dashboard"
    port = target.port or 80
    key = base64.b64encode(b"sketchlog-demo-verifier").decode()
    expected_accept = base64.b64encode(
        hashlib.sha1(f"{key}258EAFA5-E914-47DA-95CA-C5AB0DC85B11".encode()).digest()
    ).decode()
    with socket.create_connection((host, port), timeout=10) as connection:
        connection.settimeout(10)
        connection.sendall(
            (
                "GET /api/v1/streams/demo-current/ws HTTP/1.1\r\n"
                f"Host: {host}:{port}\r\n"
                "Upgrade: websocket\r\n"
                "Connection: Upgrade\r\n"
                f"Sec-WebSocket-Key: {key}\r\n"
                "Sec-WebSocket-Version: 13\r\n\r\n"
            ).encode()
        )
        response = bytearray()
        while b"\r\n\r\n" not in response:
            response.extend(recv_some(connection, 1024))
        headers, buffered = bytes(response).split(b"\r\n\r\n", 1)
        assert headers.startswith(b"HTTP/1.1 101"), headers.decode(errors="replace")
        assert f"sec-websocket-accept: {expected_accept}".lower().encode() in headers.lower()

        frame = bytearray(buffered)
        while len(frame) < 2:
            frame.extend(recv_some(connection, 2 - len(frame)))
        first, second = frame[0], frame[1]
        assert first & 0x0F == 1, f"Expected text frame, received opcode {first & 0x0F}"
        length = second & 0x7F
        offset = 2
        if length == 126:
            while len(frame) < offset + 2:
                frame.extend(recv_some(connection, offset + 2 - len(frame)))
            length = int.from_bytes(frame[offset:offset + 2], "big")
            offset += 2
        elif length == 127:
            while len(frame) < offset + 8:
                frame.extend(recv_some(connection, offset + 8 - len(frame)))
            length = int.from_bytes(frame[offset:offset + 8], "big")
            offset += 8
        if second & 0x80:
            raise AssertionError("Server frames must not be masked")
        payload = bytes(frame[offset:])
        if len(payload) < length:
            payload += read_exact(connection, length - len(payload))
        return json.loads(payload[:length])


def verify() -> None:
    """Verify every feature path required by the launch dashboard."""
    deadline = time.monotonic() + 120
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            current = json_request("/v1/streams/demo-current/metrics")
            baseline = json_request("/v1/streams/demo-baseline/metrics")
            acme = json_request("/v1/namespaces/acme/streams/checkout/metrics")
            globex = json_request("/v1/namespaces/globex/streams/checkout/metrics")
            anomaly = json_request(
                "/v1/streams/demo-current/anomaly?baseline_stream_id=demo-baseline&sensitivity=0.20"
            )
            query = json_request(
                "/v1/query",
                {"query": 'SELECT p99(latency), count_unique(users) FROM "default/demo-current"'},
            )
            metrics_status, metrics_body, _ = get(f"{SERVER_URL}/metrics")
            page_status, page_body, page_type = get(DASHBOARD_URL)
            live_state = websocket_state()

            assert current["total_events"] >= 800
            assert current["p99"] > baseline["p99"]
            assert current["memory_footprint_bytes"] > 0
            assert anomaly["is_anomalous"] is True
            assert anomaly["anomaly_score"] >= 0.20
            assert len(query["results"]) == 2
            assert acme["p99"] < globex["p99"]
            assert metrics_status == 200 and b"sketchlog_events_ingested_total" in metrics_body
            assert page_status == 200 and "text/html" in page_type
            assert b"SketchLog" in page_body
            assert int(live_state["metrics"]["total_events"]) >= current["total_events"]
            assert float(live_state["metrics"]["p99"]) > 0
            return
        except (AssertionError, KeyError, OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
            last_error = exc
            time.sleep(1)
    raise RuntimeError(f"Launch demo verification timed out: {last_error}")


class HealthHandler(BaseHTTPRequestHandler):
    """Serve the successful end-to-end verification probe."""

    def do_GET(self) -> None:  # noqa: N802
        """Expose verification success to the Compose health check."""
        payload = b'{"status":"verified"}'
        self.send_response(200 if self.path == "/health" else 404)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, _format: str, *_args: object) -> None:
        """Suppress per-probe access logs."""
        return


if __name__ == "__main__":
    try:
        verify()
        print("Launch demo smoke verification passed.", flush=True)
        ThreadingHTTPServer(("0.0.0.0", 8091), HealthHandler).serve_forever()
    except Exception as exc:
        print(f"Smoke verification failed: {exc}", file=sys.stderr, flush=True)
        raise
