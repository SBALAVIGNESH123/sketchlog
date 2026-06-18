import pytest
from sketchlog import StreamLog, WindowedStreamLog

try:
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader
    from sketchlog.integrations.opentelemetry import OpenTelemetryAdapter, SketchLogOTelPublisher, HAS_OPENTELEMETRY
except ImportError:
    HAS_OPENTELEMETRY = False

@pytest.mark.skipif(not HAS_OPENTELEMETRY, reason="opentelemetry-sdk not installed")
def test_opentelemetry_adapter_streamlog():
    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    meter = provider.get_meter("test_meter")

    log = StreamLog()
    log.add_batch([1.0, 2.0, 3.0, 4.0, 5.0])
    log.add_unique("user1")
    log.add_unique("user2")

    adapter = OpenTelemetryAdapter(log, meter)

    # Force a collect
    metrics_data = reader.get_metrics_data()
    assert metrics_data is not None
    resource_metrics = metrics_data.resource_metrics[0]
    scope_metrics = resource_metrics.scope_metrics[0]

    metrics = {m.name: m for m in scope_metrics.metrics}
    
    assert "sketchlog.latency" in metrics
    assert "sketchlog.unique_count" in metrics
    assert "sketchlog.events.total" in metrics
    assert "sketchlog.memory_kb" in metrics

    # Check unique count
    uc_metric = metrics["sketchlog.unique_count"]
    uc_data = list(uc_metric.data.data_points)[0]
    assert uc_data.value == 2

    # Check events
    ev_metric = metrics["sketchlog.events.total"]
    ev_data = list(ev_metric.data.data_points)[0]
    assert ev_data.value == 5

    # Check quantiles
    lat_metric = metrics["sketchlog.latency"]
    lat_points = list(lat_metric.data.data_points)
    assert len(lat_points) == 4
    for point in lat_points:
        if point.attributes["quantile"] == "0.5":
            assert point.value > 0.0

@pytest.mark.skipif(not HAS_OPENTELEMETRY, reason="opentelemetry-sdk not installed")
def test_opentelemetry_publisher():
    log = StreamLog()
    log.add_latency(42.0)

    # Use a dummy exporter
    from opentelemetry.sdk.metrics.export import ConsoleMetricExporter
    publisher = SketchLogOTelPublisher(log, exporter=ConsoleMetricExporter(), export_interval_millis=1000)
    publisher.shutdown()

