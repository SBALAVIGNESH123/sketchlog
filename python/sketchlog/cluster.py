import logging
import threading
import time
import json
import urllib.request
import urllib.error
import urllib.parse
from typing import Dict, List, Optional, Any, Tuple
from threading import Lock

from sketchlog.facade import StreamLog
from sketchlog.concurrent import ThreadSafeStreamLog

logger = logging.getLogger(__name__)

class ClusterManager:
    def __init__(self, node_id: str, peers: List[str], registry: Any, sync_interval: float = 5.0, heartbeat_timeout: float = 60.0, cluster_secret: Optional[str] = None):
        self.node_id = node_id
        self.peers = []
        for p in peers:
            if not p:
                continue
            parsed = urllib.parse.urlparse(p)
            if parsed.scheme in ("http", "https") and parsed.netloc:
                self.peers.append(p.strip())
            else:
                logger.warning(f"Ignoring invalid peer URL: {p}")
        self.registry = registry
        self.sync_interval = sync_interval
        self.heartbeat_timeout = heartbeat_timeout
        self.cluster_secret = cluster_secret

        # peer_snapshots[stream_id][node_id] = (StreamLog, last_seen_time)
        self.peer_snapshots: Dict[str, Dict[str, Tuple[StreamLog, float]]] = {}
        self._lock = Lock()

        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if not self.peers:
            logger.info("No peers configured. ClusterManager will not start.")
            return
        logger.info(f"Starting ClusterManager as node '{self.node_id}' with {len(self.peers)} peers: {self.peers}")
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=2.0)

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.broadcast_snapshots()
                self.evict_dead_nodes()
            except Exception as e:
                logger.error(f"Unexpected error in gossip loop: {e}")
            self._stop_event.wait(self.sync_interval)

    def broadcast_snapshots(self) -> None:
        if not self.peers:
            return

        snapshots = {}
        # We need a point-in-time snapshot of every stream in the registry.
        for stream_id, ts_log in self.registry.snapshot_items():
            try:
                data = ts_log.get_snapshot().to_dict()
                snapshots[stream_id] = data
            except NotImplementedError:
                logger.error(f"Cannot serialize stream {stream_id} - Clustering requires deterministic=True (Python backend)")
                continue
            except Exception as e:
                logger.error(f"Failed to serialize stream {stream_id}: {e}")
                continue

        if not snapshots:
            return

        if len(snapshots) > 2000:
            logger.error("Too many streams to sync (>2000). Skipping to prevent memory exhaustion.")
            return

        payload = {
            "node_id": self.node_id,
            "timestamp": time.time(),
            "streams": snapshots
        }

        # Estimate payload size before full serialization to prevent OOM
        size_estimate = 0
        for chunk in json.JSONEncoder().iterencode(payload):
            size_estimate += len(chunk.encode('utf-8'))
            if size_estimate > 50_000_000:
                break
                
        if size_estimate > 50_000_000:
            logger.error("Gossip payload too large (>50MB). Skipping sync to prevent memory exhaustion.")
            return

        payload_bytes = json.dumps(payload).encode('utf-8')

        headers = {"Content-Type": "application/json"}
        if self.cluster_secret:
            headers["X-SketchLog-Cluster-Token"] = self.cluster_secret

        for peer in self.peers:
            url = f"{peer.rstrip('/')}/_internal/sync"
            try:
                # Fire and forget. We use a short timeout because gossip should not block.
                req = urllib.request.Request(url, data=payload_bytes, headers=headers, method="POST")
                with urllib.request.urlopen(req, timeout=2.0) as _:
                    pass
            except Exception as e:
                logger.debug(f"Failed to sync with peer {url}: {e}")

    def receive_snapshot(self, node_id: str, streams_data: Dict[str, Dict[str, Any]], timestamp: Optional[float] = None) -> None:
        if node_id == self.node_id:
            return

        current_time = time.time()
        # Fallback to current_time if timestamp is missing to support old peers
        version_ts = timestamp if timestamp is not None else current_time

        with self._lock:
            for stream_id, data in streams_data.items():
                if stream_id not in self.peer_snapshots:
                    # Enforce global maximum of 10000 remote streams to prevent memory exhaustion
                    if len(self.peer_snapshots) >= 10000:
                        logger.warning("Global remote stream cap reached, dropping new stream.")
                        continue
                    self.peer_snapshots[stream_id] = {}

                # Cap the maximum number of peers per stream to prevent memory exhaustion
                # from an unbounded number of spoofed node_ids.
                if len(self.peer_snapshots[stream_id]) >= 50 and node_id not in self.peer_snapshots[stream_id]:
                    continue

                # Reject stale snapshots
                if node_id in self.peer_snapshots[stream_id]:
                    _, stored_ts = self.peer_snapshots[stream_id][node_id]
                    if version_ts < stored_ts:
                        continue

                try:
                    log = StreamLog.from_dict(data)
                    self.peer_snapshots[stream_id][node_id] = (log, version_ts)
                except Exception as e:
                    logger.warning(f"Failed to deserialize snapshot from {node_id} for stream {stream_id}: {e}")

    def evict_dead_nodes(self) -> None:
        current_time = time.time()
        with self._lock:
            for stream_id in list(self.peer_snapshots.keys()):
                nodes = self.peer_snapshots[stream_id]
                dead_nodes = [n for n, (_, last_seen) in nodes.items() if current_time - last_seen > self.heartbeat_timeout]
                for n in dead_nodes:
                    del nodes[n]
                    logger.info(f"Evicted dead node '{n}' from stream '{stream_id}' due to heartbeat timeout")
                if not nodes:
                    del self.peer_snapshots[stream_id]

    def get_merged_stream(self, stream_id: str, local_stream: Optional[Any]) -> StreamLog:
        # Use get_snapshot() to safely copy the local stream if it exists
        merged = local_stream.get_snapshot() if local_stream else StreamLog(deterministic=True)

        with self._lock:
            if stream_id in self.peer_snapshots:
                for node_id, (peer_log, _) in self.peer_snapshots[stream_id].items():
                    merged.merge(peer_log)
        return merged

    def has_peer_data(self, stream_id: str) -> bool:
        with self._lock:
            return stream_id in self.peer_snapshots and bool(self.peer_snapshots[stream_id])
