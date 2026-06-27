import pytest
from sketchlog.facade import StreamLog
from sketchlog.slo import SmartSLOEngine
from fastapi.testclient import TestClient
from sketchlog.server import app
from sketchlog.server import registry

client = TestClient(app)

@pytest.fixture(autouse=True)
def cleanup():
    registry._streams.clear()
    yield
    registry._streams.clear()

def test_smart_slo_engine():
    # Baseline stream
    baseline = StreamLog()
    baseline.add_batch([10, 20, 30, 40, 50, 60, 70, 80, 90, 100]) # p90 is 90

    # Current stream (worse)
    current = StreamLog()
    current.add_batch([10, 20, 30, 40, 50, 60, 70, 80, 90, 100, 150, 200])

    # target_percentile 0.9 => target latency approx 90
    # current_error is values > 90 => 3 (100, 150, 200)
    # total = 12
    # error rate = 3 / 12 = 0.25
    # budget = 0.05
    # burn rate = 0.25 / 0.05 = 5.0
    metrics = SmartSLOEngine.evaluate(
        current_stream=current,
        historical_stream=baseline,
        target_percentile=0.9,
        budget_percent=0.05
    )

    assert abs(metrics["target_latency"] - 90.0) < 5.0
    assert metrics["current_errors"] == 3
    assert abs(metrics["current_error_rate"] - 0.25) < 1e-5
    assert metrics["is_alerting"] == True

def test_slo_endpoint():
    # Create baseline
    client.post("/v1/streams/baseline/events", json={"latencies": [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]})
    # Create current
    client.post("/v1/streams/current/events", json={"latencies": [10, 20, 30, 40, 50, 60, 70, 80, 90, 100, 150, 200]})

    response = client.post("/v1/streams/current/slo/evaluate", json={
        "baseline_stream_id": "baseline",
        "target_percentile": 0.9,
        "budget_percent": 0.05
    })

    assert response.status_code == 200
    data = response.json()
    assert abs(data["target_latency"] - 90.0) < 5.0
    assert data["current_errors"] == 3
    assert data["is_alerting"] == True
