"""Comprehensive tests for SketchLog export integrations.

All tests are deterministic and network-free.
"""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch
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
    r = MagicMock()
    r.status_code = status
    if status >= 400:
        r.raise_for_status.side_effect = httpx.HTTPStatusError(
            f"HTTP {status}",
            request=httpx.Request("POST", "http://test"),
            response=httpx.Response(status),
        )
    else:
        r.raise_for_status.return_value = None
    return r


def _mock_client(status: int = 204) -> MagicMock:
    mc = MagicMock(spec=httpx.Client)
    mc.post.return_value = _resp(status)
    mc.__enter__ = MagicMock(return_value=mc)
    mc.__exit__ = MagicMock(return_value=False)
    return mc


# ════════════════════════════════════════════════════════════════════════════
# ExporterError
# ════════════════════════════════════════════════════════════════════════════

def test_exporter_error_no_status():
    e = ExporterError("boom")
    assert str(e) == "boom"
    assert e.status_code is None


def test_exporter_error_with_status():
    e = ExporterError("unauthorized", status_code=401)
    assert e.status_code == 401


# ════════════════════════════════════════════════════════════════════════════
# LokiConfig
# ════════════════════════════════════════════════════════════════════════════

def test_loki_config_ok():
    cfg = LokiConfig(url="http://loki:3100")
    assert cfg.url == "http://loki:3100"


def test_loki_config_empty_url():
    with pytest.raises(ValueError, match="url"):
        LokiConfig(url="")


def test_loki_config_partial_auth():
    with pytest.raises(ValueError, match="password"):
        LokiConfig(url="http://loki:3100", username="u")


def test_loki_config_full_auth():
    cfg = LokiConfig(url="http://loki:3100", username="u", password="p")
    assert cfg.username == "u"


# ════════════════════════════════════════════════════════════════════════════
# LokiExporter — push
# ════════════════════════════════════════════════════════════════════════════

def test_loki_push_ok():
    cfg = LokiConfig(url="http://loki:3100")
    mc = _mock_client(204)
    with patch.object(LokiExporter, "_make_client", return_value=mc):
        exp = LokiExporter(cfg)
        exp.push(["hello"])
    mc.post.assert_called_once()
    mc.close.assert_called_once()


def test_loki_push_bearer_auth():
    cfg = LokiConfig(url="http://loki:3100", auth_token="mytoken")
    mc = _mock_client(204)
    with patch.object(LokiExporter, "_make_client", return_value=mc) as make:
        exp = LokiExporter(cfg)
        exp.push(["line"])
    mc.post.assert_called_once()


def test_loki_basic_auth_client():
    cfg = LokiConfig(url="http://loki:3100", username="u", password="p")
    with patch("sketchlog.exporters.loki.httpx.Client") as MockCls:
        MockCls.return_value = _mock_client(204)
        exp = LokiExporter(cfg)
        exp.push(["line"])
    call_kwargs = MockCls.call_args[1]
    assert call_kwargs.get("auth") == ("u", "p")


def test_loki_push_stream():
    cfg = LokiConfig(url="http://loki:3100")
    mc = _mock_client(204)
    stream = LokiStream(labels={"env": "prod"}, lines=["a", "b"])
    with patch.object(LokiExporter, "_make_client", return_value=mc):
        exp = LokiExporter(cfg)
        exp.push_stream(stream)
    mc.post.assert_called_once()


def test_loki_push_streams_batch():
    cfg = LokiConfig(url="http://loki:3100")
    mc = _mock_client(204)
    s1 = LokiStream(labels={"a": "1"}, lines=["x"])
    s2 = LokiStream(labels={"a": "2"}, lines=["y"])
    with patch.object(LokiExporter, "_make_client", return_value=mc):
        exp = LokiExporter(cfg)
        exp.push_streams([s1, s2])
    mc.post.assert_called_once()


def test_loki_push_with_timestamps():
    cfg = LokiConfig(url="http://loki:3100")
    mc = _mock_client(204)
    with patch.object(LokiExporter, "_make_client", return_value=mc):
        exp = LokiExporter(cfg)
        exp.push(["line"], timestamps_ns=[1_000_000_000])
    mc.post.assert_called_once()


# ════════════════════════════════════════════════════════════════════════════
# LokiExporter — context manager + lifecycle
# ════════════════════════════════════════════════════════════════════════════

def test_loki_context_manager():
    cfg = LokiConfig(url="http://loki:3100")
    mc = _mock_client(204)
    with patch.object(LokiExporter, "_make_client", return_value=mc):
        with LokiExporter(cfg) as exp:
            exp.push(["cm line"])
    mc.close.assert_called()


