import copy
import json
import math
import re
import threading
import tempfile
from pathlib import Path

import numpy as np
import pytest

import sketchlog
from sketchlog import StreamLog
from sketchlog.cluster import ClusterManager
from sketchlog.server import EventBatch, StreamRegistry, app
from fastapi.testclient import TestClient
import sketchlog.server as server_module


@pytest.mark.parametrize(
    "values",
    [
        np.arange(10, dtype=np.float64)[::2],
        np.arange(10, dtype=np.float64)[::-1],
        np.arange(10, dtype=np.float32)[1::2],
    ],
)
def test_cpp_batch_respects_numpy_views(values):
    cpp = StreamLog()
    python = StreamLog(deterministic=True)

    cpp.add_batch(values)
    python.add_batch(float(value) for value in values)

    expected = {
        **python.to_dict(),
        "deterministic": False,
    }
    assert json.loads(cpp.to_json()) == json.loads(json.dumps(expected))


def test_raw_cpp_ddsketch_respects_numpy_views():
    if not sketchlog.HAS_CPP:
        pytest.skip("C++ extension unavailable")

    values = np.arange(12, dtype=np.float64)[::-2]
    sketch = sketchlog._cpp.DDSketch()
    sketch.add_batch(values)

    assert sketch.count() == len(values)
    assert sketch.min() == float(min(values))
    assert sketch.max() == float(max(values))


@pytest.mark.parametrize(
    "mutation",
    [
        lambda state: state.__setitem__("total", 999),
        lambda state: state["events"].__setitem__("total", 999),
        lambda state: state["latency"].update(
            count=1, zero_count=1, min=float("nan"), max=float("nan")
        ),
    ],
)
def test_cpp_serialization_rejects_inconsistent_state(mutation):
    state = StreamLog().to_dict()
    mutation(state)

    with pytest.raises((ValueError, RuntimeError)):
        StreamLog.from_dict(state)

    if sketchlog.HAS_CPP:
        with pytest.raises((ValueError, RuntimeError)):
            sketchlog._cpp.StreamLog.from_dict(state)


@pytest.mark.parametrize("deterministic", [False, True])
def test_sparse_extreme_range_is_supported_with_bounded_memory(deterministic):
    log = StreamLog(deterministic=deterministic)
    log.add_batch([1e-300, 1e300])

    assert log.total_events == 2
    assert log.to_dict()["latency"]["min"] == 1e-300
    assert log.to_dict()["latency"]["max"] == 1e300
    assert log.memory_bytes() < 256 * 1024


@pytest.mark.parametrize("deterministic", [False, True])
def test_bucket_capacity_rejection_is_transactional(deterministic):
    log = StreamLog(deterministic=deterministic)
    before = copy.deepcopy(log.to_dict())
    gamma = 1.01 / 0.99
    representative_factor = 2.0 / (1.0 + gamma)
    values = [
        representative_factor * math.pow(gamma, index * 2)
        for index in range(1025)
    ]

    with pytest.raises((ValueError, RuntimeError)):
        log.add_batch(values)

    assert log.to_dict() == before


@pytest.mark.parametrize("deterministic", [False, True])
def test_threshold_count_is_a_conservative_upper_bound(deterministic):
    values = [99.0, 100.1]
    log = StreamLog(deterministic=deterministic)
    log.add_batch(values)

    actual = sum(value > 100.0 for value in values)
    assert log.count_greater_than(100.0) >= actual

    negative_values = [-101.0, -99.9]
    negative = StreamLog(deterministic=deterministic)
    negative.add_batch(negative_values)
    actual_negative = sum(value > -100.0 for value in negative_values)
    assert negative.count_greater_than(-100.0) >= actual_negative


def test_integer_event_keys_and_cross_backend_merges():
    cpp = StreamLog()
    python = StreamLog(deterministic=True)
    cpp.add_event(42, 2)
    python.add_event(42, 3)

    cpp.merge(python)
    assert cpp.total_events == 5
    assert cpp.event_count(42) == 5

    python_target = StreamLog(deterministic=True)
    cpp_source = StreamLog()
    python_target.add_event(7, 4)
    cpp_source.add_event(7, 5)
    python_target.merge(cpp_source)
    assert python_target.total_events == 9
    assert python_target.event_count(7) == 9


