"""Comprehensive tests for SketchLog export integrations.

All tests are deterministic and network-free.  HTTP clients are mocked
via :func:`unittest.mock.patch`.
"""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch, call
from typing import Any
from urllib.parse import urlparse

import httpx

from sketchlog.exporters.base import ExporterError
from sketchlog.exporters.loki import LokiConfig, LokiExporter, LokiStream
from sketchlog.exporters.datadog import DatadogConfig, DatadogExporter, DatadogMetric, MetricType
from sketchlog.exporters.newrelic import (
    NewRelicConfig,
    NewRelicEvent,
    NewRelicExporter,
    NewRelicMetric,
    NewRelicMetricType,
    NewRelicRegion,
)


# ── helpers ──────────────────────────────────────────────────────────────────

def _resp(status: int = 204) -> MagicMock:
    m = MagicMock()
    m.status_code = status
    if status >= 400:
        err = httpx.HTTPStatusError(
            f"HTTP {status}", request=MagicMock(), response=MagicMock()
        )
        err.response.status_code = status
        m.raise_for_status.side_effect = err
    else:
        m.raise_for_status.return_value = None
    return m


def _mock_client(status: int = 204) -> MagicMock:
    c = MagicMock()
    c.post.return_value = _resp(status)
    return c


# ═══════════════════════════════════════════════════════════════════════════════
# ExporterError
# ═══════════════════════════════════════════════════════════════════════════════

def test_exporter_error_no_status():
    e = ExporterError("oops")
    assert str(e) == "oops"
    assert e.status_code is None


def test_exporter_error_with_status():
    e = ExporterError("bad", status_code=500)
    assert e.status_code == 500


# ═══════════════════════════════════════════════════════════════════════════════
# LokiConfig
# ═══════════════════════════════════════════════════════════════════════════════

def test_loki_config_strips_slash():
    cfg = LokiConfig(url="http://loki:3100/")
    assert not cfg.url.endswith("/")


def test_loki_config_empty_url():
    with pytest.raises(ValueError, match="url"):
        LokiConfig(url="")


def test_loki_config_partial_auth():
    with pytest.raises(ValueError, match="both"):
        LokiConfig(url="http://loki:3100", username="u")


def test_loki_config_valid_basic_auth():
    cfg = LokiConfig(url="http://loki:3100", username="u", password="p")
    assert cfg.username == "u"
    assert cfg.password == "p"


# ═══════════════════════════════════════════════════════════════════════════════
# LokiExporter
# ═══════════════════════════════════════════════════════════════════════════════

def test_loki_push_ok():
    cfg = LokiConfig(url="http://loki:3100", labels={"app": "test"})
    mc = _mock_client(204)
    with patch("sketchlog.exporters.loki.httpx.Client", return_value=mc):
        exp = LokiExporter(cfg)
        exp.push(["hello world"])
    mc.post.assert_called_once()
    args, kwargs = mc.post.call_args
    assert "loki/api/v1/push" in args[0]
    payload = kwargs["json"]
    assert len(payload["streams"]) == 1
    assert payload["streams"][0]["stream"] == {"app": "test"}


def test_loki_context_manager():
    cfg = LokiConfig(url="http://loki:3100")
    mc = _mock_client()
    with patch("sketchlog.exporters.loki.httpx.Client", return_value=mc):
        with LokiExporter(cfg) as exp:
            exp.push(["line"])
    mc.close.assert_called_once()


def test_loki_double_close():
    cfg = LokiConfig(url="http://loki:3100")
    mc = _mock_client()
    with patch("sketchlog.exporters.loki.httpx.Client", return_value=mc):
        exp = LokiExporter(cfg)
        exp.open()
        exp.close()
        exp.close()  # idempotent
    assert mc.close.call_count == 1


def test_loki_basic_auth_passed_to_client():
    cfg = LokiConfig(url="http://loki:3100", username="u", password="p")
    mc = _mock_client()
    with patch("sketchlog.exporters.loki.httpx.Client", return_value=mc) as MockCls:
        exp = LokiExporter(cfg)
        exp.push(["line"])
    _, kwargs = MockCls.call_args
    assert kwargs.get("auth") == ("u", "p")


