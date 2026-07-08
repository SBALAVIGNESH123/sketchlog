"""Comprehensive tests for SketchLog export integrations.

All tests are deterministic and network-free.  HTTP clients are mocked
via :func:`unittest.mock.patch`.
"""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch, call
from typing import Any

import httpx
from urllib.parse import urlparse

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
    r = MagicMock(spec=httpx.Response)
    r.status_code = status
    if status >= 400:
        r.raise_for_status.side_effect = httpx.HTTPStatusError(
            f"HTTP {status}", request=MagicMock(), response=r
        )
    else:
        r.raise_for_status.return_value = None
    return r


# ═══════════════════════════════════════════════════════════════════════════════
# ExporterError
# ═══════════════════════════════════════════════════════════════════════════════

def test_exporter_error_no_status():
    err = ExporterError("boom")
    assert str(err) == "boom"
    assert err.status_code is None


def test_exporter_error_with_status():
    err = ExporterError("rate limited", status_code=429)
    assert err.status_code == 429


# ═══════════════════════════════════════════════════════════════════════════════
# LokiConfig
# ═══════════════════════════════════════════════════════════════════════════════

def test_loki_config_valid():
    cfg = LokiConfig(url="http://loki:3100", labels={"app": "test"})
    assert cfg.url == "http://loki:3100"
    assert cfg.labels == {"app": "test"}
    assert cfg.timeout == 10.0


def test_loki_config_empty_url():
    with pytest.raises(ValueError, match="url"):
        LokiConfig(url="")


def test_loki_config_zero_timeout():
    with pytest.raises(ValueError, match="timeout"):
        LokiConfig(url="http://loki:3100", timeout=0)


def test_loki_config_negative_timeout():
    with pytest.raises(ValueError, match="timeout"):
        LokiConfig(url="http://loki:3100", timeout=-1.0)


def test_loki_config_with_auth_token():
    cfg = LokiConfig(url="http://loki:3100", auth_token="secret")
    assert cfg.auth_token == "secret"


def test_loki_config_with_basic_auth():
    cfg = LokiConfig(url="http://loki:3100", username="admin", password="pass")
    assert cfg.username == "admin"
    assert cfg.password == "pass"


# ═══════════════════════════════════════════════════════════════════════════════
# LokiStream
# ═══════════════════════════════════════════════════════════════════════════════

def test_loki_stream_valid():
    s = LokiStream(labels={"app": "x"}, lines=["hello"])
    assert s.lines == ["hello"]


def test_loki_stream_empty_lines():
    with pytest.raises(ValueError, match="lines"):
        LokiStream(labels={}, lines=[])


def test_loki_stream_timestamps_mismatch():
    with pytest.raises(ValueError, match="timestamps_ns"):
        LokiStream(labels={}, lines=["a", "b"], timestamps_ns=[1])


# ═══════════════════════════════════════════════════════════════════════════════
# LokiExporter
# ═══════════════════════════════════════════════════════════════════════════════

def test_loki_push_ok():
    cfg = LokiConfig(url="http://loki:3100", labels={"app": "test"})
    exp = LokiExporter(cfg)
    mock_client = MagicMock()
    mock_client.post.return_value = _resp(204)
    with patch.object(exp, "_make_client", return_value=mock_client):
        exp.push(["hello world"])
    mock_client.post.assert_called_once()
    mock_client.close.assert_called_once()


def test_loki_push_empty_lines_raises():
    cfg = LokiConfig(url="http://loki:3100")
    exp = LokiExporter(cfg)
    with pytest.raises(ValueError, match="lines"):
        exp.push([])


def test_loki_push_with_extra_labels():
    cfg = LokiConfig(url="http://loki:3100", labels={"env": "prod"})
    exp = LokiExporter(cfg)
    mock_client = MagicMock()
    mock_client.post.return_value = _resp(204)
    with patch.object(exp, "_make_client", return_value=mock_client):
        exp.push(["msg"], extra_labels={"service": "api"})
    _, kwargs = mock_client.post.call_args
    streams = kwargs["json"]["streams"]
    assert streams[0]["stream"]["env"] == "prod"
    assert streams[0]["stream"]["service"] == "api"


