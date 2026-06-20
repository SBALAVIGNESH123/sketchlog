
import time
import urllib.request
from sketchlog import ThreadSafeStreamLog
from sketchlog.integrations.prometheus import PrometheusExporter

def test_prometheus_exporter():
    log = ThreadSafeStreamLog()
    exporter = PrometheusExporter(log)
    exporter.start(port=0)  # Use ephemeral port

    # Give server time to bind and get the actual port
    time.sleep(0.1)
    port = exporter._server.server_port

    try:
        # Add some data
        log.add_latency(10.0)
        log.add_latency(20.0)
        log.add_unique("user_1")
        log.add_unique("user_2")
        log.add_event("login")

        # Fetch metrics
        req = urllib.request.Request(f"http://127.0.0.1:{port}/metrics")
        with urllib.request.urlopen(req) as response:
            content = response.read().decode('utf-8')

        assert "sketchlog_latency{quantile=\"0.95\"}" in content
        assert "sketchlog_unique_count 2" in content
        assert "sketchlog_total_events 3" in content
        assert "sketchlog_memory_kb" in content

    finally:
        exporter.stop()