def test_loki_bearer_token():
    cfg = LokiConfig(url="http://loki:3100", auth_token="mytoken")
    mc = _mock_client()
    with patch("sketchlog.exporters.loki.httpx.Client", return_value=mc) as MockCls:
        exp = LokiExporter(cfg)
        exp.push(["line"])
    _, kwargs = MockCls.call_args
    assert kwargs["headers"]["Authorization"] == "Bearer mytoken"


def test_loki_push_stream():
    cfg = LokiConfig(url="http://loki:3100")
    mc = _mock_client()
    stream = LokiStream(labels={"job": "test"}, lines=["a", "b"])
    with patch("sketchlog.exporters.loki.httpx.Client", return_value=mc):
        LokiExporter(cfg).push_stream(stream)
    mc.post.assert_called_once()


def test_loki_push_streams_multiple():
    cfg = LokiConfig(url="http://loki:3100")
    mc = _mock_client()
    streams = [
        LokiStream(labels={"job": "a"}, lines=["x"]),
        LokiStream(labels={"job": "b"}, lines=["y"]),
    ]
    with patch("sketchlog.exporters.loki.httpx.Client", return_value=mc):
        LokiExporter(cfg).push_streams(streams)
    _, kwargs = mc.post.call_args
    assert len(kwargs["json"]["streams"]) == 2


def test_loki_http_error_wrapped():
    cfg = LokiConfig(url="http://loki:3100")
    mc = _mock_client(500)
    with patch("sketchlog.exporters.loki.httpx.Client", return_value=mc):
        with pytest.raises(ExporterError) as exc_info:
            LokiExporter(cfg).push(["line"])
    assert exc_info.value.status_code == 500


def test_loki_timeout_wrapped():
    cfg = LokiConfig(url="http://loki:3100")
    mc = MagicMock()
    mc.post.side_effect = httpx.TimeoutException("timed out")
    with patch("sketchlog.exporters.loki.httpx.Client", return_value=mc):
        with pytest.raises(ExporterError, match="timed out"):
            LokiExporter(cfg).push(["line"])


def test_loki_request_error_wrapped():
    cfg = LokiConfig(url="http://loki:3100")
    mc = MagicMock()
    mc.post.side_effect = httpx.ConnectError("refused")
    with patch("sketchlog.exporters.loki.httpx.Client", return_value=mc):
        with pytest.raises(ExporterError, match="transport error"):
            LokiExporter(cfg).push(["line"])


def test_loki_custom_client():
    cfg = LokiConfig(url="http://loki:3100")
    mc = _mock_client()
    exp = LokiExporter(cfg, client=mc)
    exp.push(["line"])
    mc.post.assert_called_once()


def test_loki_label_merge():
    cfg = LokiConfig(url="http://loki:3100", labels={"env": "prod"})
    mc = _mock_client()
    with patch("sketchlog.exporters.loki.httpx.Client", return_value=mc):
        LokiExporter(cfg).push(["line"], labels={"app": "svc"})
    _, kwargs = mc.post.call_args
    assert kwargs["json"]["streams"][0]["stream"] == {"env": "prod", "app": "svc"}


def test_loki_timestamp_tuple():
    cfg = LokiConfig(url="http://loki:3100")
    mc = _mock_client()
    with patch("sketchlog.exporters.loki.httpx.Client", return_value=mc):
        LokiExporter(cfg).push([(1_000_000_000_000, "msg")])
    _, kwargs = mc.post.call_args
    values = kwargs["json"]["streams"][0]["values"]
    assert values[0][0] == "1000000000000"
    assert values[0][1] == "msg"


# ═══════════════════════════════════════════════════════════════════════════════
# DatadogConfig
# ═══════════════════════════════════════════════════════════════════════════════

def test_dd_config_empty_api_key():
    with pytest.raises(ValueError, match="api_key"):
        DatadogConfig(api_key="")


def test_dd_config_empty_site():
    with pytest.raises(ValueError, match="site"):
        DatadogConfig(api_key="k", site="")


def test_dd_config_defaults():
    cfg = DatadogConfig(api_key="k")
    assert cfg.site == "datadoghq.com"
    assert cfg.metric_prefix == ""
    assert cfg.default_tags == []


# ═══════════════════════════════════════════════════════════════════════════════
# DatadogExporter
# ═══════════════════════════════════════════════════════════════════════════════