def test_loki_push_stream():
    cfg = LokiConfig(url="http://loki:3100")
    exp = LokiExporter(cfg)
    mock_client = MagicMock()
    mock_client.post.return_value = _resp(204)
    s = LokiStream(labels={"job": "test"}, lines=["line1"])
    with patch.object(exp, "_make_client", return_value=mock_client):
        exp.push_stream(s)
    mock_client.post.assert_called_once()


def test_loki_push_streams_empty_raises():
    cfg = LokiConfig(url="http://loki:3100")
    exp = LokiExporter(cfg)
    with pytest.raises(ValueError, match="streams"):
        exp.push_streams([])


def test_loki_push_streams_with_timestamps():
    cfg = LokiConfig(url="http://loki:3100")
    exp = LokiExporter(cfg)
    mock_client = MagicMock()
    mock_client.post.return_value = _resp(204)
    s = LokiStream(labels={}, lines=["a", "b"], timestamps_ns=[1000, 2000])
    with patch.object(exp, "_make_client", return_value=mock_client):
        exp.push_streams([s])
    _, kwargs = mock_client.post.call_args
    values = kwargs["json"]["streams"][0]["values"]
    assert values[0][0] == "1000"
    assert values[1][0] == "2000"


def test_loki_push_http_error():
    cfg = LokiConfig(url="http://loki:3100")
    exp = LokiExporter(cfg)
    mock_client = MagicMock()
    mock_client.post.return_value = _resp(500)
    with patch.object(exp, "_make_client", return_value=mock_client):
        with pytest.raises(ExporterError) as exc_info:
            exp.push(["line"])
    assert exc_info.value.status_code == 500


def test_loki_push_timeout():
    cfg = LokiConfig(url="http://loki:3100")
    exp = LokiExporter(cfg)
    mock_client = MagicMock()
    mock_client.post.side_effect = httpx.TimeoutException("timed out")
    with patch.object(exp, "_make_client", return_value=mock_client):
        with pytest.raises(ExporterError, match="timed out"):
            exp.push(["line"])


def test_loki_context_manager():
    cfg = LokiConfig(url="http://loki:3100", auth_token="tok")
    mock_client = MagicMock()
    mock_client.post.return_value = _resp(204)
    with patch("sketchlog.exporters.loki.httpx.Client", return_value=mock_client):
        with LokiExporter(cfg) as exp:
            exp.push(["ctx line"])
    mock_client.close.assert_called_once()


def test_loki_double_close():
    cfg = LokiConfig(url="http://loki:3100")
    mock_client = MagicMock()
    with patch("sketchlog.exporters.loki.httpx.Client", return_value=mock_client):
        exp = LokiExporter(cfg)
        exp._client = mock_client
        exp.close()
        exp.close()  # second close must not raise
    assert mock_client.close.call_count == 1


def test_loki_basic_auth_client():
    cfg = LokiConfig(url="http://loki:3100", username="u", password="p")
    mock_client = MagicMock()
    mock_client.post.return_value = _resp(204)
    with patch("sketchlog.exporters.loki.httpx.Client", return_value=mock_client) as MockCls:
        exp = LokiExporter(cfg)
        with patch.object(exp, "_make_client", return_value=mock_client):
            exp.push(["line"])
    mock_client.post.assert_called_once()


# ═══════════════════════════════════════════════════════════════════════════════
# DatadogConfig
# ═══════════════════════════════════════════════════════════════════════════════

def test_dd_config_valid():
    cfg = DatadogConfig(api_key="abc123")
    assert cfg.api_key == "abc123"
    assert cfg.site == "datadoghq.com"
    assert cfg.timeout == 10.0


def test_dd_config_empty_api_key():
    with pytest.raises(ValueError, match="api_key"):
        DatadogConfig(api_key="")


def test_dd_config_negative_timeout():
    with pytest.raises(ValueError, match="timeout"):
        DatadogConfig(api_key="k", timeout=-5.0)


def test_dd_config_eu_site():
    cfg = DatadogConfig(api_key="k", site="datadoghq.eu")
    assert "datadoghq.eu" in DatadogExporter(cfg)._endpoint()


# ═══════════════════════════════════════════════════════════════════════════════
# DatadogMetric
# ═══════════════════════════════════════════════════════════════════════════════

def test_dd_metric_valid():
    m = DatadogMetric(name="cpu", value=0.9)
    assert m.metric_type == MetricType.GAUGE


