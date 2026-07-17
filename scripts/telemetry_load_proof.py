"""Run a deterministic real-ish telemetry load proof across storage backends."""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import platform
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = ROOT / "scripts"
PYTHON_DIR = ROOT / "python"
for candidate in (SCRIPTS_DIR, PYTHON_DIR):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

import storage_proof


NAMESPACE = "proof"
STREAM_ID = "checkout-realistic-load"
DEFAULT_EVENT_COUNT = 2_500
DEFAULT_BATCH_SIZE = 250
DEFAULT_SEED = 20260417
BACKENDS = ("memory", "postgres", "omnikv")

BASE_TIME = datetime(2026, 1, 15, 9, 0, tzinfo=timezone.utc)
REGIONS = ("us-east-1", "us-west-2", "eu-west-1", "ap-south-1")
TENANTS = ("retail", "enterprise", "marketplace", "internal")
SERVICE_ROUTES: dict[str, tuple[tuple[str, str, float], ...]] = {
    "api-gateway": (
        ("GET /v1/catalog/search", "GET", 42.0),
        ("GET /v1/products/{id}", "GET", 38.0),
        ("POST /v1/session/refresh", "POST", 51.0),
    ),
    "checkout": (
        ("POST /v1/cart/checkout", "POST", 96.0),
        ("POST /v1/orders", "POST", 124.0),
        ("GET /v1/orders/{id}", "GET", 58.0),
    ),
    "payments": (
        ("POST /v1/payments/authorize", "POST", 138.0),
        ("POST /v1/payments/capture", "POST", 166.0),
        ("POST /v1/refunds", "POST", 149.0),
    ),
    "inventory": (
        ("GET /v1/stock/{sku}", "GET", 31.0),
        ("POST /v1/reservations", "POST", 74.0),
        ("DELETE /v1/reservations/{id}", "DELETE", 68.0),
    ),
    "notifications": (
        ("POST /v1/email/send", "POST", 82.0),
        ("POST /v1/sms/send", "POST", 89.0),
        ("POST /v1/webhooks/dispatch", "POST", 113.0),
    ),
}


StorageProofFailure = storage_proof.StorageProofFailure
BackendUnavailable = storage_proof.BackendUnavailable


@dataclass(frozen=True)
class TelemetryEvent:
    """A compact, deterministic request telemetry row."""

    timestamp: str
    service: str
    route: str
    method: str
    status: int
    user_id: str
    latency_ms: float
    region: str
    tenant: str
    labels: dict[str, str]

    def to_json(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "service": self.service,
            "route": self.route,
            "method": self.method,
            "status": self.status,
            "user_id": self.user_id,
            "latency_ms": self.latency_ms,
            "region": self.region,
            "tenant": self.tenant,
            "labels": dict(sorted(self.labels.items())),
        }


def require(condition: bool, message: str) -> None:
    if not condition:
        raise StorageProofFailure(message)


def _module_is_available(module_name: str) -> bool:
    try:
        return importlib.util.find_spec(module_name) is not None
    except ValueError:
        return module_name in sys.modules


def _sanitize_event_part(value: str) -> str:
    normalized = value.lower().replace("{", "").replace("}", "")
    normalized = re.sub(r"[^a-z0-9]+", "_", normalized).strip("_")
    return normalized or "unknown"


def event_counter_name(kind: str, value: str | int) -> str:
    return f"{kind}.{_sanitize_event_part(str(value))}"