def test_dd_send_metric_ok():
    cfg = DatadogConfig(api_key="k")
    mc = _mock_client(202)
    with patch("sketchlog.exporters.datadog.httpx.Client", return_value=mc):
        DatadogExporter(cfg).send_metric(DatadogMetric("cpu", 0.5))
    mc.post.assert_called_once()
    _, kwargs = mc.post.call_args
    series = kwargs["json"]["series"]
    assert series[0]["metric"] == "cpu"
    assert series[0]["type"] == "gauge"


def test_dd_metric_prefix():
    cfg = DatadogConfig(api_key="k", metric_prefix="sk.")
    mc = _mock_client(202)
    with patch("sketchlog.exporters.datadog.httpx.Client", return_value=mc):
        DatadogExporter(cfg).send_metric(DatadogMetric("latency", 1.0))
    _, kwargs = mc.post.call_args
    assert kwargs["json"]["series"][0]["metric"] == "sk.latency"


def test_dd_default_tags_merged():
    cfg = DatadogConfig(api_key="k", default_tags=["env:prod"])
    mc = _mock_client(202)
    with patch("sketchlog.exporters.datadog.httpx.Client", return_value=mc):
        DatadogExporter(cfg).send_metric(
            DatadogMetric("req", 1, tags=["svc:api"])
        )
    _, kwargs = mc.post.call_args
    tags = kwargs["json"]["series"][0]["tags"]
    assert "env:prod" in tags
    assert "svc:api" in tags


def test_dd_eu_site_endpoint():
    cfg = DatadogConfig(api_key="k", site="datadoghq.eu")
    mc = _mock_client(202)
    with patch("sketchlog.exporters.datadog.httpx.Client", return_value=mc):
        DatadogExporter(cfg).send_metric(DatadogMetric("m", 1))
    args, _ = mc.post.call_args
    assert "datadoghq.eu" in args[0]


def test_dd_send_metrics_batch():
    cfg = DatadogConfig(api_key="k")
    mc = _mock_client(202)
    metrics = [DatadogMetric("a", 1), DatadogMetric("b", 2, MetricType.COUNT)]
    with patch("sketchlog.exporters.datadog.httpx.Client", return_value=mc):
        DatadogExporter(cfg).send_metrics(metrics)
    _, kwargs = mc.post.call_args
    assert len(kwargs["json"]["series"]) == 2


def test_dd_http_error_wrapped():
    cfg = DatadogConfig(api_key="k")
    mc = _mock_client(403)
    with patch("sketchlog.exporters.datadog.httpx.Client", return_value=mc):
        with pytest.raises(ExporterError) as exc_info:
            DatadogExporter(cfg).send_metric(DatadogMetric("m", 1))
    assert exc_info.value.status_code == 403


def test_dd_timeout_wrapped():
    cfg = DatadogConfig(api_key="k")
    mc = MagicMock()
    mc.post.side_effect = httpx.TimeoutException("t/o")
    with patch("sketchlog.exporters.datadog.httpx.Client", return_value=mc):
        with pytest.raises(ExporterError, match="timed out"):
            DatadogExporter(cfg).send_metric(DatadogMetric("m", 1))


def test_dd_request_error_wrapped():
    cfg = DatadogConfig(api_key="k")
    mc = MagicMock()
    mc.post.side_effect = httpx.ConnectError("conn refused")
    with patch("sketchlog.exporters.datadog.httpx.Client", return_value=mc):
        with pytest.raises(ExporterError, match="transport error"):
            DatadogExporter(cfg).send_metric(DatadogMetric("m", 1))


def test_dd_context_manager():
    cfg = DatadogConfig(api_key="k")
    mc = _mock_client(202)
    with patch("sketchlog.exporters.datadog.httpx.Client", return_value=mc):
        with DatadogExporter(cfg) as exp:
            exp.send_metric(DatadogMetric("m", 1))
    mc.close.assert_called_once()


def test_dd_double_close():
    cfg = DatadogConfig(api_key="k")
    mc = _mock_client(202)
    with patch("sketchlog.exporters.datadog.httpx.Client", return_value=mc):
        exp = DatadogExporter(cfg)
        exp.open()
        exp.close()
        exp.close()
    assert mc.close.call_count == 1


