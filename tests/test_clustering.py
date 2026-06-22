import os
import subprocess
import sys
import time
import httpx
import pytest

# We want to spin up 3 nodes: 8001, 8002, 8003
# They will peer with each other.

@pytest.fixture(scope="module")
def cluster():
    env1 = os.environ.copy()
    env1["SKETCHLOG_NODE_ID"] = "node1"
    env1["SKETCHLOG_PEERS"] = "http://127.0.0.1:8002,http://127.0.0.1:8003"
    
    env2 = os.environ.copy()
    env2["SKETCHLOG_NODE_ID"] = "node2"
    env2["SKETCHLOG_PEERS"] = "http://127.0.0.1:8001,http://127.0.0.1:8003"
    
    env3 = os.environ.copy()
    env3["SKETCHLOG_NODE_ID"] = "node3"
    env3["SKETCHLOG_PEERS"] = "http://127.0.0.1:8001,http://127.0.0.1:8002"

    p1 = subprocess.Popen([sys.executable, "-m", "uvicorn", "sketchlog.server:app", "--port", "8001"], env=env1)
    p2 = subprocess.Popen([sys.executable, "-m", "uvicorn", "sketchlog.server:app", "--port", "8002"], env=env2)
    p3 = subprocess.Popen([sys.executable, "-m", "uvicorn", "sketchlog.server:app", "--port", "8003"], env=env3)

    # Wait for startup
    time.sleep(3)

    yield ["http://127.0.0.1:8001", "http://127.0.0.1:8002", "http://127.0.0.1:8003"]

    p1.terminate()
    p2.terminate()
    p3.terminate()
    p1.wait()
    p2.wait()
    p3.wait()


def test_cluster_merge(cluster):
    urls = cluster
    # Node 1: Latency 10ms, Unique "user_A"
    httpx.post(f"{urls[0]}/v1/streams/my-cluster-stream/events", json={
        "latencies": [10.0],
        "uniques": ["user_A"]
    })
    
    # Node 2: Latency 20ms, Unique "user_B"
    httpx.post(f"{urls[1]}/v1/streams/my-cluster-stream/events", json={
        "latencies": [20.0],
        "uniques": ["user_B"]
    })
    
    # Node 3: Latency 30ms, Unique "user_C"
    httpx.post(f"{urls[2]}/v1/streams/my-cluster-stream/events", json={
        "latencies": [30.0],
        "uniques": ["user_C"]
    })

    # The sync interval is default 5.0 seconds. We must wait for the gossip to propagate.
    # We will poll up to 10 seconds.
    success = False
    for _ in range(20):
        time.sleep(0.5)
        # Check Node 1's view of the cluster
        resp = httpx.get(f"{urls[0]}/v1/streams/my-cluster-stream/metrics")
        if resp.status_code == 200:
            data = resp.json()
            if data["total_events"] == 3 and data["unique_count"] == 3:
                # Merged successfully!
                assert 19.0 <= data["p50"] <= 21.0  # Median of 10, 20, 30 is 20
                assert 29.0 <= data["p99"] <= 31.0  # 99th percentile is 30
                success = True
                break

    assert success, "Cluster failed to converge within timeout"

    # Now verify idempotency: the gossip sync will continue to happen every 5 seconds.
    # The counts should not double-count!
    time.sleep(6) # wait past another sync interval
    resp = httpx.get(f"{urls[1]}/v1/streams/my-cluster-stream/metrics")
    data = resp.json()
    assert data["total_events"] == 3
    assert data["unique_count"] == 3