def test_loki_double_close():
    cfg = LokiConfig(url="http://loki:3100")
    mc = _mock_client(204)
    with patch.object(LokiExporter, "_make_client", return_value=mc):
        exp = LokiExporter(cfg)
        exp.open()
        exp.close()
        exp.close()  # must not raise
    mc.close.assert_called_once()


def test_loki_persistent_client_not_closed():
    cfg = LokiConfig(url="http://loki:3100")
    mc = _mock_client(204)
    exp = LokiExporter(cfg, client=mc)
    exp.push(["line"])
    mc.close.assert_not_called()


# ════════════════════════════════════════════════════════════════════════════
# LokiExporter — error handling
# ════════════════════════════════════════════════════════════════════════════

def test_loki_http_error():
    cfg = LokiConfig(url="http://loki:3100")
    mc = _mock_client(500)
    with patch.object(LokiExporter, "_make_client", return_value=mc):
        exp = LokiExporter(cfg)
        with pytest.raises(ExporterError) as exc_info:
            exp.push(["line"])
    assert exc_info.value.status_code == 500


def test_loki_timeout_error():
    cfg = LokiConfig(url="http://loki:3100")
    mc = MagicMock(spec=httpx.Client)
    mc.post.side_effect = httpx.TimeoutException("timed out", request=httpx.Request("POST", "http://loki"))
    with patch.object(LokiExporter, "_make_client", return_value=mc):
        exp = LokiExporter(cfg)
        with pytest.raises(ExporterError, match="timed out"):
            exp.push(["line"])


def test_loki_request_error():
    cfg = LokiConfig(url="http://loki:3100")
    mc = MagicMock(spec=httpx.Client)
    mc.post.side_effect = httpx.ConnectError("conn refused", request=httpx.Request("POST", "http://loki"))
    with patch.object(LokiExporter, "_make_client", return_value=mc):
        exp = LokiExporter(cfg)
        with pytest.raises(ExporterError, match="failed"):
            exp.push(["line"])


# ════════════════════════════════════════════════════════════════════════════
# DatadogConfig
# ════════════════════════════════════════════════════════════════════════════

def test_datadog_config_ok():
    cfg = DatadogConfig(api_key="key123")
    assert cfg.site == "datadoghq.com"


def test_datadog_config_empty_key():
    with pytest.raises(ValueError, match="api_key"):
        DatadogConfig(api_key="")


def test_datadog_config_eu_site():
    cfg = DatadogConfig(api_key="k", site="datadoghq.eu")
    assert "eu" in cfg.site


# ════════════════════════════════════════════════════════════════════════════
# DatadogExporter
# ════════════════════════════════════════════════════════════════════════════

def test_datadog_send_metric_ok():
    cfg = DatadogConfig(api_key="k")
    mc = _mock_client(202)
    with patch.object(DatadogExporter, "_make_client", return_value=mc):
        exp = DatadogExporter(cfg)
        exp.send_metric(DatadogMetric("cpu", 0.5))
    mc.post.assert_called_once()
    mc.close.assert_called_once()


def test_datadog_send_metrics_batch():
    cfg = DatadogConfig(api_key="k")
    mc = _mock_client(202)
    metrics = [DatadogMetric("a", 1.0), DatadogMetric("b", 2.0, MetricType.COUNT)]
    with patch.object(DatadogExporter, "_make_client", return_value=mc):
        exp = DatadogExporter(cfg)
        exp.send_metrics(metrics)
    mc.post.assert_called_once()


def test_datadog_prefix_applied():
    cfg = DatadogConfig(api_key="k", metric_prefix="app.")
    mc = _mock_client(202)
    with patch.object(DatadogExporter, "_make_client", return_value=mc):
        exp = DatadogExporter(cfg)
        exp.send_metric(DatadogMetric("req", 1.0))
    payload = mc.post.call_args[1]["json"]
    assert payload["series"][0]["metric"] == "app.req"


def test_datadog_default_tags_merged():
    cfg = DatadogConfig(api_key="k", default_tags=["env:prod"])
    mc = _mock_client(202)
    with patch.object(DatadogExporter, "_make_client", return_value=mc):
        exp = DatadogExporter(cfg)
        exp.send_metric(DatadogMetric("m", 1.0, tags=["host:web"]))
    payload = mc.post.call_args[1]["json"]
    tags = payload["series"][0]["tags"]
    assert "env:prod" in tags
    assert "host:web" in tags


def test_datadog_context_manager():
    cfg = DatadogConfig(api_key="k")
    mc = _mock_client(202)
    with patch.object(DatadogExporter, "_make_client", return_value=mc):
        with DatadogExporter(cfg) as exp:
            exp.send_metric(DatadogMetric("x", 1.0))
    mc.close.assert_called()


