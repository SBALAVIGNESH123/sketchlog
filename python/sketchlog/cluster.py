import logging
import threading
import time
import json
import urllib.request
import urllib.error
from typing import Dict, List, Optional, Any, Tuple
from threading import Lock

from sketchlog.facade import StreamLog
from sketchlog.concurrent import ThreadSafeStreamLog

logger = logging.getLogger(__name__)

class ClusterManager:
    def __init__(self, node_id: str, peers: List[str], registry: Any, sync_interval: float = 5.0, heartbeat_timeout: float = 60.0, cluster_secret: Optional[str] = None):
        self.node_id = node_id
        # Expecting peers to be a list of base URLs like ["http://node2:8000", "http://node3:8000"]
        self.peers = [p for p in peers if p]
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
            self.broadcast_snapshots()
            self.evict_dead_nodes()
            self._stop_event.wait(self.sync_interval)

    def broadcast_snapshots(self) -> None:
        if not self.peers:
            return

        snapshots = {}
        # We need a point-in-time snapshot of every stream in the registry.
        for stream_id, ts_log in self.registry.snapshot_items():
            try:
                with ts_log._lock:
                    data = ts_log._log.to_dict()
                snapshots[stream_id] = data
            except NotImplementedError:
                logger.error(f"Cannot serialize stream {stream_id} - Clustering requires deterministic=True (Python backend)")
                return
            except Exception as e:
                logger.error(f"Failed to serialize stream {stream_id}: {e}")

        if not snapshots:
            return

        payload = {
            "node_id": self.node_id,
            "streams": snapshots
        }

        headers = {"Content-Type": "application/json"}
        if self.cluster_secret:
            headers["X-SketchLog-Cluster-Token"] = self.cluster_secret

        for peer in self.peers:
            url = f"{peer.rstrip('/')}/_internal/sync"
            try:
                # Fire and forget. We use a short timeout because gossip should not block.
                req = urllib.request.Request(url, data=json.dumps(payload).encode('utf-8'), headers=headers, method="POST")
                with urllib.request.urlopen(req, timeout=2.0) as _:
                    pass
            except Exception as e:
                logger.debug(f"Failed to sync with peer {url}: {e}")

    def receive_snapshot(self, node_id: str, streams_data: Dict[str, Dict[str, Any]]) -> None:
        current_time = time.time()
        with self._lock:
            for stream_id, data in streams_data.items():
                if stream_id not in self.peer_snapshots:
                    self.peer_snapshots[stream_id] = {}

                # Cap the maximum number of peers per stream to prevent memory exhaustion
                # from an unbounded number of spoofed node_ids.
                if len(self.peer_snapshots[stream_id]) >= 50 and node_id not in self.peer_snapshots[stream_id]:
                    continue

                try:
                    log = StreamLog.from_dict(data)
                    self.peer_snapshots[stream_id][node_id] = (log, current_time)
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

    def get_merged_stream(self, stream_id: str, local_log: Optional[ThreadSafeStreamLog]) -> StreamLog:
        """Returns the merged StreamLog for a stream."""
        # Create a fresh StreamLog to hold the merge
        merged = StreamLog(deterministic=True)

        # Merge local data
        if local_log:
            with local_log._lock:
                merged.merge(local_log._log)

        # Merge peer data
        with self._lock:
            peer_data = self.peer_snapshots.get(stream_id, {})
            for log, _ in peer_data.values():
                merged.merge(log)

        return merged
