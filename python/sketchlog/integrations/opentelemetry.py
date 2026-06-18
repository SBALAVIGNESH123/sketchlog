from typing import TYPE_CHECKING, Any, Iterable, Optional

try:
    from opentelemetry.metrics import (
        Meter,
        MeterProvider,
        CallbackOptions,
        Observation,
        get_meter_provider,
        set_meter_provider,
    )
    from opentelemetry.sdk.metrics import MeterProvider as SDKMeterProvider
    from opentelemetry.sdk.metrics.export import (
        PeriodicExportingMetricReader,
        ConsoleMetricExporter,
    )
    HAS_OPENTELEMETRY = True
except ImportError:
    HAS_OPENTELEMETRY = False
    class Meter: pass
    class CallbackOptions: pass
    class Observation: pass
    class MeterProvider: pass

if TYPE_CHECKING:
    from sketchlog import StreamLog, WindowedStreamLog, ThreadSafeStreamLog

class OpenTelemetryAdapter:
    """
    Bridges a SketchLog instance to an OpenTelemetry Meter using Asynchronous Instruments.
    
    Instead of actively pushing state to OTel, this registers callbacks that OTel's 
    MetricReader periodically invokes, ensuring zero memory duplication and correct temporality.
    """
    def __init__(self, streamlog: Any, meter: Meter):
        if not HAS_OPENTELEMETRY:
            raise ImportError("opentelemetry-api and opentelemetry-sdk must be installed")
        
        self.log = streamlog
        self.meter = meter

        # We need to know if the log is Windowed or not, because for Windowed, total_events
        # represents events IN THE CURRENT WINDOW (gauge), not a cumulative total.
        from sketchlog import WindowedStreamLog
        self.is_windowed = isinstance(streamlog, WindowedStreamLog)

        # Register Instruments
        self.meter.create_observable_gauge(
            name="sketchlog.latency",
            callbacks=[self._observe_latency],
            description="Approximate latency percentiles from DDSketch"
        )
        self.meter.create_observable_gauge(
            name="sketchlog.unique_count",
            callbacks=[self._observe_unique_count],
            description="Estimated number of unique items from HyperLogLog"
        )
        self.meter.create_observable_gauge(
            name="sketchlog.memory_kb",
            callbacks=[self._observe_memory_kb],
            description="Constant memory usage in KB across all sketches",
            unit="kB"
        )
        
        if self.is_windowed:
            self.meter.create_observable_gauge(
                name="sketchlog.events.total",
                callbacks=[self._observe_events],
                description="Number of events in the current window"
            )
        else:
            self.meter.create_observable_counter(
                name="sketchlog.events.total",
                callbacks=[self._observe_events],
                description="Total number of events processed"
            )

    def _get_stats(self):
        if hasattr(self.log, "_lock") and hasattr(self.log, "_log"):
            with self.log._lock:
                return self.log._log.stats(), self.log._log.p95()
        return self.log.stats(), self.log.p95()

    def _observe_latency(self, options: 'CallbackOptions') -> 'Iterable[Observation]':
        stats, p95 = self._get_stats()
        yield Observation(stats.latency_p50, {"quantile": "0.5"})
        yield Observation(p95, {"quantile": "0.95"})
        yield Observation(stats.latency_p99, {"quantile": "0.99"})
        yield Observation(stats.latency_p999, {"quantile": "0.999"})

    def _observe_unique_count(self, options: 'CallbackOptions') -> 'Iterable[Observation]':
        stats, _ = self._get_stats()
        yield Observation(stats.unique_count)

    def _observe_events(self, options: 'CallbackOptions') -> 'Iterable[Observation]':
        stats, _ = self._get_stats()
        yield Observation(stats.events)

    def _observe_memory_kb(self, options: 'CallbackOptions') -> 'Iterable[Observation]':
        stats, _ = self._get_stats()
        yield Observation(stats.memory_kb)


class SketchLogOTelPublisher:
    """
    High-level wrapper to quickly spin up OTLP export for a StreamLog.
    
    If no exporter is provided, defaults to OTLPMetricExporter (HTTP).
    """
    def __init__(self, streamlog: Any, exporter=None, export_interval_millis: int = 60000):
        if not HAS_OPENTELEMETRY:
            raise ImportError("opentelemetry packages are required for this feature")

        if exporter is None:
            try:
                from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
                exporter = OTLPMetricExporter()
            except ImportError:
                # Fallback to console if otlp package is missing, useful for testing
                exporter = ConsoleMetricExporter()

        self.reader = PeriodicExportingMetricReader(
            exporter,
            export_interval_millis=export_interval_millis
        )
        self.provider = SDKMeterProvider(metric_readers=[self.reader])
        
        # Set global provider if not already set
        try:
            set_meter_provider(self.provider)
        except Exception:
            pass # Global provider might already be set by another part of the application
            
        self.meter = self.provider.get_meter("sketchlog")
        self.adapter = OpenTelemetryAdapter(streamlog, self.meter)

    def shutdown(self):
        """Forces a flush and shuts down the periodic reader."""
        self.provider.shutdown()

