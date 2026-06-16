import pytest
import time
import urllib.request
from sketchlog import StreamLog
from sketchlog.integrations.prometheus import PrometheusExporter

def test_prometheus_exporter():
    log = StreamLog()
    exporter = PrometheusExporter(log)
    exporter.start(port=9099)  # Use different port to avoid conflicts
    
    # Add some data
    log.add_latency(10.0)
    log.add_latency(20.0)
    log.add_unique("user_1")
    log.add_unique("user_2")
    log.add_event("login")
    
    time.sleep(0.1) # Give server time to start
    
    # Fetch metrics
    req = urllib.request.Request("http://127.0.0.1:9099/metrics")
    with urllib.request.urlopen(req) as response:
        content = response.read().decode('utf-8')
        
    assert "sketchlog_latency_seconds{quantile=\"0.95\"}" in content
    assert "sketchlog_unique_count 2" in content
    assert "sketchlog_total_events 3" in content
    assert "sketchlog_memory_kb" in content
    assert "sketchlog_ingest_rate" in content
    
    exporter.stop()
