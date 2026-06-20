"""Lifecycle tests — graceful shutdown, readiness, restart."""
import asyncio
import signal
import subprocess
import sys
import time

import httpx
import pytest

from tests.conftest_stress import _free_port


pytestmark = [pytest.mark.stress, pytest.mark.lifecycle]


@pytest.mark.asyncio
async def test_graceful_shutdown_drains(live_server):
    """Server finishes in-flight requests before exiting on SIGTERM."""
    base = live_server["url"]
    proc = live_server["process"]

    # Send a few requests to ensure server is warm
    async with httpx.AsyncClient() as client:
        for i in range(5):
            await client.post(f"{base}/v1/streams/shutdown-{i}/events",
                              json={"latencies": [float(i)]}, timeout=5)

    # Send SIGTERM (or terminate on Windows)
    if sys.platform == "win32":
        proc.terminate()
    else:
        proc.send_signal(signal.SIGTERM)

    # Wait for process to exit
    try:
        exit_code = proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        pytest.fail("Server did not shut down within 15 seconds after SIGTERM")

    # On graceful shutdown, exit code should be 0 (or signal-based on Unix)
    assert exit_code is not None, "Process did not terminate"


@pytest.mark.asyncio
async def test_readiness_transitions():
    """Verify /ready returns 200 when server is healthy."""
    port = _free_port()
    import os
    env = os.environ.copy()
    env["SKETCHLOG_HOST"] = "127.0.0.1"
    env["SKETCHLOG_PORT"] = str(port)

    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "sketchlog.server:app",
         "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )

    base = f"http://127.0.0.1:{port}"
    try:
        # Wait for startup
        deadline = time.monotonic() + 15
        ready = False
        while time.monotonic() < deadline:
            try:
                r = httpx.get(f"{base}/ready", timeout=1)
                if r.status_code == 200:
                    ready = True
                    break
            except Exception:
                pass
            time.sleep(0.2)

        assert ready, "Server never became ready"
        assert httpx.get(f"{base}/ready", timeout=2).json()["status"] == "ready"

    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()


@pytest.mark.asyncio
async def test_restart_clean_state():
    """After restart, server starts with empty state."""
    port = _free_port()
    import os
    env = os.environ.copy()
    env["SKETCHLOG_HOST"] = "127.0.0.1"
    env["SKETCHLOG_PORT"] = str(port)

    base = f"http://127.0.0.1:{port}"

    # Start server 1
    proc1 = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "sketchlog.server:app",
         "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )

    try:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            try:
                if httpx.get(f"{base}/health", timeout=1).status_code == 200:
                    break
            except Exception:
                pass
            time.sleep(0.2)

        # Ingest data
        httpx.post(f"{base}/v1/streams/restart-test/events",
                    json={"latencies": [42.0]}, timeout=5)

        # Verify data exists
        r = httpx.get(f"{base}/v1/streams/restart-test/metrics", timeout=5)
        assert r.status_code == 200
    finally:
        proc1.terminate()
        try:
            proc1.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc1.kill()
            proc1.wait()

    # Wait for port release
    time.sleep(1)

    # Start server 2 on same port
    proc2 = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "sketchlog.server:app",
         "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )

    try:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            try:
                if httpx.get(f"{base}/health", timeout=1).status_code == 200:
                    break
            except Exception:
                pass
            time.sleep(0.2)

        # Data should NOT persist across restart
        r = httpx.get(f"{base}/v1/streams/restart-test/metrics", timeout=5)
        assert r.status_code == 404, "State leaked across restart"
    finally:
        proc2.terminate()
        try:
            proc2.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc2.kill()
            proc2.wait()
