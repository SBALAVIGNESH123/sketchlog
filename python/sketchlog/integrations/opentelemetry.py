
from typing import TYPE_CHECKING, Any, Iterable, Optional

try:
    from opentelemetry.metrics import (  # type: ignore
        Meter,
        MeterProvider,
        CallbackOptions,
        Observation,
        get_meter_provider,
        set_meter_provider,
    )
    from opentelemetry.sdk.metrics import MeterProvider as SDKMeterProvider  # type: ignore
    from opentelemetry.sdk.metrics.export import (  # type: ignore
        PeriodicExportingMetricReader,
        ConsoleMetricExporter,
    )
    HAS_OPENTELEMETRY = True
except ImportError:
    HAS_OPENTELEMETRY = False
    class _DummyType:
        def __call__(self, *args: Any, **kwargs: Any) -> Any: return None
        def __getattr__(self, name: str) -> Any: return None
    
    Meter: Any = _DummyType()  # type: ignore[no-redef]
    CallbackOptions: Any = _DummyType()  # type: ignore[no-redef]
    Observation: Any = _DummyType()  # type: ignore[no-redef]
    MeterProvider: Any = _DummyType()  # type: ignore[no-redef]
    SDKMeterProvider: Any = _DummyType()  # type: ignore[no-redef]
    PeriodicExportingMetricReader: Any = _DummyType()  # type: ignore[no-redef]
    ConsoleMetricExporter: Any = _DummyType()  # type: ignore[no-redef]
    set_meter_provider: Any = _DummyType()  # type: ignore[no-redef]

if TYPE_CHECKING:
    from sketchlog import StreamLog, WindowedStreamLog, ThreadSafeStreamLog

class OpenTelemetryAdapter:
    def __init__(self, streamlog: Any, meter: Any, export_events: Optional[Iterable[str]] = None):
        if not HAS_OPENTELEMETRY:
            raise ImportError("opentelemetry-api and opentelemetry-sdk must be installed")
        
        self.log = streamlog
        self.meter = meter
        self.export_events = list(export_events) if export_events else []

        from sketchlog import WindowedStreamLog
        self.is_windowed = isinstance(streamlog, WindowedStreamLog)

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
            if self.export_events:
                self.meter.create_observable_gauge(
                    name="sketchlog.events.frequency",
                    callbacks=[self._observe_event_frequencies],
                    description="Frequency of specific events in the current window"
                )
        else:
            self.meter.create_observable_counter(
                name="sketchlog.events.total",
                callbacks=[self._observe_events],
                description="Total number of events processed"
            )
            if self.export_events:
                self.meter.create_observable_counter(
                    name="sketchlog.events.frequency",
                    callbacks=[self._observe_event_frequencies],
                    description="Total frequency of specific events"
                )

    def _get_stats(self):
        from sketchlog import WindowedStreamLog, Stats, StreamLog
        
        if isinstance(self.log, WindowedStreamLog):
            with self.log._lock:
                self.log._rotate()
                active = self.log._active_buckets()
                if not active:
                    return Stats(0, self.log.memory_bytes(), self.log.memory_kb(), 0.0, 0.0, 0.0, 0), 0.0, self.log
                merged = StreamLog(**self.log._sk_kwargs)
                for bucket in active:
                    merged.merge(bucket)
                
                return merged.stats(), merged.p95(), merged

        if hasattr(self.log, "_lock") and hasattr(self.log, "_log"):
            with self.log._lock:
                return self.log._log.stats(), self.log._log.p95(), self.log._log
                
        return self.log.stats(), self.log.p95(), self.log

    def _observe_latency(self, options: Any) -> Iterable[Any]:
        stats, p95, _ = self._get_stats()
        yield Observation(stats.latency_p50, {"quantile": "0.5"})
        yield Observation(p95, {"quantile": "0.95"})
        yield Observation(stats.latency_p99, {"quantile": "0.99"})
        yield Observation(stats.latency_p999, {"quantile": "0.999"})

    def _observe_unique_count(self, options: Any) -> Iterable[Any]:
        stats, _, _ = self._get_stats()
        yield Observation(stats.unique_count)

    def _observe_events(self, options: Any) -> Iterable[Any]:
        stats, _, _ = self._get_stats()
        yield Observation(stats.events)

    def _observe_event_frequencies(self, options: Any) -> Iterable[Any]:
        _, _, snapshot_log = self._get_stats()
        for event_name in self.export_events:
            count = snapshot_log.event_count(event_name)
            yield Observation(count, {"event": event_name})

    def _observe_memory_kb(self, options: Any) -> Iterable[Any]:
        stats, _, _ = self._get_stats()
        yield Observation(stats.memory_kb)


class SketchLogOTelPublisher:
    def __init__(self, streamlog: Any, exporter: Any=None, export_interval_millis: int = 60000, export_events: Optional[Iterable[str]] = None):
        if not HAS_OPENTELEMETRY:
            raise ImportError("opentelemetry packages are required for this feature")

        if exporter is None:
            try:
                from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter  # type: ignore
                exporter = OTLPMetricExporter()
            except ImportError:
                exporter = ConsoleMetricExporter()

        self.reader = PeriodicExportingMetricReader(
            exporter,
            export_interval_millis=export_interval_millis
        )
        self.provider = SDKMeterProvider(metric_readers=[self.reader])
        
        try:
            set_meter_provider(self.provider)
        except Exception:
            pass 
            
        self.meter = self.provider.get_meter("sketchlog")
        self.adapter = OpenTelemetryAdapter(streamlog, self.meter, export_events=export_events)

    def shutdown(self):
        self.provider.shutdown()

