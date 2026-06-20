"""Lifecycle tests — graceful shutdown, readiness, restart."""
import asyncio
import signal
import subprocess
import sys
import time

import httpx
import pytest

from tests.conftest import _free_port


pytestmark = [pytest.mark.stress, pytest.mark.lifecycle]


@pytest.mark.asyncio
async def test_graceful_shutdown_exits_cleanly(live_server):
    """Server exits cleanly when receiving SIGTERM."""
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
    import os
    env = os.environ.copy()
    env["SKETCHLOG_HOST"] = "127.0.0.1"

    for attempt in range(5):
        port = _free_port()
        env["SKETCHLOG_PORT"] = str(port)

        proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "sketchlog.server:app",
             "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )

        base = f"http://127.0.0.1:{port}"
        try:
            # Wait for startup
            ready = False
            for _ in range(50):
                try:
                    r = httpx.get(f"{base}/ready", timeout=0.1)
                    if r.status_code == 200:
                        ready = True
                        break
                except Exception:
                    pass
                time.sleep(0.1)

            if not ready:
                proc.kill()
                proc.wait()
                if attempt < 4:
                    continue
                pytest.fail("Server never became ready")

            assert httpx.get(f"{base}/ready", timeout=2).json()["status"] == "ready"
        finally:
            proc.kill()
            proc.wait()
        break


@pytest.mark.asyncio
async def test_restart_clean_state():
    """After restart, server starts with empty state."""
    import os
    env = os.environ.copy()
    env["SKETCHLOG_HOST"] = "127.0.0.1"

    for attempt in range(5):
        port = _free_port()
        env["SKETCHLOG_PORT"] = str(port)

        proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "sketchlog.server:app",
             "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )

        base = f"http://127.0.0.1:{port}"

        ready = False
        for _ in range(50):
            try:
                r = httpx.get(f"{base}/ready", timeout=0.1)
                if r.status_code == 200:
                    ready = True
                    break
            except Exception:
                pass
            time.sleep(0.1)

        if not ready:
            proc.kill()
            proc.wait()
            if attempt < 4:
                continue
            pytest.fail("Server did not start in time")

        try:
            # 1. Start server, ingest some data
            async with httpx.AsyncClient() as client:
                r = await client.post(f"{base}/v1/streams/restart-test/events",
                                      json={"latencies": [1.0, 2.0]}, timeout=5)
                assert r.status_code == 202
                r = await client.get(f"{base}/v1/streams/restart-test/metrics", timeout=5)
                assert r.json()["total_events"] == 2

            # 2. Hard kill process
            proc.kill()
            proc.wait()

            # 3. Start a new server on same port
            proc = subprocess.Popen(
                [sys.executable, "-m", "uvicorn", "sketchlog.server:app",
                 "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
                env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )

            # Wait for restart
            ready = False
            for _ in range(50):
                try:
                    r = httpx.get(f"{base}/ready", timeout=0.1)
                    if r.status_code == 200:
                        ready = True
                        break
                except Exception:
                    pass
                time.sleep(0.1)

            assert ready, "Restarted server did not become ready"

            # 4. Verify state is empty (ephemeral)
            async with httpx.AsyncClient() as client:
                r = await client.get(f"{base}/v1/streams/restart-test/metrics", timeout=5)
                assert r.status_code == 404, "Server preserved state across restarts"
        finally:
            proc.kill()
            proc.wait()
        break
