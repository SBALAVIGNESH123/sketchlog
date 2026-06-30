import logging
import threading
import time
import json
import math
import os
import urllib.request
import urllib.error
import urllib.parse
import random
from typing import Dict, List, Optional, Any, Tuple, Set
from threading import Lock

from sketchlog.facade import StreamLog
from sketchlog.concurrent import ThreadSafeStreamLog

logger = logging.getLogger(__name__)

MAX_MESH_PAYLOAD_BYTES = int(
    os.environ.get("SKETCHLOG_MAX_MESH_PAYLOAD_BYTES", str(40 * 1024 * 1024)))
if not 1024 <= MAX_MESH_PAYLOAD_BYTES <= 64 * 1024 * 1024:
    raise ValueError(
        "SKETCHLOG_MAX_MESH_PAYLOAD_BYTES must be between 1024 and 67108864")
MAX_LOCAL_TOMBSTONES = int(
    os.environ.get("SKETCHLOG_MAX_LOCAL_TOMBSTONES", "100000"))
if not 1 <= MAX_LOCAL_TOMBSTONES <= 1_000_000:
    raise ValueError(
        "SKETCHLOG_MAX_LOCAL_TOMBSTONES must be between 1 and 1000000")


class ClusterManager:
    MAX_MEMBERS = 256
    MAX_PEER_STREAMS = 10_000
    MAX_ORIGINS_PER_STREAM = 50
    MAX_IDENTIFIER_LENGTH = 255

    def __init__(self, node_id: str, peers: List[str], registry: Any,
                 sync_interval: float = 5.0, ping_interval: float = 1.0,
                 heartbeat_timeout: float = 60.0, cluster_secret: Optional[str] = None,
                 advertised_address: Optional[str] = None,
                 peer_allowlist: Optional[List[str]] = None,
                 max_payload_bytes: int = MAX_MESH_PAYLOAD_BYTES,
                 max_local_tombstones: int = MAX_LOCAL_TOMBSTONES):
        if not 1024 <= max_payload_bytes <= 64 * 1024 * 1024:
            raise ValueError(
                "max_payload_bytes must be between 1024 and 67108864")
        if not 1 <= max_local_tombstones <= 1_000_000:
            raise ValueError(
                "max_local_tombstones must be between 1 and 1000000")
        self.node_id = node_id
        self.registry = registry
        self.sync_interval = sync_interval
        self.ping_interval = ping_interval
        self.heartbeat_timeout = heartbeat_timeout
        self.cluster_secret = cluster_secret
        self.advertised_address = advertised_address
        self.max_payload_bytes = max_payload_bytes
        self.max_local_tombstones = max_local_tombstones
        self.incarnation = 0
        self._digest_cursor = 0
        self._has_seeds = bool([p for p in peers if p])

        # Membership: node_id -> {"address": str, "status": str, "incarnation": int, "last_updated": float}
        self.members: Dict[str, Dict[str, Any]] = {}
        self.members[self.node_id] = {
            "address": self.advertised_address or "",
            "status": "alive",
            "incarnation": self.incarnation,
            "last_updated": time.time()
        }

        configured_allowlist = peer_allowlist if peer_allowlist is not None else peers
        self._allowed_peer_origins: Set[str] = set()
        for allowed in configured_allowlist:
            try:
                self._allowed_peer_origins.add(self._canonical_base_url(allowed))
            except ValueError:
                logger.warning("Ignoring invalid peer allowlist URL: %s", allowed)

        # Seed nodes parsing
        for p in peers:
            if not p:
                continue
            try:
                base_url = self._canonical_base_url(p)
            except ValueError:
                base_url = ""
            if base_url and base_url in self._allowed_peer_origins:
                # Add seed nodes as 'alive' with incarnation 0
                seed_id = f"seed-{p}" # We don't know their node_id yet, will be updated upon first ping
                self.members[seed_id] = {
                    "address": base_url,
                    "status": "alive",
                    "incarnation": 0,
                    "last_updated": time.time(),
                    "is_seed": True
                }
            else:
                logger.warning(f"Ignoring invalid peer URL: {p}")

        # peer_snapshots[stream_id][node_id] = (StreamLog, version_ts)
        self.peer_snapshots: Dict[str, Dict[str, Tuple[StreamLog, float]]] = {}
        # Tombstones share the same origin-version space as snapshots. They
        # prevent old snapshots from being resurrected by relaying peers.
        self.peer_tombstones: Dict[str, Dict[str, float]] = {}
        self.local_tombstones: Dict[str, float] = {}
        self._lock = Lock()

        self._stop_event = threading.Event()
        self._membership_thread: Optional[threading.Thread] = None
        self._gossip_thread: Optional[threading.Thread] = None

    @staticmethod
    def _json_bytes(value: Any) -> bytes:
        return json.dumps(
            value, separators=(",", ":"), allow_nan=False).encode("utf-8")

    @classmethod
    def _valid_stream_key(cls, stream_id: Any) -> bool:
        if not isinstance(stream_id, str) or len(stream_id) > 1024:
            return False
        try:
            parts = json.loads(stream_id)
        except (TypeError, ValueError, json.JSONDecodeError):
            return False
        return (
            isinstance(parts, list)
            and len(parts) == 2
            and stream_id == json.dumps(parts)
            and all(
                isinstance(part, str)
                and 0 < len(part) <= cls.MAX_IDENTIFIER_LENGTH
                for part in parts
            )
        )

    def _bounded_version_vector(
            self, version_vector: Dict[str, Dict[str, float]]
    ) -> Dict[str, Dict[str, float]]:
        payload = {"node_id": self.node_id, "versions": version_vector}
        if len(self._json_bytes(payload)) <= self.max_payload_bytes:
            return version_vector

        items = list(version_vector.items())
        if not items:
            return {}
        selected: Dict[str, Dict[str, float]] = {}
        base_size = len(self._json_bytes(
            {"node_id": self.node_id, "versions": {}}))
        used = base_size
        start = self._digest_cursor % len(items)
        considered = 0

        while considered < len(items):
            index = (start + considered) % len(items)
            stream_id, origins = items[index]
            contribution = (
                (1 if selected else 0)
                + len(self._json_bytes(stream_id))
                + 1
                + len(self._json_bytes(origins))
            )
            if used + contribution <= self.max_payload_bytes:
                selected[stream_id] = origins
                used += contribution
                considered += 1
                continue

            if not selected:
                logger.error(
                    "Skipping mesh version-vector entry larger than the "
                    "configured payload limit: %s", stream_id)
                considered += 1
                continue
            break

        self._digest_cursor = (start + considered) % len(items)
        return selected

    @staticmethod
    def _canonical_base_url(address: str) -> str:
        if not isinstance(address, str):
            raise ValueError("Peer address must be a string")
        parsed = urllib.parse.urlsplit(address.strip())
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise ValueError("Peer address must be an absolute HTTP(S) URL")
        if parsed.username or parsed.password:
            raise ValueError("Peer address must not contain credentials")
        if parsed.query or parsed.fragment or parsed.path not in ("", "/"):
            raise ValueError("Peer address must contain only an origin")
        try:
            port = parsed.port
        except ValueError as exc:
            raise ValueError("Peer address has an invalid port") from exc
        host = parsed.hostname.lower()
        if ":" in host:
            host = f"[{host}]"
        default_port = 80 if parsed.scheme == "http" else 443
        authority = host if port in (None, default_port) else f"{host}:{port}"
        return f"{parsed.scheme.lower()}://{authority}"

    def _validate_peer_address(self, address: Any) -> str:
        base_url = self._canonical_base_url(address)
        if base_url not in self._allowed_peer_origins:
            raise ValueError("Peer address is not in SKETCHLOG_PEER_ALLOWLIST")
        return base_url

    def start(self) -> None:
        if not self._has_seeds and not self.advertised_address:
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
        if not isinstance(incoming_members, dict):
            raise ValueError("Membership payload must be an object")
        with self._lock:
            for nid, info in incoming_members.items():
                if not isinstance(nid, str) or not nid or len(nid) > 255:
                    continue
                if not isinstance(info, dict):
                    continue
                status = info.get("status")
                incarnation = info.get("incarnation")
                if status not in ("alive", "suspect", "dead"):
                    continue
                if type(incarnation) is not int or incarnation < 0:
                    continue
                if nid == self.node_id:
                    # If someone says we are suspect/dead, increment incarnation to refute
                    if info["status"] in ("suspect", "dead") and info["incarnation"] >= self.incarnation:
                        self.incarnation = info["incarnation"] + 1
                        self.members[self.node_id]["incarnation"] = self.incarnation
                        self.members[self.node_id]["last_updated"] = time.time()
                    continue

                remote_addr = info.get("address", "")
                if remote_addr:
                    try:
                        remote_addr = self._validate_peer_address(remote_addr)
                    except ValueError:
                        logger.warning(f"Rejecting invalid address {remote_addr} from {nid}")
                        continue
                    info = dict(info)
                    info["address"] = remote_addr

                if nid not in self.members:
                    if len(self.members) >= self.MAX_MEMBERS:
                        logger.warning("Rejecting member %s: membership limit reached", nid)
                        continue
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
        for ns, sid, stream in self.registry.snapshot_items():
            full_id = json.dumps([ns, sid])
            version_vector[full_id] = {self.node_id: getattr(stream, "last_updated", time.time())}
        with self._lock:
            for stream_id, version in self.local_tombstones.items():
                version_vector.setdefault(stream_id, {})[self.node_id] = max(
                    version, version_vector.get(stream_id, {}).get(self.node_id, 0))

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
            for stream_id, tombstones in self.peer_tombstones.items():
                for nid, version in tombstones.items():
                    version_vector.setdefault(stream_id, {})[nid] = max(
                        version,
                        version_vector.get(stream_id, {}).get(nid, 0),
                    )

        if not version_vector:
            return

        payload = {
            "node_id": self.node_id,
            "versions": self._bounded_version_vector(version_vector),
        }

        try:
            resp_data = self._http_post(f"{address}/mesh/gossip/digest", payload)
            if resp_data:
                # 1. Integrate updates sent back to us
                if "updates" in resp_data:
                    self.receive_snapshot(resp_data["node_id"], resp_data["updates"], timestamp=time.time())

                # 2. Fulfill requests for data they need
                if "requests" in resp_data and resp_data["requests"]:
                    sync_payload: Dict[str, Any] = {
                        "node_id": self.node_id, "streams": {}}

                    def flush_sync_payload() -> None:
                        nonlocal sync_payload
                        if sync_payload["streams"]:
                            self._http_post(
                                f"{address}/mesh/gossip/sync",
                                sync_payload,
                                fire_and_forget=True,
                            )
                            sync_payload = {
                                "node_id": self.node_id, "streams": {}}

                    def queue_sync_update(
                            stream_id: str, nid: str, data: Dict[str, Any]
                    ) -> None:
                        nonlocal sync_payload
                        current_group = sync_payload["streams"].get(
                            stream_id, {})
                        candidate_group = dict(current_group)
                        candidate_group[nid] = data
                        candidate_streams = dict(sync_payload["streams"])
                        candidate_streams[stream_id] = candidate_group
                        candidate = {
                            "node_id": self.node_id,
                            "streams": candidate_streams,
                        }
                        if len(self._json_bytes(candidate)) > self.max_payload_bytes:
                            flush_sync_payload()
                            candidate = {
                                "node_id": self.node_id,
                                "streams": {stream_id: {nid: data}},
                            }
                            if len(self._json_bytes(candidate)) > self.max_payload_bytes:
                                logger.error(
                                    "Skipping mesh snapshot larger than the "
                                    "configured payload limit: %s/%s",
                                    stream_id, nid)
                                return
                        sync_payload = candidate

                    for stream_id, nids in resp_data["requests"].items():
                        if (not self._valid_stream_key(stream_id)
                                or not isinstance(nids, list)):
                            continue
                        for nid in nids:
                            if (not isinstance(nid, str)
                                    or not nid
                                    or len(nid) > self.MAX_IDENTIFIER_LENGTH):
                                continue
                            if nid == self.node_id:
                                try:
                                    parts = json.loads(stream_id)
                                    if isinstance(parts, list) and len(parts) == 2:
                                        stream = self.registry.peek(parts[0], parts[1])
                                        stream_version = (
                                            getattr(stream, "last_updated", 0)
                                            if stream else 0)
                                        with self._lock:
                                            tombstone_version = self.local_tombstones.get(
                                                stream_id, 0)
                                        if tombstone_version >= stream_version:
                                            queue_sync_update(stream_id, nid, {
                                                "__tombstone__": True,
                                                "__version__": tombstone_version,
                                            })
                                        elif stream:
                                            queue_sync_update(stream_id, nid, {
                                                "__version__": stream_version,
                                                "state": stream.get_snapshot().to_dict(),
                                            })
                                except Exception as exc:
                                    logger.warning(
                                        "Unable to prepare requested local "
                                        "mesh snapshot %s/%s: %s",
                                        stream_id, nid, exc)
                            else:
                                update_data: Optional[Dict[str, Any]] = None
                                snapshot_to_send: Optional[StreamLog] = None
                                with self._lock:
                                    snapshot_version = (
                                        self.peer_snapshots.get(stream_id, {})
                                        .get(nid, (None, 0))[1])
                                    tombstone_version = self.peer_tombstones.get(
                                        stream_id, {}).get(nid, 0)
                                    if tombstone_version >= snapshot_version:
                                        update_data = {
                                            "__tombstone__": True,
                                            "__version__": tombstone_version,
                                        }
                                    elif stream_id in self.peer_snapshots and nid in self.peer_snapshots[stream_id]:
                                        log, _ = self.peer_snapshots[stream_id][nid]
                                        snapshot_to_send = log
                                if snapshot_to_send is not None:
                                    update_data = {
                                        "__version__": snapshot_version,
                                        "state": snapshot_to_send.to_dict(),
                                    }
                                if update_data is not None:
                                    queue_sync_update(
                                        stream_id, nid, update_data)

                    flush_sync_payload()

        except Exception as e:
            logger.debug(f"Failed to gossip state with {address}: {e}")

    def handle_gossip_digest(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Called by FastAPI when /mesh/gossip/digest is hit. Returns missing snapshots + requested snapshots."""
        remote_versions = payload.get("versions", {})
        if not isinstance(remote_versions, dict):
            raise ValueError("versions must be an object")
        normalized_versions: Dict[str, Dict[str, float]] = {}
        for stream_id, origins in remote_versions.items():
            if not self._valid_stream_key(stream_id) or not isinstance(origins, dict):
                continue
            normalized_origins: Dict[str, float] = {}
            for nid, version in origins.items():
                if (isinstance(nid, str)
                        and 0 < len(nid) <= self.MAX_IDENTIFIER_LENGTH
                        and isinstance(version, (int, float))
                        and not isinstance(version, bool)
                        and math.isfinite(version)
                        and version >= 0):
                    normalized_origins[nid] = float(version)
                if len(normalized_origins) >= self.MAX_ORIGINS_PER_STREAM:
                    break
            normalized_versions[stream_id] = normalized_origins
            if len(normalized_versions) >= self.MAX_PEER_STREAMS:
                break
        remote_versions = normalized_versions

        updates_to_send: Dict[str, Dict[str, Any]] = {}
        requests_to_make: Dict[str, List[str]] = {}
        response_size = len(self._json_bytes({
            "node_id": self.node_id, "updates": {}, "requests": {}}))

        def add_request(stream_id: str, nid: str) -> bool:
            nonlocal response_size
            current = requests_to_make.get(stream_id, [])
            if nid in current:
                return True
            candidate = [*current, nid]
            delta = len(self._json_bytes(candidate))
            if current:
                delta -= len(self._json_bytes(current))
            else:
                delta += (
                    (1 if requests_to_make else 0)
                    + len(self._json_bytes(stream_id))
                    + 1
                )
            # Keep at least three quarters available for snapshot updates.
            if response_size + delta > self.max_payload_bytes // 4:
                return False
            requests_to_make[stream_id] = candidate
            response_size += delta
            return True

        def add_update(
                stream_id: str, nid: str, data: Dict[str, Any]) -> bool:
            nonlocal response_size
            current = updates_to_send.get(stream_id, {})
            candidate = dict(current)
            candidate[nid] = data
            delta = len(self._json_bytes(candidate))
            if current:
                delta -= len(self._json_bytes(current))
            else:
                delta += (
                    (1 if updates_to_send else 0)
                    + len(self._json_bytes(stream_id))
                    + 1
                )
            if response_size + delta > self.max_payload_bytes:
                return False
            updates_to_send[stream_id] = candidate
            response_size += delta
            return True

        local_ts = time.time()

        # 1. Request newer origins first so both directions keep making progress
        # even when snapshot responses need several bounded gossip rounds.
        with self._lock:
            for stream_id, remote_peers in remote_versions.items():
                for nid, remote_node_ts in remote_peers.items():
                    if nid == self.node_id:
                        continue
                    known_version = 0.0
                    if stream_id in self.peer_snapshots and nid in self.peer_snapshots[stream_id]:
                        _, known_version = self.peer_snapshots[stream_id][nid]
                    known_version = max(
                        known_version,
                        self.peer_tombstones.get(stream_id, {}).get(nid, 0),
                    )
                    if remote_node_ts > known_version:
                        add_request(stream_id, nid)

        # 2. Compare what they have vs what we have
        # A. Local streams
        for ns, sid, stream in self.registry.snapshot_items():
            stream_id = json.dumps([ns, sid])
            remote_stream = remote_versions.get(stream_id, {})
            remote_node_ts = remote_stream.get(self.node_id, 0)
            local_stream_version = float(
                getattr(stream, "last_updated", local_ts))
            # If they don't have our local stream or it's old
            if local_stream_version > remote_node_ts:
                try:
                    if not add_update(stream_id, self.node_id, {
                        "__version__": local_stream_version,
                        "state": stream.get_snapshot().to_dict(),
                    }):
                        break
                except Exception as exc:
                    logger.warning(
                        "Unable to serialize local mesh snapshot %s: %s",
                        stream_id, exc)
        with self._lock:
            local_tombstones = dict(self.local_tombstones)
        for stream_id, version in local_tombstones.items():
            try:
                namespace, stream_name = json.loads(stream_id)
                local_stream = self.registry.peek(namespace, stream_name)
                if local_stream and getattr(local_stream, "last_updated", 0) > version:
                    continue
            except (TypeError, ValueError):
                continue
            if version > remote_versions.get(stream_id, {}).get(self.node_id, 0):
                if not add_update(stream_id, self.node_id, {
                    "__tombstone__": True,
                    "__version__": version,
                }):
                    break

        # B. Peer streams comparison
        with self._lock:
            peer_snapshots = [
                (stream_id, list(peers.items()))
                for stream_id, peers in self.peer_snapshots.items()
            ]
            peer_tombstones = [
                (stream_id, list(tombstones.items()))
                for stream_id, tombstones in self.peer_tombstones.items()
            ]
            snapshot_versions = {
                (stream_id, nid): version
                for stream_id, peers in self.peer_snapshots.items()
                for nid, (_, version) in peers.items()
            }
        for stream_id, peers in peer_snapshots:
            for nid, (log, peer_version) in peers:
                remote_node_ts = remote_versions.get(stream_id, {}).get(nid, 0)
                if peer_version > remote_node_ts:
                    # We have newer, send to them
                    if not add_update(stream_id, nid, {
                        "__version__": peer_version,
                        "state": log.to_dict(),
                    }):
                        break
        for stream_id, tombstones in peer_tombstones:
            for nid, version in tombstones:
                snapshot_version = snapshot_versions.get(
                    (stream_id, nid), 0)
                if snapshot_version > version:
                    continue
                if version > remote_versions.get(stream_id, {}).get(nid, 0):
                    if not add_update(stream_id, nid, {
                        "__tombstone__": True,
                        "__version__": version,
                    }):
                        break

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
        if (not isinstance(sender_node_id, str)
                or not sender_node_id
                or len(sender_node_id) > self.MAX_IDENTIFIER_LENGTH):
            raise ValueError("invalid sender node ID")
        if not isinstance(streams_data, dict):
            raise ValueError("streams must be an object")
        for stream_id, node_data in streams_data.items():
            if (not self._valid_stream_key(stream_id)
                    or not isinstance(node_data, dict)):
                continue

            for nid, data in node_data.items():
                if (not isinstance(nid, str)
                        or not nid
                        or len(nid) > self.MAX_IDENTIFIER_LENGTH
                        or not isinstance(data, dict)):
                    continue
                if nid == self.node_id:
                    continue # Ignore our own data echoed back

                try:
                    version = data.get("__version__", timestamp)
                    if (not isinstance(version, (int, float))
                            or isinstance(version, bool)
                            or not math.isfinite(version)
                            or version < 0):
                        raise ValueError("invalid origin version")

                    is_tombstone = data.get("__tombstone__") is True
                    log: Optional[StreamLog] = None
                    if not is_tombstone:
                        state = data.get("state", data)
                        # Deserialization can be comparatively expensive. Keep
                        # it outside the membership/state lock.
                        log = StreamLog.from_dict(state)

                    with self._lock:
                        known_streams = (
                            self.peer_snapshots.keys()
                            | self.peer_tombstones.keys()
                        )
                        if (stream_id not in known_streams
                                and len(known_streams)
                                >= self.MAX_PEER_STREAMS):
                            continue
                        existing_origins = self.peer_snapshots.get(
                            stream_id, {})
                        tombstone_origins = self.peer_tombstones.get(
                            stream_id, {})
                        known_origins = (
                            existing_origins.keys()
                            | tombstone_origins.keys()
                        )
                        if (nid not in known_origins
                                and len(known_origins)
                                >= self.MAX_ORIGINS_PER_STREAM):
                            continue

                        existing_snapshot = existing_origins.get(
                            nid, (None, 0))[1]
                        existing_tombstone = tombstone_origins.get(nid, 0)
                        if version <= max(
                                existing_snapshot, existing_tombstone):
                            continue

                        if is_tombstone:
                            existing_origins.pop(nid, None)
                            if not existing_origins:
                                self.peer_snapshots.pop(stream_id, None)
                            self.peer_tombstones.setdefault(
                                stream_id, {})[nid] = version
                            continue

                        assert log is not None
                        self.peer_snapshots.setdefault(
                            stream_id, {})[nid] = (log, version)
                        tombstone_origins.pop(nid, None)
                        if not tombstone_origins:
                            self.peer_tombstones.pop(stream_id, None)
                except Exception as e:
                    logger.warning(
                        "Failed to deserialize snapshot from %s for stream "
                        "%s, origin %s: %s",
                        sender_node_id, stream_id, nid, e)

    def begin_deletion(
            self, namespace: str, stream_id: str
    ) -> Tuple[str, float, Optional[float]]:
        """Reserve a bounded monotonic tombstone before deleting local state."""
        full_id = json.dumps([namespace, stream_id])
        version = time.time()
        with self._lock:
            previous = self.local_tombstones.get(full_id)
            if (previous is None
                    and len(self.local_tombstones)
                    >= self.max_local_tombstones):
                raise RuntimeError("local mesh tombstone capacity exhausted")
            committed = max(version, (previous or 0) + 1e-6)
            self.local_tombstones[full_id] = committed
            return full_id, committed, previous

    def rollback_deletion(
            self, stream_key: str, version: float,
            previous: Optional[float]) -> None:
        """Undo a reserved tombstone when durable deletion did not commit."""
        with self._lock:
            if self.local_tombstones.get(stream_key) != version:
                return
            if previous is None:
                self.local_tombstones.pop(stream_key, None)
            else:
                self.local_tombstones[stream_key] = previous

    def record_deletion(self, namespace: str, stream_id: str) -> Tuple[str, float]:
        """Create a monotonic local-origin tombstone for mesh propagation."""
        stream_key, version, _ = self.begin_deletion(namespace, stream_id)
        return stream_key, version

    def restore_local_tombstones(self, tombstones: Dict[str, float]) -> None:
        """Restore durable local-origin deletion versions at startup."""
        with self._lock:
            for stream_id, version in tombstones.items():
                if (not self._valid_stream_key(stream_id)
                        or not isinstance(version, (int, float))
                        or isinstance(version, bool)
                        or not math.isfinite(version)
                        or version < 0):
                    continue
                if (stream_id not in self.local_tombstones
                        and len(self.local_tombstones)
                        >= self.max_local_tombstones):
                    raise RuntimeError(
                        "stored mesh tombstones exceed configured capacity")
                self.local_tombstones[stream_id] = max(
                    float(version), self.local_tombstones.get(stream_id, 0))

    def has_peer_data(self, namespace: str, stream_id: str) -> bool:
        full_id = json.dumps([namespace, stream_id])
        with self._lock:
            return bool(self.peer_snapshots.get(full_id))

    def get_merged_stream(self, namespace: str, stream_id: str, local_stream: Optional[Any]) -> StreamLog:
        merged = local_stream.get_snapshot() if local_stream else StreamLog(deterministic=True)
        full_id = json.dumps([namespace, stream_id])
        with self._lock:
            if full_id in self.peer_snapshots:
                for node_id, (peer_log, _) in self.peer_snapshots[full_id].items():
                    merged.merge(peer_log)
        return merged

    def _http_post(self, url: str, payload: Dict[str, Any], fire_and_forget: bool = False) -> Optional[Dict[str, Any]]:
        parsed = urllib.parse.urlsplit(url)
        base_url = self._canonical_base_url(
            urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, "", "", "")))
        if base_url not in self._allowed_peer_origins:
            raise ValueError("Refusing request to a non-allowlisted peer origin")

        payload_bytes = self._json_bytes(payload)
        if len(payload_bytes) > self.max_payload_bytes:
            raise ValueError(
                "Gossip payload exceeds SKETCHLOG_MAX_MESH_PAYLOAD_BYTES")

        headers = {"Content-Type": "application/json"}
        if self.cluster_secret:
            headers["X-SketchLog-Cluster-Token"] = self.cluster_secret

        try:
            req = urllib.request.Request(url, data=payload_bytes, headers=headers, method="POST")
            class NoRedirects(urllib.request.HTTPRedirectHandler):
                def redirect_request(self, req: Any, fp: Any, code: int,
                                     msg: str, headers: Any,
                                     newurl: str) -> None:
                    return None

            opener = urllib.request.build_opener(NoRedirects)
            with opener.open(req, timeout=2.0) as response:
                if fire_and_forget:
                    return None
                body = response.read(self.max_payload_bytes + 1)
                if len(body) > self.max_payload_bytes:
                    raise ValueError(
                        "Gossip response exceeds "
                        "SKETCHLOG_MAX_MESH_PAYLOAD_BYTES")
                result: Dict[str, Any] = json.loads(body)
                if not isinstance(result, dict):
                    raise ValueError("Gossip response must be a JSON object")
                return result
        except Exception:
            raise
