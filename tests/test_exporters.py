"""Tests for Loki, Datadog, and New Relic exporters — zero real network calls."""
from __future__ import annotations
import pytest
from unittest.mock import MagicMock, patch, call
from typing import Any
from urllib.parse import urlparse
import httpx

from sketchlog.exporters.base import ExporterError
from sketchlog.exporters.loki import LokiConfig, LokiStream, LokiExporter
from sketchlog.exporters.datadog import DatadogConfig, DatadogMetric, MetricType, DatadogExporter
from sketchlog.exporters.newrelic import (
    NewRelicConfig, NewRelicEvent, NewRelicMetric, NewRelicMetricType,
    NewRelicRegion, NewRelicExporter,
)


# ── helpers ───────────────────────────────────────────────────────────────────

def _resp(status: int, json_body: Any = None) -> MagicMock:
    r = MagicMock(spec=httpx.Response)
    r.status_code = status
    if status >= 400:
        r.raise_for_status.side_effect = httpx.HTTPStatusError(
            f"HTTP {status}", request=MagicMock(), response=MagicMock(status_code=status)
        )
    else:
        r.raise_for_status.return_value = None
    return r


def _mock_client(status: int = 204) -> MagicMock:
    c = MagicMock(spec=httpx.Client)
    c.post.return_value = _resp(status)
    c.__enter__ = lambda s: s
    c.__exit__ = MagicMock(return_value=False)
    return c


# ════════════════════════════════════════════════════════════════════════════════
# ExporterError
# ════════════════════════════════════════════════════════════════════════════════

def test_exporter_error_no_status():
    e = ExporterError("oops")
    assert str(e) == "oops"
    assert e.status_code is None


def test_exporter_error_with_status():
    e = ExporterError("bad", status_code=429)
    assert e.status_code == 429


# ════════════════════════════════════════════════════════════════════════════════
# LokiConfig
# ════════════════════════════════════════════════════════════════════════════════

def test_loki_config_valid():
    cfg = LokiConfig(url="http://loki:3100")
    assert cfg.url == "http://loki:3100"
    assert cfg.timeout == 10.0


def test_loki_config_strips_trailing_slash():
    cfg = LokiConfig(url="http://loki:3100/")
    assert not cfg.url.endswith("/")


def test_loki_config_empty_url():
    with pytest.raises(ValueError):
        LokiConfig(url="")


def test_loki_config_bearer_and_basic_exclusive():
    with pytest.raises(ValueError):
        LokiConfig(url="http://loki:3100", bearer_token="t", username="u")


def test_loki_config_bearer_only():
    cfg = LokiConfig(url="http://loki:3100", bearer_token="mytoken")
    assert cfg.bearer_token == "mytoken"


def test_loki_config_basic_auth():
    cfg = LokiConfig(url="http://loki:3100", username="u", password="p")
    assert cfg.username == "u"
    assert cfg.password == "p"


# ════════════════════════════════════════════════════════════════════════════════
# LokiExporter
# ════════════════════════════════════════════════════════════════════════════════

def test_loki_push_ok():
    cfg = LokiConfig(url="http://loki:3100")
    mc = _mock_client(204)
    exp = LokiExporter(cfg, client=mc)
    exp.push(["hello world"])
    mc.post.assert_called_once()
    args, kwargs = mc.post.call_args
    assert "loki/api/v1/push" in args[0]


def test_loki_push_stream_ok():
    cfg = LokiConfig(url="http://loki:3100")
    mc = _mock_client(204)
    exp = LokiExporter(cfg, client=mc)
    s = LokiStream(labels={"app": "test"}, lines=["line1", "line2"])
    exp.push_stream(s)
    mc.post.assert_called_once()


def test_loki_push_streams_multiple():
    cfg = LokiConfig(url="http://loki:3100")
    mc = _mock_client(204)
    exp = LokiExporter(cfg, client=mc)
    s1 = LokiStream(labels={"a": "1"}, lines=["x"])
    s2 = LokiStream(labels={"b": "2"}, lines=["y"])
    exp.push_streams([s1, s2])
    mc.post.assert_called_once()
    payload = mc.post.call_args[1]["json"]
    assert len(payload["streams"]) == 2


def test_loki_push_with_timestamps():
    cfg = LokiConfig(url="http://loki:3100")
    mc = _mock_client(204)
    exp = LokiExporter(cfg, client=mc)
    s = LokiStream(labels={"a": "1"}, lines=["x"], timestamps=[1_000_000_000_000])
    exp.push_stream(s)
    payload = mc.post.call_args[1]["json"]
    assert payload["streams"][0]["values"][0][0] == "1000000000000"