def test_datadog_double_close():
    cfg = DatadogConfig(api_key="k")
    mc = _mock_client(202)
    with patch.object(DatadogExporter, "_make_client", return_value=mc):
        exp = DatadogExporter(cfg)
        exp.open()
        exp.close()
        exp.close()
    mc.close.assert_called_once()


def test_datadog_http_error():
    cfg = DatadogConfig(api_key="k")
    mc = _mock_client(403)
    with patch.object(DatadogExporter, "_make_client", return_value=mc):
        exp = DatadogExporter(cfg)
        with pytest.raises(ExporterError) as exc_info:
            exp.send_metric(DatadogMetric("m", 1.0))
    assert exc_info.value.status_code == 403


def test_datadog_timeout_error():
    cfg = DatadogConfig(api_key="k")
    mc = MagicMock(spec=httpx.Client)
    mc.post.side_effect = httpx.TimeoutException("dd timeout", request=httpx.Request("POST", "http://dd"))
    with patch.object(DatadogExporter, "_make_client", return_value=mc):
        exp = DatadogExporter(cfg)
        with pytest.raises(ExporterError, match="timed out"):
            exp.send_metric(DatadogMetric("m", 1.0))


def test_datadog_request_error():
    cfg = DatadogConfig(api_key="k")
    mc = MagicMock(spec=httpx.Client)
    mc.post.side_effect = httpx.ConnectError("no conn", request=httpx.Request("POST", "http://dd"))
    with patch.object(DatadogExporter, "_make_client", return_value=mc):
        exp = DatadogExporter(cfg)
        with pytest.raises(ExporterError, match="failed"):
            exp.send_metric(DatadogMetric("m", 1.0))


def test_datadog_persistent_client_not_closed():
    cfg = DatadogConfig(api_key="k")
    mc = _mock_client(202)
    exp = DatadogExporter(cfg, client=mc)
    exp.send_metric(DatadogMetric("m", 1.0))
    mc.close.assert_not_called()


# ════════════════════════════════════════════════════════════════════════════
# NewRelicConfig
# ════════════════════════════════════════════════════════════════════════════

def test_nr_config_ok():
    cfg = NewRelicConfig(api_key="k", account_id="123")
    assert cfg.region == NewRelicRegion.US


def test_nr_config_empty_key():
    with pytest.raises(ValueError, match="api_key"):
        NewRelicConfig(api_key="")


def test_nr_config_eu_region():
    cfg = NewRelicConfig(api_key="k", account_id="1", region=NewRelicRegion.EU)
    assert cfg.region == NewRelicRegion.EU


# ════════════════════════════════════════════════════════════════════════════
# NewRelicExporter — URL helpers
# ════════════════════════════════════════════════════════════════════════════

def test_nr_us_region_events_url():
    cfg = NewRelicConfig(api_key="k", account_id="123")
    exp = NewRelicExporter(cfg)
    parsed = urlparse(exp._events_url())
    assert parsed.hostname is not None
    assert parsed.hostname == "insights-collector.nr-data.net"


def test_nr_eu_region_events_url():
    cfg = NewRelicConfig(api_key="k", account_id="999", region=NewRelicRegion.EU)
    exp = NewRelicExporter(cfg)
    parsed = urlparse(exp._events_url())
    assert parsed.hostname is not None
    assert parsed.hostname == "insights-collector.eu01.nr-data.net"


def test_nr_us_region_metrics_url():
    cfg = NewRelicConfig(api_key="k", account_id="123")
    exp = NewRelicExporter(cfg)
    parsed = urlparse(exp._metrics_url())
    assert parsed.hostname is not None
    assert parsed.hostname == "metric-api.newrelic.com"


def test_nr_eu_region_metrics_url():
    cfg = NewRelicConfig(api_key="k", account_id="999", region=NewRelicRegion.EU)
    exp = NewRelicExporter(cfg)
    parsed = urlparse(exp._metrics_url())
    assert parsed.hostname is not None
    assert parsed.hostname == "metric-api.eu.newrelic.com"


# ════════════════════════════════════════════════════════════════════════════
# NewRelicExporter — Events API
# ════════════════════════════════════════════════════════════════════════════

def test_nr_send_event_ok():
    cfg = NewRelicConfig(api_key="k", account_id="123")
    mc = _mock_client(200)
    with patch.object(NewRelicExporter, "_make_client", return_value=mc):
        exp = NewRelicExporter(cfg)
        exp.send_event(NewRelicEvent("Purchase", {"amount": 9.99}))
    mc.post.assert_called_once()
    mc.close.assert_called_once()


def test_nr_send_events_batch():
    cfg = NewRelicConfig(api_key="k", account_id="123")
    mc = _mock_client(200)
    events = [NewRelicEvent("A", {}), NewRelicEvent("B", {"x": 1})]
    with patch.object(NewRelicExporter, "_make_client", return_value=mc):
        exp = NewRelicExporter(cfg)
        exp.send_events(events)
    mc.post.assert_called_once()


