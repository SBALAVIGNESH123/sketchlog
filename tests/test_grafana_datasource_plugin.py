import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "grafana-sketchlog-datasource"


def test_grafana_datasource_plugin_manifest_is_valid() -> None:
    manifest = json.loads((PLUGIN / "src" / "plugin.json").read_text(encoding="utf-8"))

    assert manifest["type"] == "datasource"
    assert manifest["id"] == "sketchlog-datasource"
    assert manifest["metrics"] is True
    assert manifest["dependencies"]["grafanaDependency"].startswith(">=")


def test_grafana_datasource_uses_real_sketchlog_api_routes() -> None:
    datasource = (PLUGIN / "src" / "datasource.ts").read_text(encoding="utf-8")

    assert "/health" in datasource
    assert "/v1/namespaces/" in datasource
    assert "/metrics" in datasource
    assert "/events?name=" in datasource
    assert "/slo/evaluate" in datasource
    assert "/v1/query" in datasource
    assert "X-SketchLog-Auth-Token" in datasource
    assert "response.p90" not in datasource
    assert "SELECT ${functionName}(latency)" in datasource


def test_grafana_datasource_documents_dashboard_difference() -> None:
    docs = (ROOT / "docs" / "grafana-datasource-plugin.md").read_text(encoding="utf-8")

    assert "different" in docs.lower()
    assert "Prometheus" in docs
    assert "query SketchLog directly" in docs


def test_grafana_datasource_sample_dashboard_is_valid_json() -> None:
    dashboard = json.loads((PLUGIN / "dashboards" / "sketchlog-direct-overview.json").read_text(encoding="utf-8"))

    assert dashboard["uid"] == "sketchlog-direct-overview"
    assert dashboard["panels"]
    assert dashboard["templating"]["list"][0]["query"] == "sketchlog-datasource"
