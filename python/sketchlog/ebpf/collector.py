import math
import os
import sys
import threading
import time
import ctypes
from typing import Optional, List, Dict, Any

from sketchlog.facade import StreamLog

# Optional dependency check for BCC
try:
    from bcc import BPF  # type: ignore
    HAS_BCC = True
except ImportError:
    HAS_BCC = False
    BPF: Any = None

class EBPFCollector:
    """
    Zero-code Universal Collector using eBPF to measure TCP inter-packet latencies
    in kernel space and sync directly into a StreamLog DDSketch.
    """

    def __init__(self, log: StreamLog, min_ns: int = 1000, max_ns: int = 60_000_000_000, poll_interval_sec: float = 1.0):
        self.log = log
        self.poll_interval_sec = poll_interval_sec
        self.bpf: Any = None
        self._thread = None
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
