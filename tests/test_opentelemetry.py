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
    log.add_event("login", 3)

    adapter = OpenTelemetryAdapter(log, meter, export_events=["login", "checkout"])

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
    assert "sketchlog.events.frequency" in metrics

    # Check unique count
    uc_metric = metrics["sketchlog.unique_count"]
    uc_data = list(uc_metric.data.data_points)[0]
    assert uc_data.value == 2

    # Check events
    ev_metric = metrics["sketchlog.events.total"]
    ev_data = list(ev_metric.data.data_points)[0]
    assert ev_data.value == 8

    # Check quantiles
    lat_metric = metrics["sketchlog.latency"]
    lat_points = list(lat_metric.data.data_points)
    assert len(lat_points) == 4
    for point in lat_points:
        if point.attributes["quantile"] == "0.5":
            assert point.value > 0.0

    # Check event frequencies
    freq_metric = metrics["sketchlog.events.frequency"]
    freq_points = list(freq_metric.data.data_points)
    assert len(freq_points) == 2
    freq_dict = {p.attributes["event"]: p.value for p in freq_points}
    assert freq_dict["login"] == 3
    assert freq_dict["checkout"] == 0

@pytest.mark.skipif(not HAS_OPENTELEMETRY, reason="opentelemetry-sdk not installed")
def test_opentelemetry_publisher():
    log = StreamLog()
    log.add_latency(42.0)

    # Use a dummy exporter
    from opentelemetry.sdk.metrics.export import ConsoleMetricExporter
    publisher = SketchLogOTelPublisher(log, exporter=ConsoleMetricExporter(), export_interval_millis=1000)
    publisher.shutdown()



@pytest.mark.skipif(not HAS_OPENTELEMETRY, reason="opentelemetry-sdk not installed")
def test_opentelemetry_adapter_windowed_memory():
    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    meter = provider.get_meter("test_meter_windowed")

    log = WindowedStreamLog(window="60s", n_buckets=6)
    for lat in [1.0, 2.0, 3.0]:
        log.add_latency(lat)

    adapter = OpenTelemetryAdapter(log, meter)

    metrics_data = reader.get_metrics_data()
    resource_metrics = metrics_data.resource_metrics[0]
    scope_metrics = resource_metrics.scope_metrics[0]
    metrics = {m.name: m for m in scope_metrics.metrics}

    mem_metric = metrics["sketchlog.memory_kb"]
    mem_data = list(mem_metric.data.data_points)[0]

    # It should report the memory of all buckets, not just one merged sketch
    # A single StreamLog is around 81KB, 6 buckets is ~486KB
    assert mem_data.value > 400.0
    assert abs(mem_data.value - log.memory_kb()) < 1.0


@pytest.mark.skipif(not HAS_OPENTELEMETRY, reason="opentelemetry-sdk not installed")
def test_opentelemetry_adapter_reset():
    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    meter = provider.get_meter("test_meter_reset")

    log = StreamLog()
    log.add_event("error", 5)

    adapter = OpenTelemetryAdapter(log, meter, export_events=["error"])

    metrics_data = reader.get_metrics_data()
    metrics = {m.name: m for m in metrics_data.resource_metrics[0].scope_metrics[0].metrics}

    ev_metric = metrics["sketchlog.events.total"]
    assert list(ev_metric.data.data_points)[0].value == 5

    freq_metric = metrics["sketchlog.events.frequency"]
    assert list(freq_metric.data.data_points)[0].value == 5

    # Reset the log
    log.reset()

    # Read again
    metrics_data2 = reader.get_metrics_data()
    metrics2 = {m.name: m for m in metrics_data2.resource_metrics[0].scope_metrics[0].metrics}

    ev_metric2 = metrics2["sketchlog.events.total"]
    # It must support going down to 0 since we changed it to an ObservableGauge
    assert list(ev_metric2.data.data_points)[0].value == 0

    freq_metric2 = metrics2["sketchlog.events.frequency"]
    assert list(freq_metric2.data.data_points)[0].value == 0
