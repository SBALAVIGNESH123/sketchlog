"""Run a reproducible PostgreSQL durability proof against Docker Compose."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_COMPOSE_FILE = ROOT / "demo" / "postgres" / "compose.yml"
DEFAULT_SERVER_URL = "http://127.0.0.1:4180"
NAMESPACE = "proof"
STREAM_ID = "checkout-latency"
NODE_ID = "postgres-proof-node"


class ProofFailure(RuntimeError):
    """Raised when the PostgreSQL proof cannot verify an expected guarantee."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ProofFailure(message)


def run_command(args: list[str], *, capture: bool = False) -> str:
    completed = subprocess.run(
        args,
        cwd=ROOT,
        check=True,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.STDOUT if capture else None,
    )
    return completed.stdout or ""


def compose(compose_file: Path, *args: str, capture: bool = False) -> str:
    return run_command(
        ["docker", "compose", "-f", str(compose_file), *args],
        capture=capture,
    )


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


def wait_ready(server_url: str, timeout_seconds: int = 120) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | str | None = None
    while time.monotonic() < deadline:
        try:
            status, payload = request_json(server_url, "GET", "/ready")
            if status == 200 and payload == {"status": "ready"}:
                return
            last_error = f"unexpected readiness response {status}: {payload}"
        except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
            last_error = exc
        time.sleep(1)
    raise ProofFailure(f"SketchLog did not become ready: {last_error}")


def restart_server(compose_file: Path, server_url: str) -> None:
    compose(compose_file, "restart", "server")
    wait_ready(server_url)


def get_metrics(server_url: str) -> dict[str, Any]:
    status, payload = request_json(
        server_url,
        "GET",
        f"/v1/namespaces/{NAMESPACE}/streams/{STREAM_ID}/metrics",
    )
    require(status == 200, f"metrics request failed with {status}: {payload}")
    require(isinstance(payload, dict), "metrics response was not a JSON object")
    return payload


def assert_stream_missing(server_url: str) -> None:
    status, payload = request_json(
        server_url,
        "GET",
        f"/v1/namespaces/{NAMESPACE}/streams/{STREAM_ID}/metrics",
    )
    require(status == 404, f"deleted stream was still readable: {status} {payload}")


def query_postgres(compose_file: Path, sql: str) -> str:
    return compose(
        compose_file,
        "exec",
        "-T",
        "postgres",
        "psql",
        "-U",
        "sketchlog",
        "-d",
        "sketchlog",
        "-tAc",
        sql,
        capture=True,
    ).strip()


def run_proof(compose_file: Path, server_url: str) -> dict[str, Any]:
    wait_ready(server_url)

    latencies = [
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
    payload = {
        "latencies": latencies,
        "uniques": ["user-a", "user-b", "user-c", "user-d"],
        "events": {"ok": 120, "error": 3},
    }
    status, body = request_json(
        server_url,
        "POST",
        f"/v1/namespaces/{NAMESPACE}/streams/{STREAM_ID}/events",
        payload,
    )
    require(status == 202, f"ingest failed with {status}: {body}")

    before = get_metrics(server_url)
    require(before["total_events"] >= len(latencies), "latencies were not ingested")
    require(before["unique_count"] == 4, "unique count did not match fixture")
    require(before["p99"] >= before["p50"], "percentile ordering was invalid")

    restart_server(compose_file, server_url)
    after_restart = get_metrics(server_url)
    require(
        after_restart["total_events"] == before["total_events"],
        "restart changed total event count",
    )
    require(
        after_restart["unique_count"] == before["unique_count"],
        "restart changed unique count",
    )
    require(after_restart["p99"] >= after_restart["p50"], "restart broke percentiles")

    status, _ = request_json(
        server_url,
        "DELETE",
        f"/v1/namespaces/{NAMESPACE}/streams/{STREAM_ID}",
    )
    require(status == 204, f"delete failed with HTTP {status}")
    assert_stream_missing(server_url)

    tombstone_rows = query_postgres(
        compose_file,
        (
            "SELECT stream_key || '|' || version "
            "FROM sketchlog_mesh_tombstones "
            f"WHERE node_id = '{NODE_ID}' "
            "ORDER BY stream_key"
        ),
    )
    require(STREAM_ID in tombstone_rows, "durable mesh tombstone was not recorded")

    stream_rows = query_postgres(
        compose_file,
        (
            "SELECT count(*) FROM sketchlog_streams "
            f"WHERE namespace = '{NAMESPACE}' AND stream_id = '{STREAM_ID}'"
        ),
    )
    require(stream_rows == "0", f"deleted stream checkpoint remained: {stream_rows}")

    restart_server(compose_file, server_url)
    assert_stream_missing(server_url)

    return {
        "backend": "postgresql",
        "namespace": NAMESPACE,
        "stream_id": STREAM_ID,
        "events_before_restart": before["total_events"],
        "events_after_restart": after_restart["total_events"],
        "unique_count": after_restart["unique_count"],
        "p50": after_restart["p50"],
        "p99": after_restart["p99"],
        "tombstone_rows": tombstone_rows.splitlines(),
        "stream_rows_after_delete": int(stream_rows),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Prove SketchLog PostgreSQL durability with Docker Compose.")
    parser.add_argument(
        "--compose-file",
        type=Path,
        default=DEFAULT_COMPOSE_FILE,
        help="Compose file for the PostgreSQL proof stack.",
    )
    parser.add_argument(
        "--server-url",
        default=DEFAULT_SERVER_URL,
        help="SketchLog server URL exposed by the proof stack.",
    )
    parser.add_argument(
        "--start",
        action="store_true",
        help="Start the Compose stack before running the proof.",
    )
    parser.add_argument(
        "--stop",
        action="store_true",
        help="Stop and remove the Compose stack after the proof.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    compose_file = args.compose_file.resolve()
    if args.start:
        compose(compose_file, "up", "--build", "-d", "--wait")
    try:
        summary = run_proof(compose_file, args.server_url.rstrip("/"))
        print(json.dumps(summary, indent=2, sort_keys=True))
        print("PASS PostgreSQL durability proof")
        return 0
    finally:
        if args.stop:
            compose(compose_file, "down", "--volumes", "--remove-orphans")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as exc:
        message = f"FAIL PostgreSQL durability proof: {exc}"
        if exc.output:
            message += f"\nOutput:\n{exc.output}"
        print(message, file=sys.stderr)
        raise SystemExit(1)
    except ProofFailure as exc:
        print(f"FAIL PostgreSQL durability proof: {exc}", file=sys.stderr)
        raise SystemExit(1)