def test_dd_metric_empty_name():
    with pytest.raises(ValueError, match="name"):
        DatadogMetric(name="", value=1.0)


# ═══════════════════════════════════════════════════════════════════════════════
# DatadogExporter
# ═══════════════════════════════════════════════════════════════════════════════

def test_dd_send_metric_ok():
    cfg = DatadogConfig(api_key="key")
    exp = DatadogExporter(cfg)
    mock_client = MagicMock()
    mock_client.post.return_value = _resp(202)
    with patch.object(exp, "_make_client", return_value=mock_client):
        exp.send_metric(DatadogMetric("cpu", 0.5))
    mock_client.post.assert_called_once()


def test_dd_send_metrics_empty_raises():
    cfg = DatadogConfig(api_key="key")
    exp = DatadogExporter(cfg)
    with pytest.raises(ValueError, match="metrics"):
        exp.send_metrics([])


def test_dd_send_metrics_multiple():
    cfg = DatadogConfig(api_key="key", default_tags=["env:test"])
    exp = DatadogExporter(cfg)
    mock_client = MagicMock()
    mock_client.post.return_value = _resp(202)
    metrics = [DatadogMetric("m1", 1.0), DatadogMetric("m2", 2.0)]
    with patch.object(exp, "_make_client", return_value=mock_client):
        exp.send_metrics(metrics)
    _, kwargs = mock_client.post.call_args
    assert len(kwargs["json"]["series"]) == 2
    assert "env:test" in kwargs["json"]["series"][0]["tags"]


def test_dd_send_metric_with_prefix():
    cfg = DatadogConfig(api_key="key", metric_prefix="myapp")
    exp = DatadogExporter(cfg)
    mock_client = MagicMock()
    mock_client.post.return_value = _resp(202)
    with patch.object(exp, "_make_client", return_value=mock_client):
        exp.send_metric(DatadogMetric("latency", 0.1))
    _, kwargs = mock_client.post.call_args
    assert kwargs["json"]["series"][0]["metric"] == "myapp.latency"


def test_dd_send_metric_with_host():
    cfg = DatadogConfig(api_key="key")
    exp = DatadogExporter(cfg)
    mock_client = MagicMock()
    mock_client.post.return_value = _resp(202)
    with patch.object(exp, "_make_client", return_value=mock_client):
        exp.send_metric(DatadogMetric("mem", 1024.0, host="web-01"))
    _, kwargs = mock_client.post.call_args
    assert kwargs["json"]["series"][0]["resources"][0]["name"] == "web-01"


def test_dd_send_metric_with_timestamp():
    cfg = DatadogConfig(api_key="key")
    exp = DatadogExporter(cfg)
    mock_client = MagicMock()
    mock_client.post.return_value = _resp(202)
    with patch.object(exp, "_make_client", return_value=mock_client):
        exp.send_metric(DatadogMetric("m", 1.0, timestamp=1700000000))
    _, kwargs = mock_client.post.call_args
    assert kwargs["json"]["series"][0]["points"][0]["timestamp"] == 1700000000


def test_dd_send_metric_http_error():
    cfg = DatadogConfig(api_key="key")
    exp = DatadogExporter(cfg)
    mock_client = MagicMock()
    mock_client.post.return_value = _resp(403)
    with patch.object(exp, "_make_client", return_value=mock_client):
        with pytest.raises(ExporterError) as exc_info:
            exp.send_metric(DatadogMetric("m", 1.0))
    assert exc_info.value.status_code == 403


def test_dd_send_metric_timeout():
    cfg = DatadogConfig(api_key="key")
    exp = DatadogExporter(cfg)
    mock_client = MagicMock()
    mock_client.post.side_effect = httpx.TimeoutException("timeout")
    with patch.object(exp, "_make_client", return_value=mock_client):
        with pytest.raises(ExporterError, match="timed out"):
            exp.send_metric(DatadogMetric("m", 1.0))


def test_dd_context_manager():
    cfg = DatadogConfig(api_key="key")
    mock_client = MagicMock()
    mock_client.post.return_value = _resp(202)
    with patch("sketchlog.exporters.datadog.httpx.Client", return_value=mock_client):
        with DatadogExporter(cfg) as exp:
            exp.send_metric(DatadogMetric("m", 1.0))
    mock_client.close.assert_called_once()