def test_loki_push_http_error():
    cfg = LokiConfig(url="http://loki:3100")
    mc = _mock_client(500)
    exp = LokiExporter(cfg, client=mc)
    with pytest.raises(ExporterError) as exc_info:
        exp.push(["line"])
    assert exc_info.value.status_code == 500


def test_loki_push_timeout():
    cfg = LokiConfig(url="http://loki:3100")
    mc = MagicMock(spec=httpx.Client)
    mc.post.side_effect = httpx.TimeoutException("timeout")
    exp = LokiExporter(cfg, client=mc)
    with pytest.raises(ExporterError, match="timed out"):
        exp.push(["line"])


def test_loki_push_request_error():
    cfg = LokiConfig(url="http://loki:3100")
    mc = MagicMock(spec=httpx.Client)
    mc.post.side_effect = httpx.RequestError("conn refused")
    exp = LokiExporter(cfg, client=mc)
    with pytest.raises(ExporterError, match="transport error"):
        exp.push(["line"])


def test_loki_context_manager():
    cfg = LokiConfig(url="http://loki:3100")
    mc = _mock_client(204)
    with LokiExporter(cfg, client=mc) as exp:
        exp.push(["line"])
    mc.post.assert_called_once()


def test_loki_double_close():
    cfg = LokiConfig(url="http://loki:3100")
    mc = _mock_client(204)
    exp = LokiExporter(cfg, client=mc)
    exp.close()
    exp.close()  # must not raise


def test_loki_basic_auth_client_built():
    """Verify that _make_client passes auth tuple when username is set."""
    cfg = LokiConfig(url="http://loki:3100", username="u", password="p")
    with patch("sketchlog.exporters.loki.httpx.Client") as MockClient:
        MockClient.return_value = _mock_client(204)
        exp = LokiExporter(cfg)
        exp.push(["line"])
        _, kwargs = MockClient.call_args
        assert kwargs.get("auth") == ("u", "p")


def test_loki_bearer_token_header():
    """Verify that _make_client adds Authorization header for bearer token."""
    cfg = LokiConfig(url="http://loki:3100", bearer_token="my-token")
    with patch("sketchlog.exporters.loki.httpx.Client") as MockClient:
        MockClient.return_value = _mock_client(204)
        exp = LokiExporter(cfg)
        exp.push(["line"])
        _, kwargs = MockClient.call_args
        assert kwargs["headers"].get("Authorization") == "Bearer my-token"


def test_loki_tenant_id_header():
    """Verify that _make_client adds X-Scope-OrgID header for tenant_id."""
    cfg = LokiConfig(url="http://loki:3100", tenant_id="my-tenant")
    with patch("sketchlog.exporters.loki.httpx.Client") as MockClient:
        MockClient.return_value = _mock_client(204)
        exp = LokiExporter(cfg)
        exp.push(["line"])
        _, kwargs = MockClient.call_args
        assert kwargs["headers"].get("X-Scope-OrgID") == "my-tenant"


# ════════════════════════════════════════════════════════════════════════════════
# DatadogConfig
# ════════════════════════════════════════════════════════════════════════════════

def test_dd_config_valid():
    cfg = DatadogConfig(api_key="abc123")
    assert cfg.api_key == "abc123"
    assert cfg.site == "datadoghq.com"
    assert cfg.prefix == ""


def test_dd_config_empty_api_key():
    with pytest.raises(ValueError):
        DatadogConfig(api_key="")


def test_dd_config_empty_site():
    with pytest.raises(ValueError):
        DatadogConfig(api_key="k", site="")


def test_dd_config_eu_site():
    cfg = DatadogConfig(api_key="k", site="datadoghq.eu")
    assert "eu" in cfg.site


# ════════════════════════════════════════════════════════════════════════════════
# DatadogExporter
# ════════════════════════════════════════════════════════════════════════════════

def test_dd_send_metric_ok():
    cfg = DatadogConfig(api_key="k")
    mc = _mock_client(202)
    exp = DatadogExporter(cfg, client=mc)
    exp.send_metric(DatadogMetric("cpu", 42.0))
    mc.post.assert_called_once()
    payload = mc.post.call_args[1]["json"]
    assert payload["series"][0]["metric"] == "cpu"
    assert payload["series"][0]["type"] == "gauge"


def test_dd_send_metric_with_prefix():
    cfg = DatadogConfig(api_key="k", prefix="myapp.")
    mc = _mock_client(202)
    exp = DatadogExporter(cfg, client=mc)
    exp.send_metric(DatadogMetric("latency", 1.5))
    payload = mc.post.call_args[1]["json"]
    assert payload["series"][0]["metric"] == "myapp.latency"


