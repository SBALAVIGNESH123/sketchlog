import logging
import threading
import time
import json
import urllib.request
import urllib.error
import urllib.parse
import random
from typing import Dict, List, Optional, Any, Tuple
from threading import Lock

from sketchlog.facade import StreamLog
from sketchlog.concurrent import ThreadSafeStreamLog

logger = logging.getLogger(__name__)

class ClusterManager:
    def __init__(self, node_id: str, peers: List[str], registry: Any,
                 sync_interval: float = 5.0, ping_interval: float = 1.0,
                 heartbeat_timeout: float = 60.0, cluster_secret: Optional[str] = None,
                 advertised_address: Optional[str] = None):
        self.node_id = node_id
        self.registry = registry
        self.sync_interval = sync_interval
        self.ping_interval = ping_interval
        self.heartbeat_timeout = heartbeat_timeout
        self.cluster_secret = cluster_secret
        self.advertised_address = advertised_address
        self.incarnation = 0

        # Membership: node_id -> {"address": str, "status": str, "incarnation": int, "last_updated": float}
        self.members: Dict[str, Dict[str, Any]] = {}
        self.members[self.node_id] = {
            "address": self.advertised_address or "",
            "status": "alive",
            "incarnation": self.incarnation,
            "last_updated": time.time()
        }

        # Seed nodes parsing
        for p in peers:
            if not p:
                continue
            parsed = urllib.parse.urlparse(p)
            if parsed.scheme in ("http", "https") and parsed.netloc:
                # Add seed nodes as 'alive' with incarnation 0
                seed_id = f"seed-{p}" # We don't know their node_id yet, will be updated upon first ping
                self.members[seed_id] = {
                    "address": p.strip().rstrip('/'),
                    "status": "alive",
                    "incarnation": 0,
                    "last_updated": time.time(),
                    "is_seed": True
                }
            else:
                logger.warning(f"Ignoring invalid peer URL: {p}")

        # peer_snapshots[stream_id][node_id] = (StreamLog, version_ts)
        self.peer_snapshots: Dict[str, Dict[str, Tuple[StreamLog, float]]] = {}
        self._lock = Lock()

        self._stop_event = threading.Event()
        self._membership_thread: Optional[threading.Thread] = None
        self._gossip_thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if not self.members and not self.advertised_address:
            logger.info("No peers and no advertised address. ClusterManager will not start.")
            return

        logger.info(f"Starting ClusterManager node '{self.node_id}' (Mesh Mode)")
        self._stop_event.clear()

        self._membership_thread = threading.Thread(target=self._membership_loop, daemon=True)
        self._membership_thread.start()

        self._gossip_thread = threading.Thread(target=self._gossip_loop, daemon=True)
        self._gossip_thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._membership_thread:
            self._membership_thread.join(timeout=2.0)
        if self._gossip_thread:
            self._gossip_thread.join(timeout=2.0)

    # --- MEMBERSHIP (SWIM-LITE) ---
    def _membership_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._ping_random_member()
                self._check_timeouts()
            except Exception as e:
                logger.error(f"Error in membership loop: {e}")
            self._stop_event.wait(self.ping_interval)

    def _ping_random_member(self) -> None:
        with self._lock:
            # Pick a random alive member that is not us
            candidates = [
                (nid, info) for nid, info in self.members.items()
                if nid != self.node_id and info["status"] in ("alive", "suspect")
            ]

        if not candidates:
            return

        nid, info = random.choice(candidates)
        address = info["address"]
        if not address:
            return

        # Prepare payload: send up to 10 recently updated members
        with self._lock:
            sorted_members = sorted(self.members.items(), key=lambda x: x[1]["last_updated"], reverse=True)
            gossip_list = {k: v for k, v in sorted_members[:10]}

        payload = {
            "node_id": self.node_id,
            "address": self.advertised_address,
            "members": gossip_list
        }

        try:
            resp_data = self._http_post(f"{address}/mesh/ping", payload)
            if resp_data and "members" in resp_data:
                self._merge_membership(resp_data["members"])

                # If it was a seed node with a temporary ID, delete the temp ID now that we know its real ID
                if info.get("is_seed"):
                    with self._lock:
                        if nid in self.members:
                            del self.members[nid]
        except Exception:
            # Ping failed. Mark as suspect.
            with self._lock:
                if nid in self.members and self.members[nid]["status"] == "alive":
                    self.members[nid]["status"] = "suspect"
                    self.members[nid]["last_updated"] = time.time()
                    logger.warning(f"Node {nid} ping failed, marking as suspect")

    def _merge_membership(self, incoming_members: Dict[str, Dict[str, Any]]) -> None:
        with self._lock:
            for nid, info in incoming_members.items():
                if nid == self.node_id:
                    # If someone says we are suspect/dead, increment incarnation to refute
                    if info["status"] in ("suspect", "dead") and info["incarnation"] >= self.incarnation:
                        self.incarnation = info["incarnation"] + 1
                        self.members[self.node_id]["incarnation"] = self.incarnation
                        self.members[self.node_id]["last_updated"] = time.time()
                    continue

                if nid not in self.members:
                    self.members[nid] = info
                    self.members[nid]["last_updated"] = time.time()
                    logger.info(f"Discovered new node: {nid}")
                else:
                    local_info = self.members[nid]
                    # Update if incarnation is higher
                    if info["incarnation"] > local_info["incarnation"]:
                        self.members[nid] = info
                        self.members[nid]["last_updated"] = time.time()
                    # Or if incarnation is same but status is worse
                    elif info["incarnation"] == local_info["incarnation"]:
                        if local_info["status"] == "alive" and info["status"] in ("suspect", "dead"):
                            self.members[nid]["status"] = info["status"]
                            self.members[nid]["last_updated"] = time.time()

    def _check_timeouts(self) -> None:
        now = time.time()
        with self._lock:
            for nid, info in list(self.members.items()):
                if nid == self.node_id:
                    continue
                # If suspect for too long -> dead
                if info["status"] == "suspect" and now - info["last_updated"] > self.heartbeat_timeout:
                    info["status"] = "dead"
                    info["last_updated"] = now
                    logger.info(f"Node {nid} marked as dead")

                # If dead for 5x timeout -> remove entirely
                if info["status"] == "dead" and now - info["last_updated"] > self.heartbeat_timeout * 5:
                    del self.members[nid]

                    # Evict peer snapshots
                    for stream_id in list(self.peer_snapshots.keys()):
                        if nid in self.peer_snapshots[stream_id]:
                            del self.peer_snapshots[stream_id][nid]

    def handle_ping(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Called by FastAPI when /mesh/ping is hit"""
        # Always update our own timestamp so we look alive
        with self._lock:
            self.members[self.node_id]["last_updated"] = time.time()

        if "members" in payload:
            self._merge_membership(payload["members"])

        with self._lock:
            sorted_members = sorted(self.members.items(), key=lambda x: x[1]["last_updated"], reverse=True)
            gossip_list = {k: v for k, v in sorted_members[:10]}

        return {"members": gossip_list}

    # --- STATE GOSSIP (ANTI-ENTROPY) ---
    def _gossip_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._gossip_state()
            except Exception as e:
                logger.error(f"Error in gossip loop: {e}")
            self._stop_event.wait(self.sync_interval)

    def _gossip_state(self) -> None:
        with self._lock:
            candidates = [
                info["address"] for nid, info in self.members.items()
                if nid != self.node_id and info["status"] == "alive" and info["address"]
            ]

        if not candidates:
            return

        address = random.choice(candidates)

        # Build version vector for all streams we know about
        # version_vector[stream_id][node_id] = ts
        version_vector: Dict[str, Dict[str, float]] = {}

        # 1. Local streams
        local_ts = time.time()
        for stream_id, _ in self.registry.snapshot_items():
            version_vector[stream_id] = {self.node_id: local_ts}

        # 2. Peer streams
        with self._lock:
            for stream_id, peers in self.peer_snapshots.items():
                if stream_id not in version_vector:
                    version_vector[stream_id] = {}
                for nid, (_, ts) in peers.items():
                    # Don't gossip timestamps for dead nodes
                    if nid in self.members and self.members[nid]["status"] == "dead":
                        continue
                    version_vector[stream_id][nid] = ts

        if not version_vector:
            return

        payload = {
            "node_id": self.node_id,
            "versions": version_vector
        }

        try:
            resp_data = self._http_post(f"{address}/mesh/gossip/digest", payload)
            if resp_data:
                # 1. Integrate updates sent back to us
                if "updates" in resp_data:
                    self.receive_snapshot(resp_data["node_id"], resp_data["updates"], timestamp=time.time())

                # 2. Fulfill requests for data they need
                if "requests" in resp_data and resp_data["requests"]:
                    sync_payload: Dict[str, Any] = {"node_id": self.node_id, "streams": {}}
                    for stream_id, nids in resp_data["requests"].items():
                        sync_payload["streams"][stream_id] = {}
                        for nid in nids:
                            if nid == self.node_id:
                                stream = self.registry.get(stream_id)
                                if stream:
                                    try:
                                        sync_payload["streams"][stream_id][nid] = stream.get_snapshot().to_dict()
                                    except Exception:
                                        pass
                            else:
                                with self._lock:
                                    if stream_id in self.peer_snapshots and nid in self.peer_snapshots[stream_id]:
                                        log, _ = self.peer_snapshots[stream_id][nid]
                                        sync_payload["streams"][stream_id][nid] = log.to_dict()

                    if sync_payload["streams"]:
                        self._http_post(f"{address}/mesh/gossip/sync", sync_payload, fire_and_forget=True)

        except Exception as e:
            logger.debug(f"Failed to gossip state with {address}: {e}")

    def handle_gossip_digest(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Called by FastAPI when /mesh/gossip/digest is hit. Returns missing snapshots + requested snapshots."""
        remote_versions = payload.get("versions", {})

        updates_to_send: Dict[str, Dict[str, Any]] = {}
        requests_to_make: Dict[str, List[str]] = {}

        local_ts = time.time()

        # 1. Compare what they have vs what we have
        # A. Local streams
        for stream_id, _ in self.registry.snapshot_items():
            remote_stream = remote_versions.get(stream_id, {})
            remote_node_ts = remote_stream.get(self.node_id, 0)
            # If they don't have our local stream or it's old (they shouldn't have our local stream newer than us)
            if remote_node_ts == 0: # Simply: we have it, they don't
                stream = self.registry.get(stream_id)
                if stream:
                    try:
                        if stream_id not in updates_to_send:
                            updates_to_send[stream_id] = {}
                        updates_to_send[stream_id][self.node_id] = stream.get_snapshot().to_dict()
                    except Exception:
                        pass

        # B. Peer streams comparison
        with self._lock:
            for stream_id, peers in self.peer_snapshots.items():
                for nid, (log, local_node_ts) in peers.items():
                    remote_node_ts = remote_versions.get(stream_id, {}).get(nid, 0)
                    if local_node_ts > remote_node_ts:
                        # We have newer, send to them
                        if stream_id not in updates_to_send:
                            updates_to_send[stream_id] = {}
                        updates_to_send[stream_id][nid] = log.to_dict()

        # 2. Compare what they have that we want
        with self._lock:
            for stream_id, remote_peers in remote_versions.items():
                for nid, remote_node_ts in remote_peers.items():
                    if nid == self.node_id:
                        continue # We don't want our own data from someone else

                    local_node_ts = 0
                    if stream_id in self.peer_snapshots and nid in self.peer_snapshots[stream_id]:
                        _, local_node_ts = self.peer_snapshots[stream_id][nid]

                    if remote_node_ts > local_node_ts:
                        # They have newer, request it
                        if stream_id not in requests_to_make:
                            requests_to_make[stream_id] = []
                        requests_to_make[stream_id].append(nid)

        return {
            "node_id": self.node_id,
            "updates": updates_to_send,
            "requests": requests_to_make
        }

    def handle_gossip_sync(self, payload: Dict[str, Any]) -> None:
        """Called by FastAPI when /mesh/gossip/sync is hit"""
        self.receive_snapshot(payload["node_id"], payload["streams"], timestamp=time.time())

    def receive_snapshot(self, sender_node_id: str, streams_data: Dict[str, Dict[str, Any]], timestamp: float) -> None:
        # payload structure for anti-entropy is: streams_data[stream_id][node_id] = dict
        with self._lock:
            for stream_id, node_data in streams_data.items():
                if stream_id not in self.peer_snapshots:
                    if len(self.peer_snapshots) >= 10000:
                        continue
                    self.peer_snapshots[stream_id] = {}

                for nid, data in node_data.items():
                    if nid == self.node_id:
                        continue # Ignore our own data echoed back

                    if len(self.peer_snapshots[stream_id]) >= 50 and nid not in self.peer_snapshots[stream_id]:
                        continue

                    # Overwrite if newer (we don't have exact timestamps of creation in the dict, so we use receipt time)
                    # For anti-entropy, receiving an update means it's newer than what we had.
                    try:
                        log = StreamLog.from_dict(data)
                        self.peer_snapshots[stream_id][nid] = (log, timestamp)
                    except Exception as e:
                        logger.warning(f"Failed to deserialize snapshot from {sender_node_id} for stream {stream_id}, origin {nid}: {e}")

    def get_merged_stream(self, stream_id: str, local_stream: Optional[Any]) -> StreamLog:
        merged = local_stream.get_snapshot() if local_stream else StreamLog(deterministic=True)
        with self._lock:
            if stream_id in self.peer_snapshots:
                for node_id, (peer_log, _) in self.peer_snapshots[stream_id].items():
                    # Check if node is dead, if so, we can exclude its data or keep it?
                    # Let's keep it until it's evicted from peer_snapshots
                    merged.merge(peer_log)
        return merged

    def has_peer_data(self, stream_id: str) -> bool:
        with self._lock:
            return stream_id in self.peer_snapshots and bool(self.peer_snapshots[stream_id])

    def _http_post(self, url: str, payload: Dict[str, Any], fire_and_forget: bool = False) -> Optional[Dict[str, Any]]:
        payload_bytes = json.dumps(payload).encode('utf-8')
        if len(payload_bytes) > 60_000_000:
            logger.error("Gossip payload too large (>60MB).")
            return None

        headers = {"Content-Type": "application/json"}
        if self.cluster_secret:
            headers["X-SketchLog-Cluster-Token"] = self.cluster_secret

        try:
            req = urllib.request.Request(url, data=payload_bytes, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=2.0) as response:
                if fire_and_forget:
                    return None
                body = response.read()
                result: Dict[str, Any] = json.loads(body)
                return result
        except Exception as e:
            raise e