def generate_telemetry_events(
    count: int = DEFAULT_EVENT_COUNT,
    *,
    seed: int = DEFAULT_SEED,
) -> list[TelemetryEvent]:
    """Generate deterministic API telemetry with realistic skew and tails."""
    if count < 1:
        raise ValueError("count must be >= 1")

    rng = random.Random(seed)
    services = tuple(SERVICE_ROUTES)
    events: list[TelemetryEvent] = []
    for index in range(count):
        service = rng.choices(services, weights=(24, 28, 17, 19, 12), k=1)[0]
        route, method, base_latency = rng.choice(SERVICE_ROUTES[service])
        region = rng.choices(REGIONS, weights=(38, 24, 21, 17), k=1)[0]
        tenant = rng.choices(TENANTS, weights=(46, 26, 20, 8), k=1)[0]

        if rng.random() < 0.22:
            user_number = rng.randint(1, 75)
        else:
            user_number = rng.randint(76, 900)
        user_id = f"user-{user_number:04d}"

        status_roll = rng.random()
        if status_roll < 0.915:
            status = rng.choices((200, 201, 204), weights=(82, 13, 5), k=1)[0]
        elif status_roll < 0.976:
            status = rng.choices((400, 404, 409, 429), weights=(35, 28, 17, 20), k=1)[0]
        else:
            status = rng.choices((500, 502, 503), weights=(39, 26, 35), k=1)[0]

        latency = base_latency * rng.lognormvariate(0.0, 0.37)
        if tenant == "enterprise":
            latency *= 1.10
        if region == "ap-south-1":
            latency *= 1.16
        if status >= 500:
            latency *= rng.uniform(1.9, 4.2)
        elif status >= 400:
            latency *= rng.uniform(1.15, 2.0)
        if index % 333 == 0:
            latency *= rng.uniform(3.0, 5.5)
            status = 503

        timestamp = BASE_TIME + timedelta(milliseconds=index * 137)
        labels = {
            "env": "production",
            "region": region,
            "tenant": tenant,
            "service": service,
            "route": route,
            "method": method,
            "status_class": f"{status // 100}xx",
        }
        events.append(
            TelemetryEvent(
                timestamp=timestamp.isoformat().replace("+00:00", "Z"),
                service=service,
                route=route,
                method=method,
                status=status,
                user_id=user_id,
                latency_ms=round(max(latency, 1.0), 3),
                region=region,
                tenant=tenant,
                labels=labels,
            )
        )
    return events


def write_jsonl_fixture(events: Iterable[TelemetryEvent], path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    bytes_written = 0
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for event in events:
            line = json.dumps(event.to_json(), separators=(",", ":"), sort_keys=True)
            bytes_written += len(line.encode("utf-8")) + 1
            handle.write(line)
            handle.write("\n")
    return bytes_written


def _jsonl_size(events: Iterable[TelemetryEvent]) -> int:
    total = 0
    for event in events:
        line = json.dumps(event.to_json(), separators=(",", ":"), sort_keys=True)
        total += len(line.encode("utf-8")) + 1
    return total


def _nearest_rank(values: list[float], quantile: float) -> float:
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(quantile * len(ordered)) - 1))
    return ordered[index]


def build_event_counters(events: Iterable[TelemetryEvent]) -> Counter[str]:
    counters: Counter[str] = Counter()
    for event in events:
        counters[event_counter_name("status", event.status)] += 1
        counters[event_counter_name("service", event.service)] += 1
        counters[event_counter_name("route", event.route)] += 1
        counters[event_counter_name("region", event.region)] += 1
        counters[event_counter_name("tenant", event.tenant)] += 1
    return counters


def fixture_summary(
    events: list[TelemetryEvent],
    *,
    seed: int = DEFAULT_SEED,
    top_n: int = 8,
) -> dict[str, Any]:
    latencies = [event.latency_ms for event in events]
    unique_users = {event.user_id for event in events}
    event_counters = build_event_counters(events)
    raw_jsonl_bytes = _jsonl_size(events)
    return {
        "schema": 1,
        "name": "realistic_checkout_api_telemetry",
        "seed": seed,
        "event_count": len(events),
        "timestamp_start": events[0].timestamp,
        "timestamp_end": events[-1].timestamp,
        "services": sorted({event.service for event in events}),
        "regions": sorted({event.region for event in events}),
        "tenants": sorted({event.tenant for event in events}),
        "label_keys": sorted(events[0].labels),
        "raw_jsonl_bytes": raw_jsonl_bytes,
        "exact": {
            "p50_ms": _nearest_rank(latencies, 0.50),
            "p95_ms": _nearest_rank(latencies, 0.95),
            "p99_ms": _nearest_rank(latencies, 0.99),
            "unique_users": len(unique_users),
            "sketch_total_events": len(events) + sum(event_counters.values()),
        },
        "top_items": [
            {"name": name, "count": count}
            for name, count in event_counters.most_common(top_n)
        ],
    }


def iter_batches(
    events: list[TelemetryEvent],
    *,
    batch_size: int,
) -> Iterable[dict[str, Any]]:
    if batch_size < 1:
        raise ValueError("batch_size must be >= 1")
    for offset in range(0, len(events), batch_size):
        chunk = events[offset:offset + batch_size]
        counters = build_event_counters(chunk)
        yield {
            "latencies": [event.latency_ms for event in chunk],
            "uniques": [event.user_id for event in chunk],
            "events": dict(sorted(counters.items())),
        }