def test_dd_send_metrics_batch():
    cfg = DatadogConfig(api_key="k", default_tags=["env:prod"])
    mc = _mock_client(202)
    exp = DatadogExporter(cfg, client=mc)
    metrics = [DatadogMetric("a", 1.0), DatadogMetric("b", 2.0, type=MetricType.COUNT)]
    exp.send_metrics(metrics)
    payload = mc.post.call_args[1]["json"]
    assert len(payload["series"]) == 2
    assert payload["series"][0]["tags"] == ["env:prod"]


def test_dd_send_metric_with_host():
    cfg = DatadogConfig(api_key="k")
    mc = _mock_client(202)
    exp = DatadogExporter(cfg, client=mc)
    exp.send_metric(DatadogMetric("cpu", 1.0, host="myhost"))
    payload = mc.post.call_args[1]["json"]
    assert payload["series"][0]["resources"][0]["name"] == "myhost"


def test_dd_send_metric_http_error():
    cfg = DatadogConfig(api_key="k")
    mc = _mock_client(403)
    exp = DatadogExporter(cfg, client=mc)
    with pytest.raises(ExporterError) as exc_info:
        exp.send_metric(DatadogMetric("cpu", 1.0))
    assert exc_info.value.status_code == 403


def test_dd_send_metric_timeout():
    cfg = DatadogConfig(api_key="k")
    mc = MagicMock(spec=httpx.Client)
    mc.post.side_effect = httpx.TimeoutException("t/o")
    exp = DatadogExporter(cfg, client=mc)
    with pytest.raises(ExporterError, match="timed out"):
        exp.send_metric(DatadogMetric("cpu", 1.0))


def test_dd_send_metric_request_error():
    cfg = DatadogConfig(api_key="k")
    mc = MagicMock(spec=httpx.Client)
    mc.post.side_effect = httpx.RequestError("dns fail")
    exp = DatadogExporter(cfg, client=mc)
    with pytest.raises(ExporterError, match="transport error"):
        exp.send_metric(DatadogMetric("cpu", 1.0))


def test_dd_context_manager():
    cfg = DatadogConfig(api_key="k")
    mc = _mock_client(202)
    with DatadogExporter(cfg, client=mc) as exp:
        exp.send_metric(DatadogMetric("m", 1.0))
    mc.post.assert_called_once()


def test_dd_double_close():
    cfg = DatadogConfig(api_key="k")
    mc = _mock_client(202)
    exp = DatadogExporter(cfg, client=mc)
    exp.close()
    exp.close()  # must not raise


def test_dd_endpoint_eu():
    cfg = DatadogConfig(api_key="k", site="datadoghq.eu")
    exp = DatadogExporter(cfg)
    assert "datadoghq.eu" in exp._endpoint()


# ════════════════════════════════════════════════════════════════════════════════
# NewRelicConfig
# ════════════════════════════════════════════════════════════════════════════════

def test_nr_config_valid():
    cfg = NewRelicConfig(api_key="k", account_id="123")
    assert cfg.region == NewRelicRegion.US


def test_nr_config_empty_api_key():
    with pytest.raises(ValueError):
        NewRelicConfig(api_key="", account_id="123")


def test_nr_config_empty_account_id():
    with pytest.raises(ValueError):
        NewRelicConfig(api_key="k", account_id="")


def test_nr_eu_region_events_url():
    cfg = NewRelicConfig(api_key="k", account_id="999", region=NewRelicRegion.EU)
    exp = NewRelicExporter(cfg)
    url = exp._events_url()
    parsed = urlparse(url)
    assert parsed.hostname == "insights-collector.eu01.nr-data.net"


def test_nr_eu_region_metrics_url():
    cfg = NewRelicConfig(api_key="k", account_id="999", region=NewRelicRegion.EU)
    exp = NewRelicExporter(cfg)
    url = exp._metrics_url()
    parsed = urlparse(url)
    assert parsed.hostname == "metric-api.eu.newrelic.com"


def test_nr_us_region_events_url():
    cfg = NewRelicConfig(api_key="k", account_id="123")
    exp = NewRelicExporter(cfg)
    url = exp._events_url()
    parsed = urlparse(url)
    assert "nr-data.net" in parsed.hostname


def test_nr_us_region_metrics_url():
    cfg = NewRelicConfig(api_key="k", account_id="123")
    exp = NewRelicExporter(cfg)
    url = exp._metrics_url()
    parsed = urlparse(url)
    assert parsed.hostname == "metric-api.newrelic.com"