def test_dd_double_close():
    cfg = DatadogConfig(api_key="key")
    mock_client = MagicMock()
    exp = DatadogExporter(cfg)
    exp._client = mock_client
    exp.close()
    exp.close()
    assert mock_client.close.call_count == 1


# ═══════════════════════════════════════════════════════════════════════════════
# NewRelicConfig
# ═══════════════════════════════════════════════════════════════════════════════

def test_nr_config_valid():
    cfg = NewRelicConfig(api_key="NRAK-x", account_id="123")
    assert cfg.region == NewRelicRegion.US


def test_nr_config_empty_api_key():
    with pytest.raises(ValueError, match="api_key"):
        NewRelicConfig(api_key="", account_id="123")


def test_nr_config_empty_account_id():
    with pytest.raises(ValueError, match="account_id"):
        NewRelicConfig(api_key="k", account_id="")


def test_nr_config_negative_timeout():
    with pytest.raises(ValueError, match="timeout"):
        NewRelicConfig(api_key="k", account_id="1", timeout=0)


def test_nr_config_eu_region():
    cfg = NewRelicConfig(api_key="k", account_id="1", region=NewRelicRegion.EU)
    assert cfg.region == NewRelicRegion.EU


# ═══════════════════════════════════════════════════════════════════════════════
# NewRelicEvent
# ═══════════════════════════════════════════════════════════════════════════════

def test_nr_event_valid():
    ev = NewRelicEvent("PageView", {"url": "/home"})
    assert ev.event_type == "PageView"


def test_nr_event_empty_type():
    with pytest.raises(ValueError, match="event_type"):
        NewRelicEvent(event_type="")


# ═══════════════════════════════════════════════════════════════════════════════
# NewRelicMetric
# ═══════════════════════════════════════════════════════════════════════════════

def test_nr_metric_valid():
    m = NewRelicMetric("cpu", 0.5)
    assert m.metric_type == NewRelicMetricType.GAUGE


def test_nr_metric_empty_name():
    with pytest.raises(ValueError, match="name"):
        NewRelicMetric(name="", value=1.0)


def test_nr_metric_count_without_interval():
    with pytest.raises(ValueError, match="interval_ms"):
        NewRelicMetric("req", 10.0, metric_type=NewRelicMetricType.COUNT)


def test_nr_metric_count_with_interval():
    m = NewRelicMetric("req", 10.0, metric_type=NewRelicMetricType.COUNT, interval_ms=60000)
    assert m.interval_ms == 60000


def test_nr_metric_summary_without_interval():
    with pytest.raises(ValueError, match="interval_ms"):
        NewRelicMetric("p99", 0.9, metric_type=NewRelicMetricType.SUMMARY)


# ═══════════════════════════════════════════════════════════════════════════════
# NewRelicExporter
# ═══════════════════════════════════════════════════════════════════════════════

def test_nr_send_event_ok():
    cfg = NewRelicConfig(api_key="NRAK-x", account_id="123")
    exp = NewRelicExporter(cfg)
    mock_client = MagicMock()
    mock_client.post.return_value = _resp(200)
    with patch.object(exp, "_make_client", return_value=mock_client):
        exp.send_event(NewRelicEvent("Test", {"k": "v"}))
    mock_client.post.assert_called_once()


def test_nr_send_events_empty_raises():
    cfg = NewRelicConfig(api_key="k", account_id="1")
    exp = NewRelicExporter(cfg)
    with pytest.raises(ValueError, match="events"):
        exp.send_events([])


def test_nr_send_events_multiple():
    cfg = NewRelicConfig(api_key="k", account_id="1")
    exp = NewRelicExporter(cfg)
    mock_client = MagicMock()
    mock_client.post.return_value = _resp(200)
    events = [NewRelicEvent("A"), NewRelicEvent("B")]
    with patch.object(exp, "_make_client", return_value=mock_client):
        exp.send_events(events)
    _, kwargs = mock_client.post.call_args
    assert len(kwargs["json"]) == 2


def test_nr_send_event_with_timestamp():
    cfg = NewRelicConfig(api_key="k", account_id="1")
    exp = NewRelicExporter(cfg)
    mock_client = MagicMock()
    mock_client.post.return_value = _resp(200)
    ev = NewRelicEvent("TS", timestamp=1700000000)
    with patch.object(exp, "_make_client", return_value=mock_client):
        exp.send_event(ev)
    _, kwargs = mock_client.post.call_args
    assert kwargs["json"][0]["timestamp"] == 1700000000


