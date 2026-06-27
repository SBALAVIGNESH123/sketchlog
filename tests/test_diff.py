import pytest
from sketchlog import StreamLog
from sketchlog.diff import SketchDiff, get_cdf, ks_statistic, wasserstein_distance
from fastapi.testclient import TestClient
from sketchlog.server import app, registry

@pytest.fixture(autouse=True)
def cleanup():
    registry._streams.clear()
    yield

def test_sketch_diff_math():
    s1 = StreamLog()
    s1.add_batch([10, 20, 30, 40, 50, 60, 70, 80, 90, 100])
    
    s2 = StreamLog()
    s2.add_batch([15, 25, 35, 45, 55, 65, 75, 85, 95, 105])
    
    s3 = StreamLog()
    s3.add_batch([10, 20, 30, 40, 50, 60, 70, 80, 90, 100, 150, 200, 250, 300]) # heavy tail
    
    diff_s1_s1 = SketchDiff(s1, s1)
    assert diff_s1_s1.ks_statistic == 0.0
    assert diff_s1_s1.wasserstein_distance == 0.0
    
    diff_s1_s2 = SketchDiff(s1, s2)
    assert diff_s1_s2.ks_statistic > 0.0
    assert diff_s1_s2.wasserstein_distance > 0.0
    
    diff_s1_s3 = SketchDiff(s1, s3)
    assert diff_s1_s3.ks_statistic > 0.0
    assert diff_s1_s3.wasserstein_distance > diff_s1_s2.wasserstein_distance  # Heavier tail -> higher Wasserstein distance
    
    # Check ASCII plot generation doesn't crash and returns string
    plot_str = diff_s1_s3.plot_ascii()
    assert isinstance(plot_str, str)
    assert "1.0 |" in plot_str
    assert "0.0 |" in plot_str

def test_sketch_diff_endpoint():
    s1 = StreamLog()
    s1.add_batch([10, 20, 30])
    registry._streams["base"] = s1
    
    s2 = StreamLog()
    s2.add_batch([10, 20, 100])
    registry._streams["curr"] = s2

    client = TestClient(app)
    
    response = client.get("/v1/streams/curr/diff?baseline_stream_id=base")
    assert response.status_code == 200
    data = response.json()
    
    assert "ks_statistic" in data
    assert "wasserstein_distance" in data
    assert "cdf_1" in data
    assert "cdf_2" in data
    assert "ascii_plot" in data
    
    assert data["ks_statistic"] > 0
    assert data["wasserstein_distance"] > 0
    
    # Check not found
    res = client.get("/v1/streams/nonexistent/diff?baseline_stream_id=base")
    assert res.status_code == 404
