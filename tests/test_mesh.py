import pytest
import time
import json
from fastapi.testclient import TestClient
from sketchlog.cluster import ClusterManager
from sketchlog.server import StreamRegistry, app
from sketchlog.facade import StreamLog

def test_cluster_membership_ping():
    registry = StreamRegistry(10)
    cm1 = ClusterManager(
        node_id="node1",
        peers=[],
        registry=registry,
        advertised_address="http://node1",
        peer_allowlist=["http://node2"],
    )

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

@pytest.mark.asyncio
async def test_anti_entropy_digest():
    registry1 = StreamRegistry(10)
    cm1 = ClusterManager(node_id="node1", peers=[], registry=registry1, advertised_address="http://node1")

    # node1 has a local stream
    stream = await registry1.get_or_create("default", "streamA")
    stream.add_batch([10.0])

    # Simulate node2 sending a digest where it doesn't know about streamA
    payload = {
        "node_id": "node2",
        "versions": {}
    }

    resp = cm1.handle_gossip_digest(payload)

    # node1 should send the update for streamA
    assert "updates" in resp
    assert '["default", "streamA"]' in resp["updates"]
    assert "node1" in resp["updates"]['["default", "streamA"]']

    # Simulate node2 has a newer streamB that node1 wants
    payload2 = {
        "node_id": "node2",
        "versions": {
            '["default", "streamB"]': {"node2": time.time()}
        }
    }

    resp2 = cm1.handle_gossip_digest(payload2)
    assert "requests" in resp2
    assert '["default", "streamB"]' in resp2["requests"]
    assert "node2" in resp2["requests"]['["default", "streamB"]']

def test_anti_entropy_sync():
    registry1 = StreamRegistry(10)
    cm1 = ClusterManager(node_id="node1", peers=[], registry=registry1, advertised_address="http://node1")

    # node2 sends an actual snapshot
    remote_log = StreamLog(deterministic=True)
    remote_log.add_batch([50.0])

    payload = {
        "node_id": "node2",
        "streams": {
            '["default", "streamB"]': {
                "node2": remote_log.to_dict()
            }
        }
    }

    cm1.handle_gossip_sync(payload)

    # node1 should now have this in peer_snapshots
    assert cm1.has_peer_data("default", "streamB")

    merged = cm1.get_merged_stream("default", "streamB", None)
    assert merged.total_events == 1
    assert merged.percentile(0.5) > 0


def test_tombstone_prevents_stale_snapshot_resurrection():
    registry = StreamRegistry(10)
    manager = ClusterManager(
        node_id="node1", peers=[], registry=registry,
        advertised_address="http://node1")
    stream_id = '["default", "deleted"]'
    remote = StreamLog(deterministic=True)
    remote.add_latency(42)

    manager.receive_snapshot(
        "relay", {stream_id: {"node2": {
            "__version__": 1.0, "state": remote.to_dict()}}},
        timestamp=10.0,
    )
    assert manager.has_peer_data("default", "deleted")

    manager.receive_snapshot(
        "node2", {stream_id: {"node2": {
            "__version__": 2.0, "__tombstone__": True}}},
        timestamp=11.0,
    )
    assert not manager.has_peer_data("default", "deleted")

    manager.receive_snapshot(
        "stale-relay", {stream_id: {"node2": {
            "__version__": 1.0, "state": remote.to_dict()}}},
        timestamp=12.0,
    )
    assert not manager.has_peer_data("default", "deleted")

    manager.receive_snapshot(
        "node2", {stream_id: {"node2": {
            "__version__": 3.0, "state": remote.to_dict()}}},
        timestamp=13.0,
    )
    assert manager.has_peer_data("default", "deleted")


def test_local_tombstone_is_returned_by_digest():
    registry = StreamRegistry(10)
    manager = ClusterManager(
        node_id="node1", peers=[], registry=registry,
        advertised_address="http://node1")
    manager.record_deletion("default", "gone")

    response = manager.handle_gossip_digest({"node_id": "node2", "versions": {}})
    update = response["updates"]['["default", "gone"]']["node1"]
    assert update["__tombstone__"] is True
    assert update["__version__"] > 0