def test_deterministic_merge_capacity_failure_is_transactional():
    gamma = 1.01 / 0.99
    representative_factor = 2.0 / (1.0 + gamma)
    left = StreamLog(deterministic=True)
    right = StreamLog(deterministic=True)
    left.add_batch([
        representative_factor * math.pow(gamma, index * 2)
        for index in range(600)
    ])
    right.add_batch(
        [
            representative_factor * math.pow(gamma, index * 2)
            for index in range(600, 1025)
        ])

    before = copy.deepcopy(left.to_dict())
    with pytest.raises(ValueError, match="bucket count exceeds"):
        left.merge(right)
    assert left.to_dict() == before


def test_deterministic_merge_counter_overflow_is_transactional():
    maximum = 2**63 - 1

    latency_state = StreamLog(deterministic=True).to_dict()
    latency_state["total"] = maximum
    latency_state["latency"].update(
        zero_count=maximum, count=maximum, min=0.0, max=0.0)
    latency = StreamLog.from_dict(latency_state)
    one_latency = StreamLog(deterministic=True)
    one_latency.add_latency(0.0)
    before_latency = copy.deepcopy(latency.to_dict())
    with pytest.raises(OverflowError, match="zero count overflow"):
        latency.merge(one_latency)
    assert latency.to_dict() == before_latency

    events_state = StreamLog(deterministic=True, cms_width=1, cms_depth=1).to_dict()
    events_state["total"] = maximum
    events_state["events"]["total"] = maximum
    events_state["events"]["table"] = [[maximum]]
    events = StreamLog.from_dict(events_state)
    one_event = StreamLog(
        deterministic=True, cms_width=1, cms_depth=1)
    one_event.add_event("one")
    before_events = copy.deepcopy(events.to_dict())
    with pytest.raises(OverflowError, match="total_count overflow"):
        events.merge(one_event)
    assert events.to_dict() == before_events


@pytest.mark.parametrize(
    "mutation",
    [
        lambda state: state.__setitem__("total", 2**64),
        lambda state: state["latency"].update(count=2**64),
        lambda state: state["latency"].update(
            count=2**63, zero_count=2**63, min=0.0, max=0.0),
        lambda state: state.update(
            total=2**63,
            events={
                "width": 1,
                "depth": 1,
                "total": 2**63,
                "table": [[2**63]],
            },
        ),
    ],
)
def test_serialized_counters_must_fit_native_domains(mutation):
    state = StreamLog(
        deterministic=True, cms_width=1, cms_depth=1).to_dict()
    mutation(state)
    with pytest.raises(ValueError):
        StreamLog.from_dict(state)


@pytest.mark.parametrize("bad_key", [-1, 2**64])
def test_integer_event_keys_have_one_backend_independent_domain(bad_key):
    for deterministic in (False, True):
        log = StreamLog(deterministic=deterministic)
        with pytest.raises(ValueError, match="64-bit unsigned"):
            log.add_event(bad_key)
        with pytest.raises(ValueError, match="64-bit unsigned"):
            log.event_count(bad_key)


def test_mesh_membership_rejects_non_allowlisted_and_malformed_origins():
    class Registry:
        def snapshot_items(self):
            return []

    manager = ClusterManager(
        node_id="node-a",
        peers=["https://mesh.example:8443"],
        registry=Registry(),
        cluster_secret="secret",
    )
    manager._merge_membership(
        {
            "attacker": {
                "address": "http://127.0.0.1:80",
                "status": "alive",
                "incarnation": 1,
            },
            "userinfo": {
                "address": "https://token@mesh.example:8443",
                "status": "alive",
                "incarnation": 1,
            },
            "allowed": {
                "address": "https://mesh.example:8443/",
                "status": "alive",
                "incarnation": 1,
            },
        }
    )

    assert "attacker" not in manager.members
    assert "userinfo" not in manager.members
    assert manager.members["allowed"]["address"] == "https://mesh.example:8443"


def test_mesh_http_client_refuses_non_allowlisted_origin_before_network():
    class Registry:
        def snapshot_items(self):
            return []

    manager = ClusterManager(
        node_id="node-a",
        peers=["https://mesh.example"],
        registry=Registry(),
        cluster_secret="secret",
    )
    with pytest.raises(ValueError, match="non-allowlisted"):
        manager._http_post("http://127.0.0.1/mesh/ping", {})


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf"), True])
def test_http_model_rejects_non_finite_and_boolean_latencies(value):
    with pytest.raises(ValueError, match="finite"):
        EventBatch(latencies=[1.0, value])


def test_production_app_has_no_test_routes():
    assert not any(route.path.startswith("/test/") for route in app.routes)


