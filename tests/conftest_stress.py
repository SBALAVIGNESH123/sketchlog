"""Shared fixtures for stress, load, soak, and lifecycle tests."""
import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def resource_envelope():
    path = Path(__file__).parent / "resource_envelope.json"
    with open(path) as f:
        return json.load(f)


@pytest.fixture(scope="session")
def results_dir():
    d = Path(__file__).parent / "results"
    d.mkdir(exist_ok=True)
    return d


@pytest.fixture()
def live_server():
    """Start a real uvicorn server on a random port, yield base URL, then stop."""
    port = _free_port()
    env = os.environ.copy()
    env["SKETCHLOG_HOST"] = "127.0.0.1"
    env["SKETCHLOG_PORT"] = str(port)
    env["SKETCHLOG_MAX_STREAMS"] = "1000"
    env["SKETCHLOG_MAX_BATCH_SIZE"] = "10000"
    env["SKETCHLOG_MAX_REQUEST_BYTES"] = "1048576"

    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "sketchlog.server:app",
         "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    base_url = f"http://127.0.0.1:{port}"

    # Wait for server to be ready
    import httpx
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        try:
            r = httpx.get(f"{base_url}/health", timeout=1)
            if r.status_code == 200:
                break
        except Exception:
            pass
        time.sleep(0.2)
    else:
        proc.kill()
        raise RuntimeError("Server did not start in time")

    yield {"url": base_url, "process": proc, "port": port}

    # Graceful shutdown
    if proc.poll() is None:
        if sys.platform == "win32":
            proc.terminate()
        else:
            proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
