import threading
from typing import TYPE_CHECKING, Optional, Any
from wsgiref.simple_server import make_server, WSGIRequestHandler

if TYPE_CHECKING:
    from sketchlog import ThreadSafeStreamLog

class _NoLoggingWSGIRequestHandler(WSGIRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        pass

class PrometheusExporter:
    """
    Exposes StreamLog metrics to Prometheus.
    
    Provides a lightweight HTTP server that formats the current
    state of a StreamLog instance into the Prometheus text-based format.
    
    Usage:
        from sketchlog import ThreadSafeStreamLog
        from sketchlog.integrations.prometheus import PrometheusExporter
        
        log = ThreadSafeStreamLog()
        exporter = PrometheusExporter(log)
        exporter.start(port=9090)
    """
    
    def __init__(self, streamlog: "ThreadSafeStreamLog"):
        self.log = streamlog
        self._server: Optional[Any] = None
        self._thread: Optional[threading.Thread] = None
    
    def _generate_metrics(self) -> str:
        """Generates Prometheus-formatted metrics."""
        
        if hasattr(self.log, "_lock") and hasattr(self.log, "_log"):
            with self.log._lock:
                stats = self.log._log.stats()
                p95 = self.log._log.p95()
        else:
            stats = self.log.stats()
            p95 = self.log.p95()
            
        lines = [
            "# HELP sketchlog_latency Approximate latency percentiles from DDSketch",
            "# TYPE sketchlog_latency gauge",
            f'sketchlog_latency{{quantile="0.5"}} {stats.latency_p50}',
            f'sketchlog_latency{{quantile="0.95"}} {p95}',
            f'sketchlog_latency{{quantile="0.99"}} {stats.latency_p99}',
            f'sketchlog_latency{{quantile="0.999"}} {stats.latency_p999}',
            "",
            "# HELP sketchlog_unique_count Estimated number of unique items from HyperLogLog",
            "# TYPE sketchlog_unique_count gauge",
            f'sketchlog_unique_count {stats.unique_count}',
            "",
            "# HELP sketchlog_total_events Total number of events processed",
            "# TYPE sketchlog_total_events counter",
            f'sketchlog_total_events {stats.events}',
            "",
            "# HELP sketchlog_memory_kb Constant memory usage in KB across all sketches",
            "# TYPE sketchlog_memory_kb gauge",
            f'sketchlog_memory_kb {stats.memory_kb}'
        ]
        return "\n".join(lines) + "\n"

    def _wsgi_app(self, environ: Any, start_response: Any) -> Any:
        if environ.get("PATH_INFO") == "/metrics":
            status = '200 OK'
            metrics = self._generate_metrics().encode('utf-8')
            headers = [
                ('Content-Type', 'text/plain; version=0.0.4; charset=utf-8'),
                ('Content-Length', str(len(metrics)))
            ]
            start_response(status, headers)
            return [metrics]
        
        status = '404 Not Found'
        headers = [('Content-Type', 'text/plain')]
        start_response(status, headers)
        return [b"Not Found\n"]

    def start(self, port: int = 9090, host: str = "0.0.0.0") -> None:
        """Start the Prometheus exporter HTTP server on a background thread."""
        if self._server is not None:
            return
            
        self._server = make_server(host, port, self._wsgi_app, handler_class=_NoLoggingWSGIRequestHandler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        if self._thread is not None:
            self._thread.start()

    def stop(self) -> None:
        """Stop the exporter server."""
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            if self._thread is not None:
                self._thread.join()
            self._server = None
            self._thread = None