@pytest.mark.asyncio
async def test_global_registry_cap_applies_across_namespaces():
    registry = StreamRegistry(max_streams_per_namespace=10, max_streams=2)
    await registry.get_or_create("a", "one")
    await registry.get_or_create("b", "two")
    await registry.get_or_create("c", "three")

    assert len(registry.snapshot_items()) == 2
    assert registry.peek("a", "one") is None


@pytest.mark.asyncio
async def test_failed_durable_eviction_keeps_victim_resident():
    class FailingStorage:
        async def load(self, *args, **kwargs):
            return None

        async def save(self, *args, **kwargs):
            raise OSError("disk unavailable")

    registry = StreamRegistry(
        max_streams_per_namespace=1,
        max_streams=1,
        storage=FailingStorage(),
    )
    original = await registry.get_or_create("a", "one")
    original.add_latency(42)

    with pytest.raises(OSError, match="disk unavailable"):
        await registry.get_or_create("a", "two")

    assert registry.peek("a", "one") is original
    assert registry.peek("a", "two") is None


def test_http_metric_paths_do_not_contain_user_identifiers():
    with TestClient(app) as client:
        for stream_id in ("customer-a", "customer-b"):
            client.post(
                f"/v1/namespaces/acme/streams/{stream_id}/events",
                json={"latencies": [1.0]},
            )
        client.get("/definitely-not-a-route/customer-secret")
        metrics = client.get("/metrics").text

    assert 'path="/v1/namespaces/{namespace}/streams/{stream_id:path}/events"' in metrics
    assert 'path="unmatched"' in metrics
    assert "customer-a" not in metrics
    assert "customer-b" not in metrics
    assert "customer-secret" not in metrics


@pytest.mark.parametrize(
    "raw_value", ["NaN", "Infinity", "-Infinity", "1e309", "true"])
def test_http_rejects_invalid_latencies_atomically(raw_value):
    stream_id = f"invalid-latency-{raw_value.replace('-', 'neg')}"
    with TestClient(app) as client:
        response = client.post(
            f"/v1/streams/{stream_id}/events",
            content=f'{{"latencies":[2.0,{raw_value}]}}',
            headers={"content-type": "application/json"},
        )
        assert response.status_code == 422
        assert client.get(f"/v1/streams/{stream_id}/metrics").status_code == 404


def test_namespace_tokens_enforce_cross_tenant_isolation(monkeypatch):
    monkeypatch.setattr(server_module, "AUTH_TOKEN", None)
    monkeypatch.setattr(
        server_module,
        "NAMESPACE_TOKENS",
        {
            "token-a": frozenset({"tenant-a"}),
            "token-b": frozenset({"tenant-b"}),
        },
    )
    headers_a = {"X-SketchLog-Auth-Token": "token-a"}
    headers_b = {"X-SketchLog-Auth-Token": "token-b"}

    with TestClient(app) as client:
        assert client.post(
            "/v1/namespaces/tenant-a/streams/isolation/events",
            json={"latencies": [10]},
            headers=headers_a,
        ).status_code == 202
        assert client.get(
            "/v1/namespaces/tenant-a/streams/isolation/metrics",
            headers=headers_a,
        ).status_code == 200
        assert client.get(
            "/v1/namespaces/tenant-a/streams/isolation/metrics",
            headers=headers_b,
        ).status_code == 403
        assert client.delete(
            "/v1/namespaces/tenant-a/streams/isolation",
            headers=headers_b,
        ).status_code == 403
        assert client.get(
            "/v1/namespaces/aggregate",
            params={"namespaces": "tenant-a,tenant-b", "stream_id": "isolation"},
            headers=headers_a,
        ).status_code == 403
        assert client.post(
            "/v1/query",
            json={"query": 'SELECT p99(latency) FROM "tenant-a/isolation"'},
            headers=headers_b,
        ).status_code == 403


def test_browser_websocket_accepts_scoped_httponly_cookie(monkeypatch):
    monkeypatch.setattr(server_module, "AUTH_TOKEN", None)
    monkeypatch.setattr(
        server_module, "NAMESPACE_TOKENS",
        {"browser-token": frozenset({"browser-tenant"})})
    with TestClient(app) as client:
        client.cookies.set("sketchlog_auth", "browser-token")
        with client.websocket_connect(
            "/v1/namespaces/browser-tenant/streams/missing/ws"
        ) as websocket:
            assert websocket.receive_json() == {"error": "Stream not found"}