# ════════════════════════════════════════════════════════════════════════════════
# NewRelicExporter
# ════════════════════════════════════════════════════════════════════════════════

def test_nr_send_event_ok():
    cfg = NewRelicConfig(api_key="k", account_id="123")
    mc = _mock_client(200)
    exp = NewRelicExporter(cfg, client=mc)
    exp.send_event(NewRelicEvent("MyEvent", {"key": "val"}))
    mc.post.assert_called_once()
    payload = mc.post.call_args[1]["json"]
    assert payload[0]["eventType"] == "MyEvent"
    assert payload[0]["key"] == "val"


def test_nr_send_events_batch():
    cfg = NewRelicConfig(api_key="k", account_id="123")
    mc = _mock_client(200)
    exp = NewRelicExporter(cfg, client=mc)
    events = [NewRelicEvent("A", {}), NewRelicEvent("B", {})]
    exp.send_events(events)
    payload = mc.post.call_args[1]["json"]
    assert len(payload) == 2


def test_nr_send_event_with_timestamp():
    cfg = NewRelicConfig(api_key="k", account_id="123")
    mc = _mock_client(200)
    exp = NewRelicExporter(cfg, client=mc)
    exp.send_event(NewRelicEvent("T", {}, timestamp=1234567890))
    payload = mc.post.call_args[1]["json"]
    assert payload[0]["timestamp"] == 1234567890


def test_nr_send_metric_gauge():
    cfg = NewRelicConfig(api_key="k", account_id="123")
    mc = _mock_client(202)
    exp = NewRelicExporter(cfg, client=mc)
    exp.send_metric(NewRelicMetric("latency", 1.5))
    payload = mc.post.call_args[1]["json"]
    assert payload[0]["metrics"][0]["name"] == "latency"
    assert payload[0]["metrics"][0]["type"] == "gauge"


def test_nr_send_metric_summary():
    cfg = NewRelicConfig(api_key="k", account_id="123")
    mc = _mock_client(202)
    exp = NewRelicExporter(cfg, client=mc)
    val = {"sum": 100.0, "count": 10, "min": 1.0, "max": 20.0}
    exp.send_metric(NewRelicMetric("req", val, type=NewRelicMetricType.SUMMARY, interval_ms=60000))
    payload = mc.post.call_args[1]["json"]
    assert payload[0]["metrics"][0]["value"] == val
    assert payload[0]["metrics"][0]["interval.ms"] == 60000


def test_nr_send_metrics_batch():
    cfg = NewRelicConfig(api_key="k", account_id="123")
    mc = _mock_client(202)
    exp = NewRelicExporter(cfg, client=mc)
    m1 = NewRelicMetric("a", 1.0)
    m2 = NewRelicMetric("b", 2.0, attributes={"host": "srv1"})
    exp.send_metrics([m1, m2])
    payload = mc.post.call_args[1]["json"]
    assert len(payload[0]["metrics"]) == 2
    assert payload[0]["metrics"][1]["attributes"]["host"] == "srv1"


def test_nr_send_event_http_error():
    cfg = NewRelicConfig(api_key="k", account_id="123")
    mc = _mock_client(403)
    exp = NewRelicExporter(cfg, client=mc)
    with pytest.raises(ExporterError) as exc_info:
        exp.send_event(NewRelicEvent("E", {}))
    assert exc_info.value.status_code == 403


def test_nr_send_metric_timeout():
    cfg = NewRelicConfig(api_key="k", account_id="123")
    mc = MagicMock(spec=httpx.Client)
    mc.post.side_effect = httpx.TimeoutException("t/o")
    exp = NewRelicExporter(cfg, client=mc)
    with pytest.raises(ExporterError, match="timed out"):
        exp.send_metric(NewRelicMetric("x", 1.0))


def test_nr_send_metric_request_error():
    cfg = NewRelicConfig(api_key="k", account_id="123")
    mc = MagicMock(spec=httpx.Client)
    mc.post.side_effect = httpx.RequestError("conn fail")
    exp = NewRelicExporter(cfg, client=mc)
    with pytest.raises(ExporterError, match="transport error"):
        exp.send_metric(NewRelicMetric("x", 1.0))


def test_nr_context_manager():
    cfg = NewRelicConfig(api_key="k", account_id="123")
    mc = _mock_client(200)
    with NewRelicExporter(cfg, client=mc) as exp:
        exp.send_event(NewRelicEvent("E", {}))
    mc.post.assert_called_once()


def test_nr_double_close():
    cfg = NewRelicConfig(api_key="k", account_id="123")
    mc = _mock_client(200)
    exp = NewRelicExporter(cfg, client=mc)
    exp.close()
    exp.close()  # must not raise