def test_dd_host_resource():
    cfg = DatadogConfig(api_key="k")
    mc = _mock_client(202)
    with patch("sketchlog.exporters.datadog.httpx.Client", return_value=mc):
        DatadogExporter(cfg).send_metric(DatadogMetric("m", 1, host="web-01"))
    _, kwargs = mc.post.call_args
    resources = kwargs["json"]["series"][0]["resources"]
    assert resources[0] == {"name": "web-01", "type": "host"}


def test_dd_custom_timestamp():
    cfg = DatadogConfig(api_key="k")
    mc = _mock_client(202)
    with patch("sketchlog.exporters.datadog.httpx.Client", return_value=mc):
        DatadogExporter(cfg).send_metric(DatadogMetric("m", 1, timestamp=12345))
    _, kwargs = mc.post.call_args
    assert kwargs["json"]["series"][0]["points"][0]["timestamp"] == 12345


# ═══════════════════════════════════════════════════════════════════════════════
# NewRelicConfig
# ═══════════════════════════════════════════════════════════════════════════════

def test_nr_config_empty_key():
    with pytest.raises(ValueError, match="api_key"):
        NewRelicConfig(api_key="", account_id="123")


def test_nr_config_empty_account():
    with pytest.raises(ValueError, match="account_id"):
        NewRelicConfig(api_key="k", account_id="")


def test_nr_config_defaults():
    cfg = NewRelicConfig(api_key="k", account_id="123")
    assert cfg.region == NewRelicRegion.US


# ═══════════════════════════════════════════════════════════════════════════════
# NewRelicExporter — URL helpers
# ═══════════════════════════════════════════════════════════════════════════════

def test_nr_us_events_url():
    cfg = NewRelicConfig(api_key="k", account_id="999")
    exp = NewRelicExporter(cfg)
    parsed = urlparse(exp._events_url())
    assert parsed.hostname == "insights-collector.nr-data.net"
    assert "999" in exp._events_url()


def test_nr_eu_events_url():
    cfg = NewRelicConfig(api_key="k", account_id="999", region=NewRelicRegion.EU)
    exp = NewRelicExporter(cfg)
    parsed = urlparse(exp._events_url())
    assert parsed.hostname == "insights-collector.eu01.nr-data.net"


def test_nr_us_metrics_url():
    cfg = NewRelicConfig(api_key="k", account_id="999")
    exp = NewRelicExporter(cfg)
    parsed = urlparse(exp._metrics_url())
    assert parsed.hostname == "metric-api.newrelic.com"


def test_nr_eu_metrics_url():
    cfg = NewRelicConfig(api_key="k", account_id="999", region=NewRelicRegion.EU)
    exp = NewRelicExporter(cfg)
    parsed = urlparse(exp._metrics_url())
    assert parsed.hostname == "metric-api.eu.newrelic.com"


# ═══════════════════════════════════════════════════════════════════════════════
# NewRelicExporter — events
# ═══════════════════════════════════════════════════════════════════════════════

def test_nr_send_event_ok():
    cfg = NewRelicConfig(api_key="k", account_id="123")
    mc = _mock_client(200)
    with patch("sketchlog.exporters.newrelic.httpx.Client", return_value=mc):
        NewRelicExporter(cfg).send_event(
            NewRelicEvent("Deploy", {"version": "1.0"})
        )
    mc.post.assert_called_once()
    _, kwargs = mc.post.call_args
    payload = kwargs["json"]
    assert payload[0]["eventType"] == "Deploy"
    assert payload[0]["version"] == "1.0"


def test_nr_send_events_batch():
    cfg = NewRelicConfig(api_key="k", account_id="123")
    mc = _mock_client(200)
    events = [
        NewRelicEvent("A", {"x": 1}),
        NewRelicEvent("B", {"y": 2}),
    ]
    with patch("sketchlog.exporters.newrelic.httpx.Client", return_value=mc):
        NewRelicExporter(cfg).send_events(events)
    _, kwargs = mc.post.call_args
    assert len(kwargs["json"]) == 2


def test_nr_event_custom_timestamp():
    cfg = NewRelicConfig(api_key="k", account_id="123")
    mc = _mock_client(200)
    with patch("sketchlog.exporters.newrelic.httpx.Client", return_value=mc):
        NewRelicExporter(cfg).send_event(
            NewRelicEvent("E", timestamp=99999)
        )
    _, kwargs = mc.post.call_args
    assert kwargs["json"][0]["timestamp"] == 99999