def test_nr_send_metric_ok():
    cfg = NewRelicConfig(api_key="k", account_id="1")
    exp = NewRelicExporter(cfg)
    mock_client = MagicMock()
    mock_client.post.return_value = _resp(202)
    with patch.object(exp, "_make_client", return_value=mock_client):
        exp.send_metric(NewRelicMetric("cpu", 0.5))
    mock_client.post.assert_called_once()


def test_nr_send_metrics_empty_raises():
    cfg = NewRelicConfig(api_key="k", account_id="1")
    exp = NewRelicExporter(cfg)
    with pytest.raises(ValueError, match="metrics"):
        exp.send_metrics([])


def test_nr_send_metrics_with_interval():
    cfg = NewRelicConfig(api_key="k", account_id="1")
    exp = NewRelicExporter(cfg)
    mock_client = MagicMock()
    mock_client.post.return_value = _resp(202)
    m = NewRelicMetric("req", 5.0, metric_type=NewRelicMetricType.COUNT, interval_ms=60000)
    with patch.object(exp, "_make_client", return_value=mock_client):
        exp.send_metrics([m])
    _, kwargs = mock_client.post.call_args
    item = kwargs["json"][0]["metrics"][0]
    assert item["interval.ms"] == 60000


def test_nr_send_metric_http_error():
    cfg = NewRelicConfig(api_key="k", account_id="1")
    exp = NewRelicExporter(cfg)
    mock_client = MagicMock()
    mock_client.post.return_value = _resp(429)
    with patch.object(exp, "_make_client", return_value=mock_client):
        with pytest.raises(ExporterError) as exc_info:
            exp.send_metric(NewRelicMetric("m", 1.0))
    assert exc_info.value.status_code == 429


def test_nr_send_metric_timeout():
    cfg = NewRelicConfig(api_key="k", account_id="1")
    exp = NewRelicExporter(cfg)
    mock_client = MagicMock()
    mock_client.post.side_effect = httpx.TimeoutException("timeout")
    with patch.object(exp, "_make_client", return_value=mock_client):
        with pytest.raises(ExporterError, match="timed out"):
            exp.send_metric(NewRelicMetric("m", 1.0))


def test_nr_context_manager():
    cfg = NewRelicConfig(api_key="k", account_id="1")
    mock_client = MagicMock()
    mock_client.post.return_value = _resp(200)
    with patch("sketchlog.exporters.newrelic.httpx.Client", return_value=mock_client):
        with NewRelicExporter(cfg) as exp:
            exp.send_event(NewRelicEvent("Click"))
    mock_client.close.assert_called_once()


def test_nr_eu_region_events_url():
    cfg = NewRelicConfig(api_key="k", account_id="999", region=NewRelicRegion.EU)
    exp = NewRelicExporter(cfg)
    parsed = urlparse(exp._events_url())
    assert parsed.hostname == "insights-collector.eu01.nr-data.net"


def test_nr_eu_region_metrics_url():
    cfg = NewRelicConfig(api_key="k", account_id="999", region=NewRelicRegion.EU)
    exp = NewRelicExporter(cfg)
    parsed = urlparse(exp._metrics_url())
    assert parsed.hostname == "metric-api.eu.newrelic.com"


def test_nr_double_close():
    cfg = NewRelicConfig(api_key="k", account_id="1")
    mock_client = MagicMock()
    exp = NewRelicExporter(cfg)
    exp._client = mock_client
    exp.close()
    exp.close()
    assert mock_client.close.call_count == 1


def test_nr_send_metric_with_timestamp():
    cfg = NewRelicConfig(api_key="k", account_id="1")
    exp = NewRelicExporter(cfg)
    mock_client = MagicMock()
    mock_client.post.return_value = _resp(202)
    m = NewRelicMetric("cpu", 0.7, timestamp=1700000000000)
    with patch.object(exp, "_make_client", return_value=mock_client):
        exp.send_metric(m)
    _, kwargs = mock_client.post.call_args
    item = kwargs["json"][0]["metrics"][0]
    assert item["timestamp"] == 1700000000000