def request_json(
    server_url: str,
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
) -> tuple[int, Any]:
    return storage_proof.request_json(server_url, method, path, payload)


def ingest_fixture(
    server_url: str,
    events: list[TelemetryEvent],
    *,
    batch_size: int,
) -> dict[str, Any]:
    started = time.perf_counter()
    batches = 0
    for payload in iter_batches(events, batch_size=batch_size):
        status, body = request_json(
            server_url,
            "POST",
            f"/v1/namespaces/{NAMESPACE}/streams/{STREAM_ID}/events",
            payload,
        )
        require(status == 202, f"ingest failed with HTTP {status}: {body}")
        batches += 1
    elapsed = time.perf_counter() - started
    return {
        "batches": batches,
        "raw_events": len(events),
        "batch_size": batch_size,
        "duration_ms": round(elapsed * 1000, 3),
        "raw_events_per_second": round(len(events) / elapsed, 3) if elapsed else 0,
    }


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


def query_aggregates(server_url: str) -> dict[str, float | int]:
    query = (
        'SELECT p50(latency) AS p50_ms, p95(latency) AS p95_ms, '
        'p99(latency) AS p99_ms, unique_count(user_id) AS unique_users, '
        f'event_count(*) AS sketch_total_events FROM "{NAMESPACE}/{STREAM_ID}"'
    )
    status, payload = request_json(server_url, "POST", "/v1/query", {"query": query})
    require(status == 200, f"SQL aggregate query failed with HTTP {status}: {payload}")
    require(isinstance(payload, dict), "SQL aggregate response was not an object")
    results = payload.get("results")
    require(isinstance(results, list) and results, "SQL aggregate query returned no rows")
    row = {
        str(item["metric"]): item["value"]
        for item in results
        if isinstance(item, dict) and "metric" in item and "value" in item
    }
    required = {"p50_ms", "p95_ms", "p99_ms", "unique_users", "sketch_total_events"}
    require(required.issubset(row), f"SQL aggregate query missed fields: {row}")
    return row


def query_event_count(server_url: str, event_name: str) -> int:
    query = (
        f"SELECT event_count(event, '{event_name}') AS item_count "
        f'FROM "{NAMESPACE}/{STREAM_ID}"'
    )
    status, payload = request_json(server_url, "POST", "/v1/query", {"query": query})
    require(status == 200, f"SQL event_count query failed with HTTP {status}: {payload}")
    results = payload.get("results") if isinstance(payload, dict) else None
    require(isinstance(results, list) and results, "SQL event_count returned no rows")
    row = results[0]
    require(isinstance(row, dict), "SQL event_count row was not an object")
    value = row.get("value")
    require(isinstance(value, int), f"SQL event_count returned non-int value: {value}")
    return value


def _relative_error(observed: float, expected: float) -> float:
    denominator = max(abs(expected), 1.0)
    return abs(observed - expected) / denominator


def validate_observation(
    *,
    metrics: dict[str, Any],
    sql: dict[str, float | int],
    top_items: list[dict[str, Any]],
    fixture: dict[str, Any],
) -> dict[str, Any]:
    exact = fixture["exact"]
    require(
        metrics["total_events"] == exact["sketch_total_events"],
        (
            "Sketch total event count mismatch: "
            f"{metrics['total_events']} != {exact['sketch_total_events']}"
        ),
    )
    require(
        int(sql["sketch_total_events"]) == exact["sketch_total_events"],
        "SQL total event count did not match the fixture model",
    )
    require(sql["p50_ms"] <= sql["p95_ms"] <= sql["p99_ms"], "SQL percentile order failed")
    require(metrics["p50"] <= sql["p95_ms"] <= metrics["p99"], "metrics/SQL percentile order failed")

    percentile_errors = {
        "p50": _relative_error(float(sql["p50_ms"]), float(exact["p50_ms"])),
        "p95": _relative_error(float(sql["p95_ms"]), float(exact["p95_ms"])),
        "p99": _relative_error(float(sql["p99_ms"]), float(exact["p99_ms"])),
    }
    for name, error in percentile_errors.items():
        require(error <= 0.15, f"{name} relative error too high: {error:.3f}")

    unique_error = _relative_error(
        float(metrics["unique_count"]),
        float(exact["unique_users"]),
    )
    require(unique_error <= 0.10, f"unique count relative error too high: {unique_error:.3f}")
    for item in top_items:
        require(
            item["estimated_count"] >= item["actual_count"],
            f"Count-Min Sketch undercounted {item['name']}",
        )

    raw_to_compact_ratio = fixture["raw_jsonl_bytes"] / metrics["memory_footprint_bytes"]
    return {
        "percentile_relative_error": {
            key: round(value, 6) for key, value in percentile_errors.items()
        },
        "unique_relative_error": round(unique_error, 6),
        "raw_to_compact_ratio": round(raw_to_compact_ratio, 3),
        "compact_smaller_than_raw": metrics["memory_footprint_bytes"] < fixture["raw_jsonl_bytes"],
    }