# ═══════════════════════════════════════════════════════════════════════════════
# NewRelicExporter — metrics
# ═══════════════════════════════════════════════════════════════════════════════

def test_nr_send_metric_gauge():
    cfg = NewRelicConfig(api_key="k", account_id="123")
    mc = _mock_client(202)
    with patch("sketchlog.exporters.newrelic.httpx.Client", return_value=mc):
        NewRelicExporter(cfg).send_metric(
            NewRelicMetric("cpu", 0.7)
        )
    _, kwargs = mc.post.call_args
    m = kwargs["json"][0]["metrics"][0]
    assert m["name"] == "cpu"
    assert m["type"] == "gauge"
    assert m["value"] == 0.7


def test_nr_send_metric_summary():
    cfg = NewRelicConfig(api_key="k", account_id="123")
    mc = _mock_client(202)
    summary_val: dict[str, float] = {"count": 10, "sum": 100.0, "min": 1.0, "max": 20.0}
    with patch("sketchlog.exporters.newrelic.httpx.Client", return_value=mc):
        NewRelicExporter(cfg).send_metric(
            NewRelicMetric("latency", summary_val, NewRelicMetricType.SUMMARY, interval_ms=60000)
        )
    _, kwargs = mc.post.call_args
    m = kwargs["json"][0]["metrics"][0]
    assert m["type"] == "summary"
    assert m["value"] == summary_val
    assert m["interval.ms"] == 60000


def test_nr_send_metrics_batch():
    cfg = NewRelicConfig(api_key="k", account_id="123")
    mc = _mock_client(202)
    metrics = [NewRelicMetric("a", 1), NewRelicMetric("b", 2)]
    with patch("sketchlog.exporters.newrelic.httpx.Client", return_value=mc):
        NewRelicExporter(cfg).send_metrics(metrics)
    _, kwargs = mc.post.call_args
    assert len(kwargs["json"][0]["metrics"]) == 2


# ═══════════════════════════════════════════════════════════════════════════════
# NewRelicExporter — error handling & lifecycle
# ═══════════════════════════════════════════════════════════════════════════════

def test_nr_http_error_wrapped():
    cfg = NewRelicConfig(api_key="k", account_id="123")
    mc = _mock_client(403)
    with patch("sketchlog.exporters.newrelic.httpx.Client", return_value=mc):
        with pytest.raises(ExporterError) as exc_info:
            NewRelicExporter(cfg).send_event(NewRelicEvent("E"))
    assert exc_info.value.status_code == 403


def test_nr_timeout_wrapped():
    cfg = NewRelicConfig(api_key="k", account_id="123")
    mc = MagicMock()
    mc.post.side_effect = httpx.TimeoutException("t/o")
    with patch("sketchlog.exporters.newrelic.httpx.Client", return_value=mc):
        with pytest.raises(ExporterError, match="timed out"):
            NewRelicExporter(cfg).send_event(NewRelicEvent("E"))


def test_nr_request_error_wrapped():
    cfg = NewRelicConfig(api_key="k", account_id="123")
    mc = MagicMock()
    mc.post.side_effect = httpx.ConnectError("refused")
    with patch("sketchlog.exporters.newrelic.httpx.Client", return_value=mc):
        with pytest.raises(ExporterError, match="transport error"):
            NewRelicExporter(cfg).send_event(NewRelicEvent("E"))


def test_nr_context_manager():
    cfg = NewRelicConfig(api_key="k", account_id="123")
    mc = _mock_client(200)
    with patch("sketchlog.exporters.newrelic.httpx.Client", return_value=mc):
        with NewRelicExporter(cfg) as exp:
            exp.send_event(NewRelicEvent("E"))
    mc.close.assert_called_once()


def test_nr_double_close():
    cfg = NewRelicConfig(api_key="k", account_id="123")
    mc = _mock_client(200)
    with patch("sketchlog.exporters.newrelic.httpx.Client", return_value=mc):
        exp = NewRelicExporter(cfg)
        exp.open()
        exp.close()
        exp.close()
    assert mc.close.call_count == 1


def test_nr_custom_client():
    cfg = NewRelicConfig(api_key="k", account_id="123")
    mc = _mock_client(200)
    exp = NewRelicExporter(cfg, client=mc)
    exp.send_event(NewRelicEvent("E"))
    mc.post.assert_called_once()
