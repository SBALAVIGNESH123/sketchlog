"""Generate deterministic telemetry for the launch demo."""

from __future__ import annotations

import json
import math
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

SERVER_URL = os.getenv("SKETCHLOG_SERVER_URL", "http://server:8000").rstrip("/")
HEALTH_PORT = int(os.getenv("DEMO_HEALTH_PORT", "8090"))


def request(path: str, payload: dict[str, Any] | None = None) -> Any:
    body = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        f"{SERVER_URL}{path}",
        data=body,
        headers={"Content-Type": "application/json"} if body else {},
        method="POST" if body else "GET",
    )
    with urllib.request.urlopen(req, timeout=10) as response:
        raw = response.read()
        return json.loads(raw) if raw else None


def wait_for_server() -> None:
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        try:
            if request("/ready") == {"status": "ready"}:
                return
        except (OSError, urllib.error.URLError, json.JSONDecodeError):
            pass
        time.sleep(1)
    raise RuntimeError("SketchLog server did not become ready within 120 seconds")


def ingest(path: str, latencies: list[float], users: list[str], errors: int) -> None:
    response = request(
        path,
        {"latencies": latencies, "uniques": users, "events": {"HTTP_500": errors}},
    )
    if response != {"status": "accepted"}:
        raise RuntimeError(f"Unexpected ingestion response: {response!r}")


def initial_seed() -> None:
    baseline = [34 + 4 * math.sin(index / 13) + (index % 7) * 0.35 for index in range(800)]
    current = [
        88 + 16 * math.sin(index / 11) + (210 if index % 17 == 0 else 0)
        for index in range(800)
    ]
    ingest(
        "/v1/streams/demo-baseline/events",
        baseline,
        [f"baseline-user-{index % 220:03d}" for index in range(800)],
        2,
    )
    ingest(
        "/v1/streams/demo-current/events",
        current,
        [f"live-user-{index % 410:03d}" for index in range(800)],
        31,
    )
    ingest(
        "/v1/namespaces/acme/streams/checkout/events",
        [42 + (index % 13) * 1.1 for index in range(320)],
        [f"acme-{index % 140}" for index in range(320)],
        3,
    )
    ingest(
        "/v1/namespaces/globex/streams/checkout/events",
        [125 + (index % 19) * 2.6 for index in range(320)],
        [f"globex-{index % 95}" for index in range(320)],
        12,
    )


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        status = 200 if self.path == "/health" else 404
        payload = b'{"status":"ready"}' if status == 200 else b'{"status":"not_found"}'
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, _format: str, *_args: object) -> None:
        return


def start_health_server() -> None:
    server = ThreadingHTTPServer(("0.0.0.0", HEALTH_PORT), HealthHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()


def run() -> None:
    wait_for_server()
    initial_seed()
    start_health_server()
    print("Deterministic demo telemetry is ready.", flush=True)

    tick = 0
    while True:
        latencies = [
            82 + 18 * math.sin((tick * 48 + index) / 9) + (235 if (tick * 48 + index) % 29 == 0 else 0)
            for index in range(48)
        ]
        users = [f"live-user-{(tick * 37 + index) % 2500:04d}" for index in range(48)]
        ingest("/v1/streams/demo-current/events", latencies, users, 1 + tick % 3)
        tick += 1
        time.sleep(1.25)


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:
        print(f"Telemetry generator failed: {exc}", file=sys.stderr, flush=True)
        raise