def observe_server(server_url: str, fixture: dict[str, Any]) -> dict[str, Any]:
    metrics = get_metrics(server_url)
    sql = query_aggregates(server_url)
    top_items = []
    for item in fixture["top_items"]:
        estimated = query_event_count(server_url, item["name"])
        top_items.append(
            {
                "name": item["name"],
                "actual_count": item["count"],
                "estimated_count": estimated,
            }
        )
    validation = validate_observation(
        metrics=metrics,
        sql=sql,
        top_items=top_items,
        fixture=fixture,
    )
    return {
        "metrics": metrics,
        "sql": sql,
        "top_items": top_items,
        "validation": validation,
        "storage_model": {
            "raw_jsonl_bytes": fixture["raw_jsonl_bytes"],
            "compact_state_bytes": metrics["memory_footprint_bytes"],
            "raw_to_compact_ratio": validation["raw_to_compact_ratio"],
            "compact_smaller_than_raw": validation["compact_smaller_than_raw"],
        },
    }


def prove_running_server(
    server_url: str,
    events: list[TelemetryEvent],
    *,
    batch_size: int,
    seed: int = DEFAULT_SEED,
    restart: Callable[[], None] | None,
) -> dict[str, Any]:
    fixture = fixture_summary(events, seed=seed)
    delete_status, delete_body = request_json(
        server_url,
        "DELETE",
        f"/v1/namespaces/{NAMESPACE}/streams/{STREAM_ID}",
    )
    require(
        delete_status in {204, 404},
        f"preflight stream cleanup failed with HTTP {delete_status}: {delete_body}",
    )
    ingest = ingest_fixture(server_url, events, batch_size=batch_size)
    before_restart = observe_server(server_url, fixture)
    after_restart = None
    if restart is not None:
        restart()
        after_restart = observe_server(server_url, fixture)
        require(
            after_restart["metrics"]["total_events"]
            == before_restart["metrics"]["total_events"],
            "restart changed total event count",
        )
        require(
            after_restart["metrics"]["unique_count"]
            == before_restart["metrics"]["unique_count"],
            "restart changed unique count",
        )
        require(
            after_restart["sql"]["p99_ms"] >= after_restart["sql"]["p50_ms"],
            "restart broke percentile ordering",
        )
    return {
        "namespace": NAMESPACE,
        "stream_id": STREAM_ID,
        "fixture": fixture,
        "ingest": ingest,
        "before_restart": before_restart,
        "after_restart": after_restart,
    }


def run_memory_load_proof(
    work_dir: Path,
    events: list[TelemetryEvent],
    *,
    batch_size: int,
    seed: int = DEFAULT_SEED,
) -> dict[str, Any]:
    started = time.perf_counter()
    log_dir = work_dir / "logs"
    env = {"SKETCHLOG_STORAGE_BACKEND": "memory"}
    handle, server_url = storage_proof.start_server(env, log_dir)
    try:
        proof = prove_running_server(
            server_url,
            events,
            batch_size=batch_size,
            seed=seed,
            restart=None,
        )
    finally:
        handle.stop()

    handle, server_url = storage_proof.start_server(env, log_dir)
    try:
        assert_stream_missing(server_url, "memory backend after restart")
    finally:
        handle.stop()

    proof.update(
        {
            "backend": "memory",
            "status": "pass",
            "restart_behavior": "ephemeral_expected_missing",
            "duration_ms": round((time.perf_counter() - started) * 1000, 3),
        }
    )
    return proof


