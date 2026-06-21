

@pytest.mark.skipif(not HAS_OPENTELEMETRY, reason="opentelemetry-sdk not installed")
def test_opentelemetry_adapter_windowed_memory():
    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    meter = provider.get_meter("test_meter_windowed")

    # Use n_buckets=6 to ensure multiple buckets exist
    log = WindowedStreamLog(window_size_seconds=60, n_buckets=6)
    log.add_batch([1.0, 2.0, 3.0])

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

