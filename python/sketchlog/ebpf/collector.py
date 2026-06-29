import math
import os
import sys
import threading
import time
import ctypes
import argparse
import json
import signal
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional, List, Dict, Any, Union

from sketchlog.facade import StreamLog
from sketchlog.concurrent import ThreadSafeStreamLog

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    BPF: Any = None
    HAS_BCC: bool = False
else:
    try:
        from bcc import BPF
        HAS_BCC = True
    except ImportError:
        HAS_BCC = False
        BPF = None

class EBPFCollector:
    """
    Zero-code Universal Collector using eBPF to measure TCP inter-packet latencies
    in kernel space and sync directly into a StreamLog DDSketch.
    """

    def __init__(self, log: Union[StreamLog, ThreadSafeStreamLog], min_ns: int = 1000, max_ns: int = 60_000_000_000, poll_interval_sec: float = 1.0):
        self.log = log
        self.poll_interval_sec = poll_interval_sec
        self.bpf: Any = None
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._mapping: List[int] = []

        if sys.platform != 'linux':
            # Skip actual eBPF init on non-Linux platforms for dev/test compatibility
            return

        if not HAS_BCC:
            raise RuntimeError("The 'bcc' python package is required for eBPF integration.")

        self._init_ebpf(min_ns, max_ns)

    def _init_ebpf(self, min_ns: int, max_ns: int) -> None:
        """Compile and attach the eBPF program, and load the bucket boundaries."""
        if min_ns <= 0 or max_ns <= 0 or max_ns <= min_ns:
            raise ValueError(f'Invalid min_ns ({min_ns}) and max_ns ({max_ns})')

        # 1. Calculate boundaries based on StreamLog's alpha
        alpha = self.log.relative_accuracy
        gamma = (1.0 + alpha) / (1.0 - alpha)
        multiplier = 1.0 / math.log(gamma)
        self._multiplier = multiplier

        min_idx = math.ceil(math.log(min_ns) * multiplier)
        max_idx = math.ceil(math.log(max_ns) * multiplier)

        # We enforce a max of 2048 buckets (as defined in C code MAX_BUCKETS)
        num_buckets = min(max_idx - min_idx + 1, 2048)

        # 2. Compile BPF
        bpf_source_path = os.path.join(os.path.dirname(__file__), "bpf_code.c")
        with open(bpf_source_path, "r") as f:
            bpf_text = f.read()

        self.bpf = BPF(text=bpf_text)

        # 3. Attach kprobes
        self.bpf.attach_kprobe(event="tcp_sendmsg", fn_name="trace_tcp_sendmsg")
        self.bpf.attach_kprobe(event="tcp_cleanup_rbuf", fn_name="trace_tcp_cleanup_rbuf")
        self.bpf.attach_kprobe(event="tcp_close", fn_name="trace_tcp_close")

        # 4. Load boundaries into BPF_ARRAY
        bucket_boundaries = self.bpf.get_table("bucket_boundaries")
        for i in range(num_buckets):
            actual_bucket_idx = min_idx + i
            # Calculate upper bound in nanoseconds for this bucket
            bound_val = int(math.exp(actual_bucket_idx / multiplier))
            bucket_boundaries[ctypes.c_int(i)] = ctypes.c_uint64(bound_val)
            self._mapping.append(actual_bucket_idx)

        # Set config map to activate the kernel eBPF recording
        config_map = self.bpf.get_table("config_map")
        config_map[ctypes.c_int(0)] = ctypes.c_uint32(num_buckets)

    def start(self) -> None:
        """Start the background syncing thread."""
        if sys.platform != 'linux':
            print("Warning: eBPF Collector is a no-op on non-Linux platforms.")
            return

        if self._thread is not None and self._thread.is_alive():
            return

        self._stop_event.clear()
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop the background syncing thread."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5.0)
            if self._thread.is_alive():
                print("Warning: EBPFCollector polling thread did not exit cleanly", flush=True)
        if self.bpf:
            self.bpf.cleanup()

    def _poll_loop(self) -> None:
        """Periodically read per-cpu counters, merge into userspace StreamLog, and reset kernel array."""
        bucket_counts = self.bpf.get_table("bucket_counts")
        num_buckets = len(self._mapping)

        while not self._stop_event.is_set():
            self._stop_event.wait(self.poll_interval_sec)

            # Read and flush kernel buckets
            for i in range(num_buckets):
                key = ctypes.c_int(i)
                try:
                    cpu_values = bucket_counts[key]
                    total_count = sum(v.value if hasattr(v, 'value') else v for v in cpu_values)
                except KeyError:
                    continue

                if total_count > 0:
                    actual_bucket_idx = self._mapping[i]
                    gamma = (1.0 + self.log.relative_accuracy) / (1.0 - self.log.relative_accuracy)
                    bound_val = float((2.0 / (1.0 + gamma)) * (gamma ** actual_bucket_idx))

                    # Add to StreamLog using add_batch for backend-agnostic support
                    batch_size = min(total_count, 1000)
                    batch = [bound_val] * batch_size
                    for _ in range(total_count // batch_size):
                        self.log.add_batch(batch)
                    if total_count % batch_size:
                        self.log.add_batch([bound_val] * (total_count % batch_size))

                    # Zero the kernel per-cpu counters
                    bucket_counts[ctypes.c_int(i)] = bucket_counts.Leaf()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Collect bounded TCP timing sketches with Linux eBPF")
    parser.add_argument("--namespace", default="default")
    parser.add_argument("--stream-id", default="universal-collector")
    parser.add_argument("--server", required=True)
    parser.add_argument("--auth-token", default=os.environ.get("SKETCHLOG_AUTH_TOKEN"))
    parser.add_argument("--flush-interval", type=float, default=5.0)
    parser.add_argument("--poll-interval", type=float, default=1.0)
    parser.add_argument("--health-port", type=int, default=9091)
    args = parser.parse_args()

    if sys.platform != "linux":
        parser.error("sketchlog-collector requires Linux")
    if hasattr(os, "geteuid") and os.geteuid() != 0:
        parser.error("sketchlog-collector requires root/CAP_BPF privileges")
    if not HAS_BCC:
        parser.error("BCC Python bindings are not installed")
    if args.flush_interval <= 0 or args.poll_interval <= 0:
        parser.error("flush and poll intervals must be positive")
    server_url = urllib.parse.urlsplit(args.server)
    if (
        server_url.scheme not in ("http", "https")
        or not server_url.hostname
        or server_url.username
        or server_url.password
        or server_url.path not in ("", "/")
        or server_url.query
        or server_url.fragment
    ):
        parser.error("--server must be an HTTP(S) origin without credentials or a path")

    log = ThreadSafeStreamLog()
    collector = EBPFCollector(log, poll_interval_sec=args.poll_interval)
    stop_event = threading.Event()
    last_export_error: List[Optional[str]] = [None]

    class HealthHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path not in ("/health", "/ready"):
                self.send_error(404)
                return
            ready = collector._thread is not None and collector._thread.is_alive()
            if self.path == "/ready" and last_export_error[0]:
                ready = False
            body = json.dumps({
                "status": "ready" if ready else "degraded",
                "buffered_events": log.total_events,
                "last_export_error": last_export_error[0],
            }).encode()
            self.send_response(200 if ready else 503)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format: str, *_args: Any) -> None:
            return

    health_server = ThreadingHTTPServer(("127.0.0.1", args.health_port), HealthHandler)
    health_thread = threading.Thread(
        target=health_server.serve_forever, daemon=True)
    endpoint = (
        f"{args.server.rstrip('/')}/v1/namespaces/"
        f"{urllib.parse.quote(args.namespace, safe='')}/streams/"
        f"{urllib.parse.quote(args.stream_id, safe='')}/merge"
    )

    def request_stop(_signum: int, _frame: Any) -> None:
        stop_event.set()

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    collector.start()
    health_thread.start()
    try:
        while not stop_event.wait(args.flush_interval):
            snapshot = log.drain()
            if snapshot.total_events == 0:
                continue
            request = urllib.request.Request(
                endpoint,
                data=json.dumps({"state": snapshot.to_dict()}).encode(),
                headers={
                    "Content-Type": "application/json",
                    **({"X-SketchLog-Auth-Token": args.auth_token}
                       if args.auth_token else {}),
                },
                method="POST",
            )
            try:
                # The base origin is validated above; only quoted namespace and
                # stream path segments are appended.
                with urllib.request.urlopen(  # nosec B310
                        request, timeout=10) as response:
                    if response.status != 202:
                        raise RuntimeError(f"unexpected HTTP {response.status}")
                last_export_error[0] = None
            except (OSError, RuntimeError) as exc:
                log.merge(snapshot)
                last_export_error[0] = str(exc)
    finally:
        collector.stop()
        health_server.shutdown()
        health_server.server_close()
        health_thread.join(timeout=5)
    return 0