def test_nr_event_with_timestamp():
    cfg = NewRelicConfig(api_key="k", account_id="123")
    mc = _mock_client(200)
    with patch.object(NewRelicExporter, "_make_client", return_value=mc):
        exp = NewRelicExporter(cfg)
        exp.send_event(NewRelicEvent("T", timestamp=1_700_000_000.0))
    payload = mc.post.call_args[1]["json"]
    assert payload[0]["timestamp"] == 1_700_000_000


# ════════════════════════════════════════════════════════════════════════════
# NewRelicExporter — Metric API
# ════════════════════════════════════════════════════════════════════════════

def test_nr_send_metric_ok():
    cfg = NewRelicConfig(api_key="k", account_id="123")
    mc = _mock_client(202)
    with patch.object(NewRelicExporter, "_make_client", return_value=mc):
        exp = NewRelicExporter(cfg)
        exp.send_metric(NewRelicMetric("cpu", 0.9))
    mc.post.assert_called_once()


def test_nr_send_metrics_batch():
    cfg = NewRelicConfig(api_key="k", account_id="123")
    mc = _mock_client(202)
    metrics = [
        NewRelicMetric("a", 1.0),
        NewRelicMetric("b", 2.0, NewRelicMetricType.COUNT, interval_ms=60000),
    ]
    with patch.object(NewRelicExporter, "_make_client", return_value=mc):
        exp = NewRelicExporter(cfg)
        exp.send_metrics(metrics)
    mc.post.assert_called_once()


def test_nr_summary_metric():
    cfg = NewRelicConfig(api_key="k", account_id="123")
    mc = _mock_client(202)
    val = {"count": 10, "sum": 100.0, "min": 1.0, "max": 20.0}
    with patch.object(NewRelicExporter, "_make_client", return_value=mc):
        exp = NewRelicExporter(cfg)
        exp.send_metric(NewRelicMetric("lat", val, NewRelicMetricType.SUMMARY, interval_ms=1000))
    mc.post.assert_called_once()


# ════════════════════════════════════════════════════════════════════════════
# NewRelicExporter — lifecycle + errors
# ════════════════════════════════════════════════════════════════════════════

def test_nr_context_manager():
    cfg = NewRelicConfig(api_key="k", account_id="123")
    mc = _mock_client(200)
    with patch.object(NewRelicExporter, "_make_client", return_value=mc):
        with NewRelicExporter(cfg) as exp:
            exp.send_event(NewRelicEvent("X", {}))
    mc.close.assert_called()


def test_nr_double_close():
    cfg = NewRelicConfig(api_key="k", account_id="123")
    mc = _mock_client(200)
    with patch.object(NewRelicExporter, "_make_client", return_value=mc):
        exp = NewRelicExporter(cfg)
        exp.open()
        exp.close()
        exp.close()
    mc.close.assert_called_once()


def test_nr_http_error():
    cfg = NewRelicConfig(api_key="k", account_id="123")
    mc = _mock_client(403)
    with patch.object(NewRelicExporter, "_make_client", return_value=mc):
        exp = NewRelicExporter(cfg)
        with pytest.raises(ExporterError) as exc_info:
            exp.send_event(NewRelicEvent("X", {}))
    assert exc_info.value.status_code == 403


def test_nr_timeout_error():
    cfg = NewRelicConfig(api_key="k", account_id="123")
    mc = MagicMock(spec=httpx.Client)
    mc.post.side_effect = httpx.TimeoutException("nr timeout", request=httpx.Request("POST", "http://nr"))
    with patch.object(NewRelicExporter, "_make_client", return_value=mc):
        exp = NewRelicExporter(cfg)
        with pytest.raises(ExporterError, match="timed out"):
            exp.send_event(NewRelicEvent("X", {}))


def test_nr_request_error():
    cfg = NewRelicConfig(api_key="k", account_id="123")
    mc = MagicMock(spec=httpx.Client)
    mc.post.side_effect = httpx.ConnectError("no conn", request=httpx.Request("POST", "http://nr"))
    with patch.object(NewRelicExporter, "_make_client", return_value=mc):
        exp = NewRelicExporter(cfg)
        with pytest.raises(ExporterError, match="failed"):
            exp.send_event(NewRelicEvent("X", {}))


def test_nr_persistent_client_not_closed():
    cfg = NewRelicConfig(api_key="k", account_id="123")
    mc = _mock_client(200)
    exp = NewRelicExporter(cfg, client=mc)
    exp.send_event(NewRelicEvent("X", {}))
    mc.close.assert_not_called()
