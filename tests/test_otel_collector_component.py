from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "contrib" / "otelcol-sketchlog-exporter"


def test_collector_module_files_exist():
    expected = [
        MODULE / "go.mod",
        MODULE / "README.md",
        MODULE / "examples" / "collector.yaml",
        MODULE / "sketchlogexporter" / "config.go",
        MODULE / "sketchlogexporter" / "factory.go",
        MODULE / "sketchlogexporter" / "exporter.go",
        MODULE / "sketchlogexporter" / "exporter_test.go",
    ]
    for path in expected:
        assert path.exists(), path


def test_exporter_uses_sketchlog_ingestion_api():
    source = (MODULE / "sketchlogexporter" / "exporter.go").read_text(encoding="utf-8")
    assert '"/v1/namespaces/"' in source
    assert 'escapeStreamPath(stream)' in source
    assert 'validateStreamPath(stream)' in source
    assert 'X-SketchLog-Auth-Token' in source
    assert 'splitBatch' in source


def test_config_documents_namespace_and_signal_mapping():
    config = (MODULE / "examples" / "collector.yaml").read_text(encoding="utf-8")
    assert "namespace: production" in config
    assert "kind: latency" in config
    assert "kind: event" in config
    assert "span_duration_stream" in config
    assert "log_event_stream" in config


def test_docs_nav_mentions_collector():
    docs = (ROOT / "docs" / "otel-collector.md").read_text(encoding="utf-8")
    nav = (ROOT / "mkdocs.yml").read_text(encoding="utf-8")
    assert "OpenTelemetry Collector exporter" in docs
    assert "OpenTelemetry Collector: otel-collector.md" in nav