def test_local_tombstone_capacity_fails_closed_and_can_rollback():
    registry = StreamRegistry(10)
    manager = ClusterManager(
        node_id="node1",
        peers=[],
        registry=registry,
        max_local_tombstones=1,
    )
    stream_key, version, previous = manager.begin_deletion(
        "default", "first")
    with pytest.raises(RuntimeError, match="capacity exhausted"):
        manager.begin_deletion("default", "second")

    manager.rollback_deletion(stream_key, version, previous)
    assert manager.local_tombstones == {}
    manager.begin_deletion("default", "second")


@pytest.mark.asyncio
async def test_large_digest_response_progresses_in_bounded_rounds():
    registry = StreamRegistry(10)
    manager = ClusterManager(
        node_id="node1",
        peers=[],
        registry=registry,
        advertised_address="http://node1",
        max_payload_bytes=30_000,
    )
    for name in ("first", "second"):
        stream = await registry.get_or_create("default", name)
        stream.add_latency(1)

    first = manager.handle_gossip_digest(
        {"node_id": "node2", "versions": {}})
    assert len(manager._json_bytes(first)) <= manager.max_payload_bytes
    assert len(first["updates"]) == 1

    received_versions = {
        stream_id: {
            origin: update["__version__"]
            for origin, update in origins.items()
        }
        for stream_id, origins in first["updates"].items()
    }
    second = manager.handle_gossip_digest(
        {"node_id": "node2", "versions": received_versions})
    assert len(manager._json_bytes(second)) <= manager.max_payload_bytes
    assert set(first["updates"]).isdisjoint(second["updates"])
    assert len(second["updates"]) == 1


@pytest.mark.asyncio
async def test_requested_snapshots_are_split_into_bounded_sync_requests():
    registry = StreamRegistry(10)
    manager = ClusterManager(
        node_id="node1",
        peers=["http://node2"],
        registry=registry,
        advertised_address="http://node1",
        peer_allowlist=["http://node2"],
        max_payload_bytes=30_000,
    )
    requested = {}
    for name in ("one", "two", "three"):
        stream = await registry.get_or_create("default", name)
        stream.add_latency(1)
        requested[json.dumps(["default", name])] = ["node1"]

    captured = []

    def fake_post(url, payload, fire_and_forget=False):
        if url.endswith("/digest"):
            return {
                "node_id": "node2",
                "updates": {},
                "requests": requested,
            }
        captured.append(payload)
        return None

    manager._http_post = fake_post
    manager._gossip_state()

    assert len(captured) == 3
    assert all(
        len(manager._json_bytes(payload)) <= manager.max_payload_bytes
        for payload in captured
    )
    sent_streams = {
        stream_id
        for payload in captured
        for stream_id in payload["streams"]
    }
    assert sent_streams == set(requested)


def test_mesh_rejects_invalid_snapshot_keys_and_non_finite_versions():
    class Registry:
        def snapshot_items(self):
            return []

    manager = ClusterManager(
        node_id="node1", peers=[], registry=Registry())
    manager.receive_snapshot(
        "node2",
        {
            "not-a-stream-key": {
                "node2": {"__version__": 1, "state": {}}},
            '["default", "valid"]': {
                "node2": {"__version__": float("nan"), "state": {}}},
        },
        timestamp=1,
    )
    assert manager.peer_snapshots == {}


def test_mesh_http_response_is_bounded(monkeypatch):
    class Registry:
        def snapshot_items(self):
            return []

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self, size):
            return b"x" * size

    class FakeOpener:
        def open(self, request, timeout):
            return FakeResponse()

    monkeypatch.setattr(
        "sketchlog.cluster.urllib.request.build_opener",
        lambda *args: FakeOpener(),
    )
    manager = ClusterManager(
        node_id="node1",
        peers=["http://node2"],
        registry=Registry(),
        max_payload_bytes=1024,
    )
    with pytest.raises(ValueError, match="response exceeds"):
        manager._http_post("http://node2/mesh/ping", {})


def test_mesh_request_body_uses_its_dedicated_bound(monkeypatch):
    monkeypatch.setattr(
        "sketchlog.server.MAX_MESH_PAYLOAD_BYTES", 64)
    with TestClient(app) as client:
        response = client.post(
            "/mesh/gossip/sync",
            content=b"x" * 65,
            headers={"Content-Type": "application/json"},
        )
    assert response.status_code == 413