def run_omnikv_load_proof(
    data_dir: Path | None,
    module_name: str,
    embedded_namespace: str,
    keep_data: bool,
    events: list[TelemetryEvent],
    *,
    batch_size: int,
    seed: int = DEFAULT_SEED,
) -> dict[str, Any]:
    if not _module_is_available(module_name):
        raise BackendUnavailable(f"OmniKV bridge module '{module_name}' is not installed")

    started = time.perf_counter()
    created_temp_dir = False
    if data_dir is None:
        root = Path(tempfile.mkdtemp(prefix="sketchlog-telemetry-load-omnikv-"))
        selected_data_dir = root / "omnikv"
        created_temp_dir = True
    else:
        root = data_dir.parent
        selected_data_dir = data_dir
    selected_data_dir.mkdir(parents=True, exist_ok=True)
    log_dir = root / "logs"
    env = {
        "SKETCHLOG_STORAGE_BACKEND": "omnikv",
        "SKETCHLOG_OMNIKV_DATA_DIR": str(selected_data_dir),
        "SKETCHLOG_OMNIKV_MODULE": module_name,
        "SKETCHLOG_OMNIKV_NAMESPACE": embedded_namespace,
    }

    try:
        handle, server_url = storage_proof.start_server(env, log_dir)
        try:
            proof = prove_running_server(
                server_url,
                events,
                batch_size=batch_size,
                seed=seed,
                restart=None,
            )
        finally:
            handle.stop()

        handle, server_url = storage_proof.start_server(env, log_dir)
        try:
            after_restart = observe_server(server_url, proof["fixture"])
            proof["after_restart"] = after_restart
            require(
                after_restart["metrics"]["total_events"]
                == proof["before_restart"]["metrics"]["total_events"],
                "OmniKV restart changed total event count",
            )
            require(
                after_restart["metrics"]["unique_count"]
                == proof["before_restart"]["metrics"]["unique_count"],
                "OmniKV restart changed unique count",
            )
        finally:
            handle.stop()
    finally:
        if created_temp_dir and not keep_data:
            shutil.rmtree(root, ignore_errors=True)

    proof.update(
        {
            "backend": "omnikv",
            "status": "pass",
            "bridge_module": module_name,
            "embedded_namespace": embedded_namespace,
            "data_dir": selected_data_dir.name,
            "restart_behavior": "durable_state_recovered",
            "duration_ms": round((time.perf_counter() - started) * 1000, 3),
        }
    )
    return proof


def _probe_docker() -> None:
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
        output = storage_proof.concise_output(exc.stdout or "")
        detail = f": {output}" if output else ""
        raise BackendUnavailable(f"Docker daemon is not available{detail}") from exc
    except subprocess.TimeoutExpired as exc:
        output = storage_proof.concise_output(str(exc))
        detail = f": {output}" if output else ""
        raise BackendUnavailable(f"Docker daemon probe timed out{detail}") from exc


def run_postgres_load_proof(
    compose_file: Path,
    server_url: str,
    *,
    start: bool,
    stop: bool,
    events: list[TelemetryEvent],
    batch_size: int,
    seed: int = DEFAULT_SEED,
) -> dict[str, Any]:
    _probe_docker()
    from postgres_durability_proof import compose, restart_server, wait_ready

    started = time.perf_counter()
    try:
        if start:
            compose(compose_file, "up", "--build", "-d", "--wait")
        wait_ready(server_url)
        proof = prove_running_server(
            server_url,
            events,
            batch_size=batch_size,
            seed=seed,
            restart=lambda: restart_server(compose_file, server_url),
        )
    except subprocess.CalledProcessError:
        raise
    except Exception as exc:
        raise StorageProofFailure(f"PostgreSQL telemetry load proof failed: {exc}") from exc
    finally:
        if stop:
            compose(compose_file, "down", "--volumes", "--remove-orphans")

    proof.update(
        {
            "backend": "postgres",
            "status": "pass",
            "compose_file": compose_file.name,
            "restart_behavior": "durable_state_recovered",
            "duration_ms": round((time.perf_counter() - started) * 1000, 3),
        }
    )
    return proof


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
        "generated_at": storage_proof.now_iso(),
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
    proof_root = args.work_dir or Path(tempfile.mkdtemp(prefix="sketchlog-telemetry-load-"))
    proof_root.mkdir(parents=True, exist_ok=True)
    events = generate_telemetry_events(args.events, seed=args.seed)
    fixture = fixture_summary(events, seed=args.seed)
    if args.fixture_output:
        fixture["fixture_output"] = args.fixture_output.name
        fixture["fixture_output_bytes"] = write_jsonl_fixture(events, args.fixture_output)

    results: list[dict[str, Any]] = []
    for backend in selected:
        try:
            if backend == "memory":
                results.append(
                    run_memory_load_proof(
                        proof_root / "memory",
                        events,
                        batch_size=args.batch_size,
                        seed=args.seed,
                    )
                )
            elif backend == "omnikv":
                results.append(
                    run_omnikv_load_proof(
                        args.omnikv_data_dir,
                        args.omnikv_module,
                        args.omnikv_namespace,
                        args.keep_data,
                        events,
                        batch_size=args.batch_size,
                        seed=args.seed,
                    )
                )
            elif backend == "postgres":
                results.append(
                    run_postgres_load_proof(
                        args.postgres_compose_file.resolve(),
                        args.postgres_server_url.rstrip("/"),
                        start=args.postgres_start,
                        stop=args.postgres_stop,
                        events=events,
                        batch_size=args.batch_size,
                        seed=args.seed,
                    )
                )
            else:  # pragma: no cover - argparse choices prevent this.
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
                    error = f"{error}\nOutput:\n{storage_proof.concise_output(str(output))}"
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
    return {
        "runner": "sketchlog-telemetry-load-proof",
        "schema": 1,
        "status": "pass" if not failed else "failed",
        "environment": environment_metadata(),
        "fixture": fixture,
        "proof_root": proof_root.name,
        "selected_backends": selected,
        "passed": len(passed),
        "failed": len(failed),
        "skipped": len(skipped),
        "results": results,
    }


