"""Run a unified SketchLog storage proof across supported backends."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import tempfile
import textwrap
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TextIO


ROOT = Path(__file__).resolve().parents[1]
PYTHON_DIR = ROOT / "python"
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

NAMESPACE = "proof"
STREAM_ID = "checkout-latency"
LATENCIES = [
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
]
UNIQUES = ["user-a", "user-b", "user-c", "user-d"]
EVENTS = {"ok": 120, "error": 3}
BACKENDS = ("memory", "postgres", "omnikv")

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

SERVER_ENV_KEYS = (
    "SKETCHLOG_STORAGE_BACKEND",
    "SKETCHLOG_DB_URI",
    "SKETCHLOG_OMNIKV_DATA_DIR",
    "SKETCHLOG_OMNIKV_NAMESPACE",
    "SKETCHLOG_OMNIKV_MODULE",
    "SKETCHLOG_NODE_ID",
    "SKETCHLOG_PEERS",
    "SKETCHLOG_ADVERTISED_ADDRESS",
    "SKETCHLOG_PEER_ALLOWLIST",
    "SKETCHLOG_CLUSTER_SECRET",
    "SKETCHLOG_AUTH_TOKEN",
    "SKETCHLOG_NAMESPACE_TOKENS",
)


class StorageProofFailure(RuntimeError):
    """Raised when a selected storage proof cannot verify its guarantees."""


class BackendUnavailable(StorageProofFailure):
    """Raised when an optional backend dependency is not installed or running."""


class ServerHandle:
    """Own a SketchLog proof server subprocess and diagnostic log file."""

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
        raise StorageProofFailure(message)


def concise_output(output: str, *, max_lines: int = 8) -> str:
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    for line in reversed(lines):
        lowered = line.lower()
        if "failed" in lowered or "error" in lowered or "cannot" in lowered:
            return line[:1000]
    return "\n".join(lines[-max_lines:])[:1000]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


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
            raise StorageProofFailure(
                "SketchLog server exited before readiness.\n"
                f"Logs:\n{handle.logs()}"
            )
        try:
            status, payload = request_json(server_url, "GET", "/ready")
            if status == 200 and payload == {"status": "ready"}:
                return
            last_error = f"unexpected readiness response {status}: {payload}"
        except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
            last_error = exc
        time.sleep(0.25)
    raise StorageProofFailure(
        f"SketchLog did not become ready: {last_error}\nLogs:\n{handle.logs()}"
    )


def server_env(overrides: dict[str, str]) -> dict[str, str]:
    env = os.environ.copy()
    for key in SERVER_ENV_KEYS:
        env.pop(key, None)
    env["PYTHONPATH"] = (
        f"{PYTHON_DIR}{os.pathsep}{env['PYTHONPATH']}"
        if env.get("PYTHONPATH")
        else str(PYTHON_DIR)
    )
    env["PYTHONUNBUFFERED"] = "1"
    env["SKETCHLOG_MEMORY_THRESHOLD"] = "99"
    env.update(overrides)
    return env


def start_server(
    env_overrides: dict[str, str],
    log_dir: Path,
    *,
    port: int | None = None,
) -> tuple[ServerHandle, str]:
    host = "127.0.0.1"
    selected_port = port or find_free_port()
    server_url = f"http://{host}:{selected_port}"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"sketchlog-storage-proof-{selected_port}.log"
    log_file = log_path.open("w", encoding="utf-8")
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            textwrap.dedent(SERVER_BOOTSTRAP),
            host,
            str(selected_port),
        ],
        cwd=ROOT,
        env=server_env(env_overrides),
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
    return handle, server_url


def ingest_fixture(server_url: str) -> dict[str, Any]:
    payload = {"latencies": LATENCIES, "uniques": UNIQUES, "events": EVENTS}
    status, body = request_json(
        server_url,
        "POST",
        f"/v1/namespaces/{NAMESPACE}/streams/{STREAM_ID}/events",
        payload,
    )
    require(status == 202, f"ingest failed with HTTP {status}: {body}")
    return payload


def get_metrics(server_url: str) -> dict[str, Any]:
    status, payload = request_json(
        server_url,
        "GET",
        f"/v1/namespaces/{NAMESPACE}/streams/{STREAM_ID}/metrics",
    )
    require(status == 200, f"metrics failed with HTTP {status}: {payload}")
    require(isinstance(payload, dict), "metrics response was not a JSON object")
    return payload


def assert_stream_missing(server_url: str, context: str) -> None:
    status, payload = request_json(
        server_url,
        "GET",
        f"/v1/namespaces/{NAMESPACE}/streams/{STREAM_ID}/metrics",
    )
    require(status == 404, f"{context}: expected 404, got {status}: {payload}")


def assert_metrics(metrics: dict[str, Any]) -> None:
    require(metrics["total_events"] >= len(LATENCIES), "latencies were not ingested")
    require(metrics["unique_count"] == len(UNIQUES), "unique count did not match fixture")
    require(metrics["p99"] >= metrics["p50"], "percentile ordering was invalid")


def run_memory_proof(work_dir: Path) -> dict[str, Any]:
    """Prove no-backend behavior: writes work, restarts are intentionally ephemeral."""
    started = time.perf_counter()
    timings: dict[str, float] = {}
    log_dir = work_dir / "logs"
    env = {"SKETCHLOG_STORAGE_BACKEND": "memory"}

    step = time.perf_counter()
    handle, server_url = start_server(env, log_dir)
    timings["initial_start_ms"] = round((time.perf_counter() - step) * 1000, 3)
    try:
        step = time.perf_counter()
        ingest_fixture(server_url)
        before = get_metrics(server_url)
        assert_metrics(before)
        timings["write_and_query_ms"] = round((time.perf_counter() - step) * 1000, 3)
    finally:
        handle.stop()

    step = time.perf_counter()
    handle, server_url = start_server(env, log_dir)
    timings["restart_ms"] = round((time.perf_counter() - step) * 1000, 3)
    try:
        assert_stream_missing(server_url, "memory backend after restart")
    finally:
        handle.stop()

    step = time.perf_counter()
    handle, server_url = start_server(env, log_dir)
    try:
        ingest_fixture(server_url)
        status, body = request_json(
            server_url,
            "DELETE",
            f"/v1/namespaces/{NAMESPACE}/streams/{STREAM_ID}",
        )
        require(status == 204, f"delete failed with HTTP {status}: {body}")
        assert_stream_missing(server_url, "memory backend after delete")
    finally:
        handle.stop()
    timings["delete_ms"] = round((time.perf_counter() - step) * 1000, 3)

    return {
        "backend": "memory",
        "status": "pass",
        "namespace": NAMESPACE,
        "stream_id": STREAM_ID,
        "events_before_restart": before["total_events"],
        "events_after_restart": None,
        "restart_behavior": "ephemeral_expected_missing",
        "unique_count": before["unique_count"],
        "p50": before["p50"],
        "p99": before["p99"],
        "delete_verified": True,
        "tombstone": {
            "supported": False,
            "durable": False,
            "reason": "in-memory mode has no durable tombstone store",
        },
        "duration_ms": round((time.perf_counter() - started) * 1000, 3),
        "timings_ms": timings,
    }


def run_omnikv_proof(
    data_dir: Path | None,
    module_name: str,
    embedded_namespace: str,
    keep_data: bool,
) -> dict[str, Any]:
    if importlib.util.find_spec(module_name) is None:
        raise BackendUnavailable(
            f"OmniKV bridge module '{module_name}' is not installed"
        )
    from omnikv_e2e_smoke import run_smoke

    started = time.perf_counter()
    created_temp_dir = False
    if data_dir is None:
        root = Path(tempfile.mkdtemp(prefix="sketchlog-storage-proof-omnikv-"))
        selected_data_dir = root / "omnikv"
        created_temp_dir = True
    else:
        selected_data_dir = data_dir
        root = data_dir.parent

    try:
        summary = run_smoke(selected_data_dir, module_name, embedded_namespace)
    except Exception as exc:
        raise StorageProofFailure(f"OmniKV proof failed: {exc}") from exc
    finally:
        if created_temp_dir and not keep_data:
            shutil.rmtree(root, ignore_errors=True)

    summary.update(
        {
            "backend": "omnikv",
            "status": "pass",
            "data_dir": Path(str(summary.get("data_dir", selected_data_dir))).name,
            "restart_behavior": "durable_state_recovered",
            "delete_verified": True,
            "tombstone": {
                "supported": True,
                "durable": True,
                "version": summary["tombstone_version_after_reopen"],
            },
            "duration_ms": round((time.perf_counter() - started) * 1000, 3),
            "timings_ms": {
                "total_ms": round((time.perf_counter() - started) * 1000, 3)
            },
        }
    )
    return summary


def run_postgres_proof(
    compose_file: Path,
    server_url: str,
    *,
    start: bool,
    stop: bool,
) -> dict[str, Any]:
    if shutil.which("docker") is None:
        raise BackendUnavailable("Docker CLI is not installed or not on PATH")
    try:
        subprocess.run(
            ["docker", "info"],
            cwd=ROOT,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=15,
        )
    except subprocess.CalledProcessError as exc:
        output = concise_output(exc.stdout or "")
        detail = f": {output}" if output else ""
        raise BackendUnavailable(f"Docker daemon is not available{detail}") from exc
    except subprocess.TimeoutExpired as exc:
        output = concise_output(str(exc))
        detail = f": {output}" if output else ""
        raise BackendUnavailable(f"Docker daemon probe timed out{detail}") from exc
    from postgres_durability_proof import compose, run_proof

    started = time.perf_counter()
    try:
        if start:
            compose(compose_file, "up", "--build", "-d", "--wait")
        summary = run_proof(compose_file, server_url.rstrip("/"))
    except subprocess.CalledProcessError:
        raise
    except Exception as exc:
        raise StorageProofFailure(f"PostgreSQL proof failed: {exc}") from exc
    finally:
        if stop:
            compose(compose_file, "down", "--volumes", "--remove-orphans")

    summary.update(
        {
            "backend": "postgres",
            "status": "pass",
            "restart_behavior": "durable_state_recovered",
            "delete_verified": True,
            "tombstone": {
                "supported": True,
                "durable": True,
                "rows": summary.get("tombstone_rows", []),
            },
            "duration_ms": round((time.perf_counter() - started) * 1000, 3),
            "timings_ms": {
                "total_ms": round((time.perf_counter() - started) * 1000, 3)
            },
        }
    )
    return summary


def git_commit() -> str | None:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        return completed.stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def environment_metadata() -> dict[str, Any]:
    return {
        "generated_at": now_iso(),
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "machine": platform.machine(),
        "repo": ROOT.name,
        "git_commit": git_commit(),
    }


def normalize_backends(requested: list[str] | None) -> list[str]:
    if not requested:
        return ["memory"]
    expanded: list[str] = []
    for backend in requested:
        if backend == "all":
            expanded.extend(BACKENDS)
        else:
            expanded.append(backend)
    return list(dict.fromkeys(expanded))


def run_selected_backends(args: argparse.Namespace) -> dict[str, Any]:
    selected = normalize_backends(args.backend)
    proof_root = args.work_dir or Path(tempfile.mkdtemp(prefix="sketchlog-storage-proof-"))
    proof_root.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []

    for backend in selected:
        try:
            if backend == "memory":
                results.append(run_memory_proof(proof_root / "memory"))
            elif backend == "omnikv":
                results.append(
                    run_omnikv_proof(
                        args.omnikv_data_dir,
                        args.omnikv_module,
                        args.omnikv_namespace,
                        args.keep_data,
                    )
                )
            elif backend == "postgres":
                results.append(
                    run_postgres_proof(
                        args.postgres_compose_file.resolve(),
                        args.postgres_server_url,
                        start=args.postgres_start,
                        stop=args.postgres_stop,
                    )
                )
            else:  # pragma: no cover - argparse choices prevent this branch.
                raise StorageProofFailure(f"Unknown backend: {backend}")
        except BackendUnavailable as exc:
            if args.allow_missing_optional:
                results.append(
                    {
                        "backend": backend,
                        "status": "skipped",
                        "reason": str(exc),
                        "duration_ms": 0,
                    }
                )
                continue
            results.append(
                {
                    "backend": backend,
                    "status": "failed",
                    "error": str(exc),
                    "duration_ms": 0,
                }
            )
            if not args.continue_on_error:
                break
        except Exception as exc:
            error = str(exc)
            if isinstance(exc, subprocess.CalledProcessError):
                output = getattr(exc, "output", None) or getattr(exc, "stdout", None)
                if output:
                    error = f"{error}\nOutput:\n{concise_output(str(output))}"
            results.append(
                {
                    "backend": backend,
                    "status": "failed",
                    "error": error,
                    "duration_ms": 0,
                }
            )
            if not args.continue_on_error:
                break

    failed = [result for result in results if result["status"] == "failed"]
    passed = [result for result in results if result["status"] == "pass"]
    skipped = [result for result in results if result["status"] == "skipped"]
    status = "pass" if not failed else "failed"
    return {
        "runner": "sketchlog-storage-proof",
        "schema": 1,
        "status": status,
        "environment": environment_metadata(),
        "scenario": {
            "name": "checkout-latency-restart-delete",
            "namespace": NAMESPACE,
            "stream_id": STREAM_ID,
            "latency_samples": len(LATENCIES),
            "unique_samples": len(UNIQUES),
            "event_counts": EVENTS,
        },
        "proof_root": proof_root.name,
        "selected_backends": selected,
        "passed": len(passed),
        "failed": len(failed),
        "skipped": len(skipped),
        "results": results,
    }


def print_human_summary(report: dict[str, Any]) -> None:
    print("SketchLog storage proof")
    print(f"Scenario: {report['scenario']['name']}")
    print(f"Commit: {report['environment'].get('git_commit') or 'unknown'}")
    print("")
    for result in report["results"]:
        backend = result["backend"]
        status = result["status"].upper()
        duration = result.get("duration_ms", 0)
        if result["status"] == "pass":
            restart = result.get("restart_behavior", "unknown")
            tombstone = result.get("tombstone", {})
            tombstone_label = (
                "durable"
                if tombstone.get("durable")
                else "not durable / not supported"
            )
            print(
                f"- {backend}: {status} in {duration} ms "
                f"(restart={restart}, tombstone={tombstone_label})"
            )
        elif result["status"] == "skipped":
            print(f"- {backend}: {status} ({result['reason']})")
        else:
            print(f"- {backend}: {status} ({result['error']})")
    print("")
    print(json.dumps(report, indent=2, sort_keys=True))
    print("")
    if report["status"] == "pass":
        print("PASS SketchLog storage proof")
    else:
        print("FAIL SketchLog storage proof")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run one proof command for SketchLog storage behavior across "
            "memory, PostgreSQL, and OmniKV backends."
        )
    )
    parser.add_argument(
        "--backend",
        choices=["memory", "postgres", "omnikv", "all"],
        action="append",
        help="Backend to prove. Repeatable. Defaults to memory.",
    )
    parser.add_argument(
        "--work-dir",
        type=Path,
        help="Directory for proof data and logs. Defaults to a temporary directory.",
    )
    parser.add_argument(
        "--json-output",
        type=Path,
        help="Optional file path to write the JSON proof report.",
    )
    parser.add_argument(
        "--json-only",
        action="store_true",
        help="Print only the JSON report.",
    )
    parser.add_argument(
        "--continue-on-error",
        action="store_true",
        help="Run remaining selected backends after a failure.",
    )
    parser.add_argument(
        "--allow-missing-optional",
        action="store_true",
        help="Record missing PostgreSQL/OmniKV dependencies as skipped.",
    )
    parser.add_argument(
        "--keep-data",
        action="store_true",
        help="Keep temporary backend data directories after successful proofs.",
    )
    parser.add_argument(
        "--postgres-compose-file",
        type=Path,
        default=ROOT / "demo" / "postgres" / "compose.yml",
        help="Docker Compose file for the PostgreSQL proof stack.",
    )
    parser.add_argument(
        "--postgres-server-url",
        default="http://127.0.0.1:4180",
        help="SketchLog server URL for the PostgreSQL proof stack.",
    )
    parser.add_argument(
        "--postgres-start",
        action="store_true",
        help="Start the PostgreSQL Compose stack before proving it.",
    )
    parser.add_argument(
        "--postgres-stop",
        action="store_true",
        help="Stop and remove the PostgreSQL Compose stack after the proof.",
    )
    parser.add_argument(
        "--omnikv-data-dir",
        type=Path,
        help="OmniKV data directory. Defaults to a temporary directory.",
    )
    parser.add_argument(
        "--omnikv-module",
        default="omnikv",
        help="Python module exposing the OmniKV bridge contract.",
    )
    parser.add_argument(
        "--omnikv-namespace",
        default="sketchlog",
        help="Embedded OmniKV namespace for SketchLog keys.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = run_selected_backends(args)
    if args.json_output:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(
            json.dumps(report, indent=2, sort_keys=True),
            encoding="utf-8",
        )
    if args.json_only:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print_human_summary(report)
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