def test_atomic_checkpoint_preserves_previous_file_on_replace_failure(monkeypatch):
    with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
        path = Path(directory) / "checkpoint.json"
        log = StreamLog(deterministic=True)
        log.add_latency(1)
        log.save(str(path))
        original = path.read_bytes()
        log.add_latency(2)

        def fail_replace(*_args):
            raise OSError("simulated replace failure")

        monkeypatch.setattr("sketchlog._atomic.os.replace", fail_replace)
        with pytest.raises(OSError, match="simulated"):
            log.save(str(path))

        assert path.read_bytes() == original
        assert StreamLog.load(str(path)).total_events == 1
        assert list(Path(directory).glob("*.tmp")) == []


def test_concurrent_checkpoint_readers_never_observe_partial_json():
    with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
        path = Path(directory) / "checkpoint.json"
        log = StreamLog(deterministic=True)
        log.save(str(path))
        failures = []
        stop = threading.Event()

        def reader():
            while not stop.is_set():
                try:
                    StreamLog.load(str(path))
                except Exception as exc:  # pragma: no cover - failure evidence
                    failures.append(exc)
                    stop.set()

        thread = threading.Thread(target=reader)
        thread.start()
        try:
            for value in range(30):
                log.add_latency(value)
                log.save(str(path))
        finally:
            stop.set()
            thread.join(timeout=5)
        assert failures == []


def test_checkpoint_size_limit_preserves_existing_file(monkeypatch):
    from sketchlog import _atomic

    with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
        path = Path(directory) / "bounded.json"
        path.write_text('{"valid":true}', encoding="utf-8")
        monkeypatch.setattr(_atomic, "MAX_SERIALIZED_STATE_BYTES", 32)

        with pytest.raises(ValueError, match="32 MiB"):
            _atomic.atomic_write_json(
                str(path), {"oversized": "x" * 64})
        assert json.loads(path.read_text(encoding="utf-8")) == {"valid": True}

        path.write_text(json.dumps({"oversized": "x" * 64}), encoding="utf-8")
        with pytest.raises(ValueError, match="32 MiB"):
            _atomic.read_json_checkpoint(str(path))


def test_grafana_dashboard_queries_only_server_exported_metrics():
    dashboard_path = (
        __import__("pathlib").Path(__file__).parents[1]
        / "dashboards" / "sketchlog-overview.json"
    )
    dashboard = json.loads(dashboard_path.read_text(encoding="utf-8"))
    expressions = [
        target["expr"]
        for panel in dashboard["panels"]
        for target in panel.get("targets", [])
    ]
    referenced = {
        name
        for expression in expressions
        for name in re.findall(r"\b(sketchlog_[a-zA-Z0-9_:]+)", expression)
    }
    assert "sketchlog_total_events" not in referenced
    assert "sketchlog_latency" not in referenced
    assert "sketchlog_unique_count" not in referenced
    assert "sketchlog_memory_kb" not in referenced

    with TestClient(app) as client:
        client.post(
            "/v1/streams/dashboard-contract/events",
            json={"latencies": [1]},
        )
        client.get("/ready")
        scrape = client.get("/metrics").text
    for metric in referenced:
        assert metric in scrape, f"dashboard references unavailable metric {metric}"


def test_readiness_memory_measurement_supports_cgroup_v1(monkeypatch):
    values = {
        "/sys/fs/cgroup/memory.current": None,
        "/sys/fs/cgroup/memory.max": None,
        "/sys/fs/cgroup/memory/memory.usage_in_bytes": 400.0,
        "/sys/fs/cgroup/memory/memory.limit_in_bytes": 1000.0,
    }
    monkeypatch.delenv("SKETCHLOG_MEMORY_LIMIT_BYTES", raising=False)
    monkeypatch.setattr(server_module, "_OS_NAME", "posix")
    monkeypatch.setattr(
        server_module, "_read_cgroup_number", lambda path: values[path])

    current, limit = server_module._effective_memory_usage()
    assert (current, limit) == (400.0, 1000.0)


def test_readiness_metric_exposes_one_active_state():
    server_module._set_readiness_cause("memory")
    values = {
        sample.labels["cause"]: sample.value
        for metric in server_module.READINESS_STATUS.collect()
        for sample in metric.samples
        if sample.name == "sketchlog_readiness_status"
    }
    assert values["memory"] == 1
    assert values["ready"] == 0
    assert sum(values.values()) == 1