def print_human_summary(report: dict[str, Any]) -> None:
    fixture = report["fixture"]
    exact = fixture["exact"]
    print("SketchLog telemetry load proof")
    print(
        f"Fixture: {fixture['event_count']} realistic API events, "
        f"{fixture['raw_jsonl_bytes']} raw JSONL bytes"
    )
    print(
        "Exact: "
        f"p50={exact['p50_ms']} ms, p95={exact['p95_ms']} ms, "
        f"p99={exact['p99_ms']} ms, users={exact['unique_users']}"
    )
    print(f"Commit: {report['environment'].get('git_commit') or 'unknown'}")
    print("")
    for result in report["results"]:
        backend = result["backend"]
        status = result["status"].upper()
        duration = result.get("duration_ms", 0)
        if result["status"] == "pass":
            observation = result.get("after_restart") or result["before_restart"]
            metrics = observation["metrics"]
            sql = observation["sql"]
            storage_model = observation["storage_model"]
            print(
                f"- {backend}: {status} in {duration} ms "
                f"(events={fixture['event_count']}, p95={sql['p95_ms']:.3f} ms, "
                f"users={metrics['unique_count']}, "
                f"raw/compact={storage_model['raw_to_compact_ratio']}x, "
                f"restart={result['restart_behavior']})"
            )
        elif result["status"] == "skipped":
            print(f"- {backend}: {status} ({result['reason']})")
        else:
            print(f"- {backend}: {status} ({result['error']})")
    print("")
    print(json.dumps(report, indent=2, sort_keys=True))
    print("")
    if report["status"] == "pass":
        print("PASS SketchLog telemetry load proof")
    else:
        print("FAIL SketchLog telemetry load proof")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Generate deterministic realistic telemetry, ingest it into "
            "SketchLog, and prove bounded-memory analytics across storage backends."
        )
    )
    parser.add_argument(
        "--backend",
        choices=["memory", "postgres", "omnikv", "all"],
        action="append",
        help="Backend to prove. Repeatable. Defaults to memory.",
    )
    parser.add_argument(
        "--events",
        type=int,
        default=DEFAULT_EVENT_COUNT,
        help=f"Number of realistic telemetry events to generate. Default: {DEFAULT_EVENT_COUNT}.",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=DEFAULT_SEED,
        help=f"Deterministic fixture seed. Default: {DEFAULT_SEED}.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help=f"Events per HTTP ingest batch. Default: {DEFAULT_BATCH_SIZE}.",
    )
    parser.add_argument(
        "--fixture-output",
        type=Path,
        help="Optional JSONL path for the generated telemetry fixture.",
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
    if args.events < 1:
        raise SystemExit("--events must be >= 1")
    if args.batch_size < 1:
        raise SystemExit("--batch-size must be >= 1")
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
