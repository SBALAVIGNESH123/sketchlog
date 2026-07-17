"""Run an end-to-end SketchLog HTTP smoke against real OmniKV storage."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import textwrap
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, TextIO


ROOT = Path(__file__).resolve().parents[1]
PYTHON_DIR = ROOT / "python"
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

NAMESPACE = "proof"
STREAM_ID = "checkout-latency"
NODE_ID = "omnikv-e2e-node"
STREAM_KEY = json.dumps([NAMESPACE, STREAM_ID])

SERVER_BOOTSTRAP = """
import sys
import threading

import uvicorn

host = sys.argv[1]
port = int(sys.argv[2])
server = uvicorn.Server(
    uvicorn.Config(
        "sketchlog.server:app",
        host=host,
        port=port,
        log_level="info",
        access_log=False,
    )
)


def wait_for_stop() -> None:
    sys.stdin.readline()
    server.should_exit = True


threading.Thread(target=wait_for_stop, daemon=True).start()
server.run()
"""


class SmokeFailure(RuntimeError):
    """Raised when the OmniKV end-to-end smoke cannot prove an invariant."""


class ServerHandle:
    """Own a SketchLog server subprocess and its diagnostic log file."""

    def __init__(
        self,
        process: subprocess.Popen[str],
        log_file: TextIO,
        log_path: Path,
    ) -> None:
        self.process = process
        self._log_file = log_file
        self.log_path = log_path

    def stop(self, *, timeout_seconds: float = 30.0) -> None:
        if self.process.poll() is None:
            try:
                assert self.process.stdin is not None
                self.process.stdin.write("\n")
                self.process.stdin.flush()
            except (BrokenPipeError, OSError):
                pass
            try:
                self.process.wait(timeout=timeout_seconds)
            except subprocess.TimeoutExpired:
                self.process.terminate()
                try:
                    self.process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=10)
        self._log_file.close()

    def logs(self) -> str:
        try:
            self._log_file.flush()
        except ValueError:
            pass
        try:
            return self.log_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SmokeFailure(message)


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def request_json(
    server_url: str,
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
) -> tuple[int, Any]:
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        f"{server_url.rstrip('/')}{path}",
        data=body,
        headers={"Content-Type": "application/json"} if body is not None else {},
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            raw = response.read()
            if not raw:
                return response.status, None
            return response.status, json.loads(raw.decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        if not raw:
            return exc.code, None
        try:
            return exc.code, json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            return exc.code, raw.decode("utf-8", errors="replace")


def wait_ready(
    server_url: str,
    handle: ServerHandle,
    *,
    timeout_seconds: float = 45.0,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | str | None = None
    while time.monotonic() < deadline:
        if handle.process.poll() is not None:
            raise SmokeFailure(
                "SketchLog server exited before readiness while using "
                f"OmniKV storage.\nLogs:\n{handle.logs()}"
            )
        try:
            status, payload = request_json(server_url, "GET", "/ready")
            if status == 200 and payload == {"status": "ready"}:
                return
            last_error = f"unexpected readiness response {status}: {payload}"
        except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
            last_error = exc
        time.sleep(0.25)
    raise SmokeFailure(
        f"SketchLog did not become ready with OmniKV storage: {last_error}\n"
        f"Logs:\n{handle.logs()}"
    )


def start_server(
    data_dir: Path,
    module_name: str,
    embedded_namespace: str,
    port: int,
    log_dir: Path,
) -> ServerHandle:
    host = "127.0.0.1"
    server_url = f"http://{host}:{port}"
    log_path = log_dir / f"sketchlog-omnikv-e2e-{port}.log"
    log_file = log_path.open("w", encoding="utf-8")
    env = os.environ.copy()
    env.update(
        {
            "PYTHONPATH": (
                f"{PYTHON_DIR}{os.pathsep}{env['PYTHONPATH']}"
                if env.get("PYTHONPATH")
                else str(PYTHON_DIR)
            ),
            "PYTHONUNBUFFERED": "1",
            "SKETCHLOG_STORAGE_BACKEND": "omnikv",
            "SKETCHLOG_OMNIKV_DATA_DIR": str(data_dir),
            "SKETCHLOG_OMNIKV_MODULE": module_name,
            "SKETCHLOG_OMNIKV_NAMESPACE": embedded_namespace,
            "SKETCHLOG_NODE_ID": NODE_ID,
            "SKETCHLOG_ADVERTISED_ADDRESS": server_url,
            "SKETCHLOG_PEER_ALLOWLIST": server_url,
            "SKETCHLOG_CLUSTER_SECRET": "sketchlog-omnikv-e2e-secret",
            "SKETCHLOG_SYNC_INTERVAL": "0.25",
            "SKETCHLOG_MEMORY_THRESHOLD": "99",
        }
    )
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            textwrap.dedent(SERVER_BOOTSTRAP),
            host,
            str(port),
        ],
        cwd=ROOT,
        env=env,
        stdin=subprocess.PIPE,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        text=True,
    )
    handle = ServerHandle(process, log_file, log_path)
    try:
        wait_ready(server_url, handle)
    except BaseException:
        handle.stop()
        raise
    return handle


def get_metrics(server_url: str) -> dict[str, Any]:
    status, payload = request_json(
        server_url,
        "GET",
        f"/v1/namespaces/{NAMESPACE}/streams/{STREAM_ID}/metrics",
    )
    require(status == 200, f"metrics failed with HTTP {status}: {payload}")
    require(isinstance(payload, dict), "metrics response was not a JSON object")
    return payload


def assert_stream_missing(server_url: str) -> None:
    status, payload = request_json(
        server_url,
        "GET",
        f"/v1/namespaces/{NAMESPACE}/streams/{STREAM_ID}/metrics",
    )
    require(status == 404, f"deleted stream was readable: {status} {payload}")


async def load_tombstones(
    data_dir: Path,
    module_name: str,
    embedded_namespace: str,
) -> dict[str, float]:
    from sketchlog.storage import OmniKVEmbeddedStorage

    storage = OmniKVEmbeddedStorage(
        data_dir=data_dir,
        module_name=module_name,
        namespace=embedded_namespace,
    )
    await storage.initialize()
    try:
        return await storage.load_tombstones(NODE_ID)
    finally:
        await storage.close()


def run_smoke(
    data_dir: Path,
    module_name: str = "omnikv",
    embedded_namespace: str = "sketchlog",
) -> dict[str, Any]:
    data_dir.mkdir(parents=True, exist_ok=True)
    log_dir = data_dir.parent / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    port = find_free_port()
    server_url = f"http://127.0.0.1:{port}"

    handle = start_server(data_dir, module_name, embedded_namespace, port, log_dir)
    try:
        payload = {
            "latencies": [
                12.0,
                18.0,
                22.0,
                31.0,
                45.0,
                65.0,
                89.0,
                144.0,
                233.0,
                377.0,
                610.0,
                987.0,
            ],
            "uniques": ["user-a", "user-b", "user-c", "user-d"],
            "events": {"ok": 120, "error": 3},
        }
        status, body = request_json(
            server_url,
            "POST",
            f"/v1/namespaces/{NAMESPACE}/streams/{STREAM_ID}/events",
            payload,
        )
        require(status == 202, f"ingest failed with HTTP {status}: {body}")
        before = get_metrics(server_url)
        require(before["unique_count"] == 4, "unique count did not match fixture")
        require(before["p99"] >= before["p50"], "percentile ordering was invalid")
    finally:
        handle.stop()

    handle = start_server(data_dir, module_name, embedded_namespace, port, log_dir)
    try:
        after_restart = get_metrics(server_url)
        require(
            after_restart["total_events"] == before["total_events"],
            "restart changed total event count",
        )
        require(
            after_restart["unique_count"] == before["unique_count"],
            "restart changed unique count",
        )
        require(
            after_restart["p99"] >= after_restart["p50"],
            "restart broke percentile ordering",
        )

        status, body = request_json(
            server_url,
            "DELETE",
            f"/v1/namespaces/{NAMESPACE}/streams/{STREAM_ID}",
        )
        require(status == 204, f"delete failed with HTTP {status}: {body}")
        assert_stream_missing(server_url)
    finally:
        handle.stop()

    tombstones = asyncio.run(
        load_tombstones(data_dir, module_name, embedded_namespace)
    )
    require(
        STREAM_KEY in tombstones,
        f"durable mesh tombstone was not recorded: {tombstones}",
    )

    handle = start_server(data_dir, module_name, embedded_namespace, port, log_dir)
    try:
        assert_stream_missing(server_url)
    finally:
        handle.stop()

    return {
        "backend": "omnikv",
        "bridge_module": module_name,
        "embedded_namespace": embedded_namespace,
        "data_dir": str(data_dir),
        "namespace": NAMESPACE,
        "stream_id": STREAM_ID,
        "stream_key": STREAM_KEY,
        "events_before_restart": before["total_events"],
        "events_after_restart": after_restart["total_events"],
        "unique_count": after_restart["unique_count"],
        "p50": after_restart["p50"],
        "p99": after_restart["p99"],
        "tombstone_version_after_reopen": tombstones[STREAM_KEY],
        "deleted_stream_resurrected": False,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Prove SketchLog can run end-to-end over the real OmniKV embedded "
            "backend, survive restart, and persist mesh tombstones."
        )
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        help="OmniKV data directory. Defaults to a temporary directory.",
    )
    parser.add_argument(
        "--module",
        default="omnikv",
        help="Python module exposing the OmniKV bridge contract.",
    )
    parser.add_argument(
        "--namespace",
        default="sketchlog",
        help="Embedded OmniKV namespace for SketchLog keys.",
    )
    parser.add_argument(
        "--keep-data",
        action="store_true",
        help="Keep the temporary data and server logs after a successful smoke.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    created_temp_dir = False
    if args.data_dir is None:
        root = Path(tempfile.mkdtemp(prefix="sketchlog-omnikv-e2e-"))
        data_dir = root / "omnikv"
        created_temp_dir = True
    else:
        data_dir = args.data_dir
        root = data_dir.parent

    try:
        summary = run_smoke(data_dir, args.module, args.namespace)
        print(json.dumps(summary, indent=2, sort_keys=True))
        print("PASS SketchLog OmniKV E2E smoke")
        return 0
    finally:
        if created_temp_dir and not args.keep_data:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SmokeFailure as exc:
        print(f"FAIL SketchLog OmniKV E2E smoke: {exc}", file=sys.stderr)
        raise SystemExit(1)
