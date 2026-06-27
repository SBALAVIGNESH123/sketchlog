import pytest
import time
from sketchlog.cluster import ClusterManager
from sketchlog.server import StreamRegistry
from sketchlog.facade import StreamLog

def test_cluster_membership_ping():
    registry = StreamRegistry(10)
    cm1 = ClusterManager(node_id="node1", peers=[], registry=registry, advertised_address="http://node1")

    # Simulate receiving a ping from node2
    payload = {
        "node_id": "node2",
        "address": "http://node2",
        "members": {
            "node2": {"address": "http://node2", "status": "alive", "incarnation": 1, "last_updated": time.time()}
        }
    }

    resp = cm1.handle_ping(payload)
    assert "node2" in cm1.members
    assert cm1.members["node2"]["status"] == "alive"

    # Response should contain node1
    assert "node1" in resp["members"]

def test_anti_entropy_digest():
    registry1 = StreamRegistry(10)
    cm1 = ClusterManager(node_id="node1", peers=[], registry=registry1, advertised_address="http://node1")

    # node1 has a local stream
    stream = registry1.get_or_create("streamA")
    stream.add_batch([10.0])

    # Simulate node2 sending a digest where it doesn't know about streamA
    payload = {
        "node_id": "node2",
        "versions": {}
    }

    resp = cm1.handle_gossip_digest(payload)

    # node1 should send the update for streamA
    assert "updates" in resp
    assert "streamA" in resp["updates"]
    assert "node1" in resp["updates"]["streamA"]

    # Simulate node2 has a newer streamB that node1 wants
    payload2 = {
        "node_id": "node2",
        "versions": {
            "streamB": {"node2": time.time()}
        }
    }

    resp2 = cm1.handle_gossip_digest(payload2)
    assert "requests" in resp2
    assert "streamB" in resp2["requests"]
    assert "node2" in resp2["requests"]["streamB"]

def test_anti_entropy_sync():
    registry1 = StreamRegistry(10)
    cm1 = ClusterManager(node_id="node1", peers=[], registry=registry1, advertised_address="http://node1")

    # node2 sends an actual snapshot
    remote_log = StreamLog(deterministic=True)
    remote_log.add_batch([50.0])

    payload = {
        "node_id": "node2",
        "streams": {
            "streamB": {
                "node2": remote_log.to_dict()
            }
        }
    }

    cm1.handle_gossip_sync(payload)

    # node1 should now have this in peer_snapshots
    assert cm1.has_peer_data("streamB")

    merged = cm1.get_merged_stream("streamB", None)
    assert merged.total_events == 1
    assert merged.percentile(0.5) > 0
