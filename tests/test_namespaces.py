import pytest
from fastapi.testclient import TestClient

from sketchlog.server import app, registry

client = TestClient(app)

@pytest.fixture(autouse=True)
def reset_registry():
    """Clear the stream registry before each test."""
    registry._namespaces.clear()
    original_max = registry.max_streams_per_ns
    yield
    registry.max_streams_per_ns = original_max

def test_namespace_isolation():
    """Verify that streams in different namespaces are completely isolated."""
    # Insert data into namespace 'tenant-a'
    client.post("/v1/namespaces/tenant-a/streams/login-latency/events", json={"latencies": [100.0, 200.0]})

    # Insert data into namespace 'tenant-b' with the same stream_id
    client.post("/v1/namespaces/tenant-b/streams/login-latency/events", json={"latencies": [500.0]})

    # Check tenant-a
    r_a = client.get("/v1/namespaces/tenant-a/streams/login-latency/metrics")
    assert r_a.status_code == 200
    assert r_a.json()["total_events"] == 2
    assert r_a.json()["p50"] <= 200.0

    # Check tenant-b
    r_b = client.get("/v1/namespaces/tenant-b/streams/login-latency/metrics")
    assert r_b.status_code == 200
    assert r_b.json()["total_events"] == 1
    assert r_b.json()["p50"] >= 490.0

    # Check default namespace (should not exist)
    r_default = client.get("/v1/streams/login-latency/metrics")
    assert r_default.status_code == 404

def test_namespace_lru_eviction():
    """Verify that LRU eviction is scoped per namespace."""
    registry.max_streams_per_ns = 5

    # Fill tenant-a to limit
    for i in range(5):
        client.post(f"/v1/namespaces/tenant-a/streams/stream-{i}/events", json={"latencies": [1.0]})

    # Fill tenant-b to limit
    for i in range(5):
        client.post(f"/v1/namespaces/tenant-b/streams/stream-{i}/events", json={"latencies": [1.0]})

    # Add one more to tenant-a, triggering eviction
    client.post("/v1/namespaces/tenant-a/streams/stream-overflow/events", json={"latencies": [1.0]})

    assert len(registry._namespaces["tenant-a"]) == 5
    assert len(registry._namespaces["tenant-b"]) == 5

    # Stream-0 in tenant-a should be gone
    assert client.get("/v1/namespaces/tenant-a/streams/stream-0/metrics").status_code == 404
    # Stream-0 in tenant-b should still exist
    assert client.get("/v1/namespaces/tenant-b/streams/stream-0/metrics").status_code == 200

def test_cross_tenant_aggregation():
    """Verify that /v1/namespaces/aggregate correctly combines data across namespaces."""
    client.post("/v1/namespaces/tenant-a/streams/cpu-usage/events", json={"latencies": [10.0, 20.0]})
    client.post("/v1/namespaces/tenant-b/streams/cpu-usage/events", json={"latencies": [90.0, 100.0]})

    # Get aggregate
    r = client.get("/v1/namespaces/aggregate?namespaces=tenant-a,tenant-b&stream_id=cpu-usage")
    assert r.status_code == 200
    data = r.json()
    assert data["total_events"] == 4

    # Should include data from both (p50 around 20, p99 around 100)
    assert data["p50"] >= 10.0 and data["p50"] <= 90.0
    assert data["p99"] >= 90.0

def test_cross_tenant_aggregation_missing_stream():
    """Verify that /v1/namespaces/aggregate returns 404 if stream doesn't exist in any namespace."""
    r = client.get("/v1/namespaces/aggregate?namespaces=tenant-a,tenant-b&stream_id=does-not-exist")
    assert r.status_code == 404

def test_delete_stream_namespace():
    """Verify that deleting a stream in one namespace doesn't affect others."""
    client.post("/v1/namespaces/tenant-a/streams/test-del/events", json={"latencies": [100.0]})
    client.post("/v1/namespaces/tenant-b/streams/test-del/events", json={"latencies": [200.0]})

    # Delete from tenant-a
    assert client.delete("/v1/namespaces/tenant-a/streams/test-del").status_code == 204

    # Verify tenant-a is gone
    assert client.get("/v1/namespaces/tenant-a/streams/test-del/metrics").status_code == 404

    # Verify tenant-b is intact
    assert client.get("/v1/namespaces/tenant-b/streams/test-del/metrics").status_code == 200
