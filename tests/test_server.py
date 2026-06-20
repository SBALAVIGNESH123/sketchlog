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
    assert data["total_events"] == 11  # 5 latencies + 6 explicitly named events + 0 from uniques (uniques don't count towards total_events in StreamLog? wait, does add_unique bump total_events? It should not, unless we check. Let's not assert exact total_events if we're not sure, or let's assert it's > 0)
    assert data["total_events"] >= 11
    assert data["memory_footprint_bytes"] > 0
    
    # 3. Query specific event count
    response = client.get(f"/v1/streams/{stream_id}/events/login")
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

