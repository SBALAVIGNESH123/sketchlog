import pytest
from fastapi.testclient import TestClient

from sketchlog.server import app, registry

client = TestClient(app)

@pytest.fixture(autouse=True)
def reset_registry():
    """Clear the stream registry before each test."""
    registry._streams.clear()

def test_health_check():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}

def test_readiness_check():
    response = client.get("/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ready"}

def test_ingest_and_query_metrics():
    stream_id = "test-stream-1"

    # 1. Ingest data
    payload = {
        "latencies": [10.5, 20.1, 30.5, 100.0, 50.0],
        "uniques": ["userA", "userB", "userA"],
        "events": {
            "login": 5,
            "error": 1
        }
    }

    response = client.post(f"/v1/streams/{stream_id}/events", json=payload)
    assert response.status_code == 202
    assert response.json() == {"status": "accepted"}

    # 2. Query metrics
    response = client.get(f"/v1/streams/{stream_id}/metrics")
    assert response.status_code == 200

    data = response.json()
    assert data["stream_id"] == stream_id
    assert 0.0 < data["p50"] <= 100.0
    assert 0.0 < data["p99"] <= 105.0
    assert data["unique_count"] == 2
    assert data["total_events"] == 11
    assert data["memory_footprint_bytes"] > 0

    # 3. Query specific event count using query parameter
    response = client.get(f"/v1/streams/{stream_id}/events?name=login")
    assert response.status_code == 200
    assert response.json() == {"stream_id": stream_id, "event_name": "login", "count": 5}

def test_query_nonexistent_stream():
    response = client.get("/v1/streams/does-not-exist/metrics")
    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()

def test_delete_stream():
    stream_id = "test-delete"

    client.post(f"/v1/streams/{stream_id}/events", json={"latencies": [1.0]})

    # Delete it
    response = client.delete(f"/v1/streams/{stream_id}")
    assert response.status_code == 204

    # Query should fail
    response = client.get(f"/v1/streams/{stream_id}/metrics")
    assert response.status_code == 404

def test_lru_eviction():
    # Fill registry beyond capacity
    max_streams = registry.max_size

    for i in range(max_streams + 5):
        stream_id = f"stream-{i}"
        client.post(f"/v1/streams/{stream_id}/events", json={"latencies": [1.0]})

    assert len(registry._streams) == max_streams

    # The first 5 should be evicted
    response = client.get("/v1/streams/stream-0/metrics")
    assert response.status_code == 404

    # The last one should exist
    response = client.get(f"/v1/streams/stream-{max_streams + 4}/metrics")
    assert response.status_code == 200

def test_invalid_event_count():
    stream_id = "test-invalid-events"

    payload = {
        "latencies": [42.0],
        "uniques": ["u1"],
        "events": {"invalid": 0}
    }

    response = client.post(f"/v1/streams/{stream_id}/events", json=payload)
    assert response.status_code == 422

    # Ensure atomicity (stream should not be created/mutated)
    response = client.get(f"/v1/streams/{stream_id}/metrics")
    assert response.status_code == 404

def test_batch_size_limit():
    stream_id = "test-batch-limit"

    from sketchlog.server import MAX_BATCH_SIZE

    payload = {
        "latencies": [1.0] * (MAX_BATCH_SIZE + 1)
    }

    response = client.post(f"/v1/streams/{stream_id}/events", json=payload)
    assert response.status_code == 422
    assert "exceeds maximum limit" in response.text

def test_oversized_request_body():
    stream_id = "test-body-limit"
    # The default limit is 1MB. We can simulate a large request by mocking headers since TestClient bypasses some real network limits,
    # or just passing a large string if TestClient passes it through the middleware correctly.
    # Actually TestClient serializes JSON to bytes, so a 1.1MB payload will have Content-Length > 1MB.

    # 1.5MB of data
    large_string = "a" * 1500000
    payload = {"uniques": [large_string]}

    response = client.post(f"/v1/streams/{stream_id}/events", json=payload)
    assert response.status_code == 413

def test_path_encoding_and_limits():
    stream_id = "test/stream/encoded"

    payload = {
        "events": {"cache/miss": 1}
    }
    response = client.post(f"/v1/streams/{stream_id}/events", json=payload)
    assert response.status_code == 202

    # 3. Query specific event count
    response = client.get(f"/v1/streams/{stream_id}/events?name=cache/miss")
    assert response.status_code == 200
    assert response.json() == {"stream_id": stream_id, "event_name": "cache/miss", "count": 1}

    # 4. Unknown event returns 200 with count 0
    response = client.get(f"/v1/streams/{stream_id}/events?name=unknown")
    assert response.status_code == 200
    assert response.json() == {"stream_id": stream_id, "event_name": "unknown", "count": 0}

def test_native_backend_range_limit():
    stream_id = "test-native-limit"

    payload = {
        "latencies": [42.0],
        "events": {"overflow": 2**100}
    }

    # Ensure atomic rejection on single event overflow leaves no stream created
    response = client.post(f"/v1/streams/{stream_id}/events", json=payload)
    assert response.status_code == 422
    assert client.get(f"/v1/streams/{stream_id}/metrics").status_code == 404

    # Check total stream capacity overflow leaves no stream created
    payload_capacity = {
        "latencies": [42.0],
        "events": {"almost": 9223372036854775807}
    }
    response = client.post(f"/v1/streams/{stream_id}/events", json=payload_capacity)
    assert response.status_code == 422
    assert client.get(f"/v1/streams/{stream_id}/metrics").status_code == 404

def test_rejected_request_does_not_mutate_lru():
    from sketchlog.server import registry
    original_max = registry.max_size
    registry.max_size = 2
    try:
        # Create A and B
        client.post("/v1/streams/stream-a/events", json={"latencies": [1]})
        client.post("/v1/streams/stream-b/events", json={"latencies": [1]})
        
        # Order should be A, B (B is most recent)
        assert list(registry._streams.keys()) == ["stream-a", "stream-b"]
        
        # Send overflow to A (should be rejected)
        payload = {"events": {"overflow": 9223372036854775807}}
        assert client.post("/v1/streams/stream-a/events", json=payload).status_code == 422
        
        # Order should STILL be A, B
        assert list(registry._streams.keys()) == ["stream-a", "stream-b"]
        
        # Create C, should evict A (least recent)
        client.post("/v1/streams/stream-c/events", json={"latencies": [1]})
        assert list(registry._streams.keys()) == ["stream-b", "stream-c"]
    finally:
        registry.max_size = original_max

def test_oversized_chunked_request():
    stream_id = "test-oversized-chunked"

    # This payload is generated dynamically to simulate a chunked transfer exceeding 1MB limit.
    def generate_large_payload():
        yield b'{"uniques": ["'
        for _ in range(150):
            yield b'a' * 10000
        yield b'"]}'

    response = client.post(f"/v1/streams/{stream_id}/events", content=generate_large_payload(), headers={"Content-Type": "application/json"})
    assert response.status_code == 413

    # Assert stream was NOT created because the endpoint execution was preempted
    response_metric = client.get(f"/v1/streams/{stream_id}/metrics")
    assert response_metric.status_code == 404

def test_malformed_content_length():
    response = client.post("/v1/streams/test/events", json={}, headers={"Content-Length": "abc"})
    assert response.status_code == 400
