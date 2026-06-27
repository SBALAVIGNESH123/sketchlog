import sys
import pytest
from unittest.mock import patch, MagicMock

from sketchlog import StreamLog
from sketchlog.ebpf.collector import EBPFCollector

@pytest.fixture
def mock_bcc():
    with patch("sketchlog.ebpf.collector.HAS_BCC", True), \
         patch("sketchlog.ebpf.collector.BPF", create=True) as mock_bpf_cls:
        
        # Setup mock BPF instance
        mock_bpf_instance = MagicMock()
        mock_bpf_cls.return_value = mock_bpf_instance
        
        # Setup mock tables
        mock_bucket_boundaries = MagicMock()
        mock_bucket_counts = MagicMock()
        mock_config_map = MagicMock()
        
        def get_table(name):
            if name == "bucket_boundaries":
                return mock_bucket_boundaries
            elif name == "bucket_counts":
                return mock_bucket_counts
            elif name == "config_map":
                return mock_config_map
            return MagicMock()
            
        mock_bpf_instance.get_table.side_effect = get_table
        
        yield {
            "bpf_cls": mock_bpf_cls,
            "bpf_instance": mock_bpf_instance,
            "bucket_boundaries": mock_bucket_boundaries,
            "bucket_counts": mock_bucket_counts,
            "config_map": mock_config_map
        }

@patch("sys.platform", "linux")
def test_ebpf_collector_initialization(mock_bcc):
    log = StreamLog(relative_accuracy=0.01)
    
    # 1. Initialize collector
    collector = EBPFCollector(log, min_ns=1000, max_ns=10_000_000, poll_interval_sec=0.1)
    
    # 2. Verify compilation and attachment
    mock_bcc["bpf_cls"].assert_called_once()
    mock_bpf = mock_bcc["bpf_instance"]
    
    # verify kprobes attached
    mock_bpf.attach_kprobe.assert_any_call(event="tcp_sendmsg", fn_name="trace_tcp_sendmsg")
    mock_bpf.attach_kprobe.assert_any_call(event="tcp_cleanup_rbuf", fn_name="trace_tcp_cleanup_rbuf")
    
    # 3. Verify bucket mapping
    # min_ns = 1000, max_ns = 10_000_000, multiplier ~ 50.25 for 0.01 accuracy
    # total buckets should be roughly log(10000) * 50.25 = 461
    assert len(collector._mapping) > 400
    assert len(collector._mapping) < 500
    
    # First boundary should be >= 1000
    first_idx = collector._mapping[0]
    import math
    alpha = log._relative_accuracy
    gamma = (1.0 + alpha) / (1.0 - alpha)
    multiplier = 1.0 / math.log(gamma)
    
    first_bound = int(math.exp(first_idx / multiplier))
    assert first_bound >= 1000

@patch("sys.platform", "linux")
def test_ebpf_collector_syncing(mock_bcc):
    log = StreamLog(relative_accuracy=0.01)
    collector = EBPFCollector(log, min_ns=1000, max_ns=100_000, poll_interval_sec=0.01)
    
    # Mock kernel counters
    # Let's say bucket index 0 (the first one in the mapping) has 5 events
    mock_counts = mock_bcc["bucket_counts"]
    
    def mock_get_value(idx):
        if idx == 0:
            return [2, 3] # simulate 2 cpus with 2 and 3 counts
        return [0, 0]
        
    mock_counts.GetValue.side_effect = mock_get_value
    
    # Start thread
    collector.start()
    
    import time
    # wait for at least one poll
    time.sleep(0.05)
    
    # Stop thread
    collector.stop()
    
    # Check that the counts were merged into StreamLog
    assert log.total_events >= 5
    # The actual latency p99 should be roughly the first bound
    assert log.p99() > 0

def test_ebpf_collector_noop_on_windows():
    with patch("sys.platform", "win32"):
        log = StreamLog()
        collector = EBPFCollector(log)
        assert collector.bpf is None
        
        # Starting should do nothing
        collector.start()
        assert collector._thread is None
