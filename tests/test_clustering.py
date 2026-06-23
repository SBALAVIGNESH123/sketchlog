import os
import subprocess
import sys
import time
import httpx
import pytest

import socket

def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]

# We want to spin up 3 nodes with dynamic ports to avoid CI conflicts
# They will peer with each other.

@pytest.fixture(scope="module")
def cluster():
    port1 = find_free_port()
    port2 = find_free_port()
    port3 = find_free_port()

    env1 = os.environ.copy()
    env1["SKETCHLOG_NODE_ID"] = "node1"
    env1["SKETCHLOG_PEERS"] = f"http://127.0.0.1:{port2},http://127.0.0.1:{port3}"

    env2 = os.environ.copy()
    env2["SKETCHLOG_NODE_ID"] = "node2"
    env2["SKETCHLOG_PEERS"] = f"http://127.0.0.1:{port1},http://127.0.0.1:{port3}"

    env3 = os.environ.copy()
    env3["SKETCHLOG_NODE_ID"] = "node3"
    env3["SKETCHLOG_PEERS"] = f"http://127.0.0.1:{port1},http://127.0.0.1:{port2}"

    p1 = subprocess.Popen([sys.executable, "-m", "uvicorn", "sketchlog.server:app", "--port", str(port1)], env=env1)
    p2 = subprocess.Popen([sys.executable, "-m", "uvicorn", "sketchlog.server:app", "--port", str(port2)], env=env2)
    p3 = subprocess.Popen([sys.executable, "-m", "uvicorn", "sketchlog.server:app", "--port", str(port3)], env=env3)

    # Wait for startup
    urls = [f"http://127.0.0.1:{port1}", f"http://127.0.0.1:{port2}", f"http://127.0.0.1:{port3}"]
    for url in urls:
        wait_for_ready(url)

    yield urls

    try:
        p1.terminate()
        p2.terminate()
        p3.terminate()
        p1.wait(timeout=5)
        p2.wait(timeout=5)
        p3.wait(timeout=5)
    finally:
        p1.kill()
        p2.kill()
        p3.kill()


def wait_for_ready(url: str, timeout: float = 10.0) -> None:
    start = time.time()
    while time.time() - start < timeout:
        try:
            r = httpx.get(f"{url}/health")
            if r.status_code == 200:
                return
        except httpx.RequestError:
            pass
        time.sleep(0.1)
    raise RuntimeError(f"Server at {url} failed to start within {timeout}s")


def test_cluster_convergence(cluster):
    urls = cluster
    # Node 1: Latency 10ms, Unique "user_A"
    r1 = httpx.post(f"{urls[0]}/v1/streams/my-cluster-stream/events", json={
        "latencies": [10.0],
        "uniques": ["user_A"]
    })
    r1.raise_for_status()

    # Node 2: Latency 20ms, Unique "user_B"
    r2 = httpx.post(f"{urls[1]}/v1/streams/my-cluster-stream/events", json={
        "latencies": [20.0],
        "uniques": ["user_B"]
    })
    r2.raise_for_status()

    # Node 3: Latency 30ms, Unique "user_C"
    r3 = httpx.post(f"{urls[2]}/v1/streams/my-cluster-stream/events", json={
        "latencies": [30.0],
        "uniques": ["user_C"]
    })
    r3.raise_for_status()

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
