"""Comprehensive exporter tests — zero real network calls."""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch
from typing import Any
from urllib.parse import urlparse
import httpx

from sketchlog.exporters.base import ExporterError
from sketchlog.exporters.loki import LokiConfig, LokiStream, LokiExporter
from sketchlog.exporters.datadog import DatadogConfig, DatadogMetric, MetricType, DatadogExporter
from sketchlog.exporters.newrelic import (
    NewRelicConfig, NewRelicEvent, NewRelicMetric,
    NewRelicExporter, NewRelicRegion, NewRelicMetricType,
)


# ── helpers ───────────────────────────────────────────────────────────────────

def _resp(status: int, body: Any = None) -> MagicMock:
    mock = MagicMock(spec=httpx.Response)
    mock.status_code = status
    if status >= 400:
        err = httpx.HTTPStatusError(
            f"HTTP {status}",
            request=MagicMock(),
            response=MagicMock(status_code=status),
        )
        mock.raise_for_status.side_effect = err
    else:
        mock.raise_for_status.return_value = None
    if body is not None:
        mock.json.return_value = body
    return mock


def _client(status: int = 204, body: Any = None) -> MagicMock:
    c = MagicMock(spec=httpx.Client)
    c.post.return_value = _resp(status, body)
    return c


def _connect_error() -> httpx.ConnectError:
    """Create httpx.ConnectError safely — requires request= in all httpx versions."""
    return httpx.ConnectError("connection refused", request=MagicMock())


# ══════════════════════════════════════════════════════════════════════════════
# ExporterError
# ══════════════════════════════════════════════════════════════════════════════

def test_exporter_error_basic() -> None:
    e = ExporterError("boom")
    assert str(e) == "boom"
    assert e.status_code is None


def test_exporter_error_with_status() -> None:
    e = ExporterError("not found", status_code=404)
    assert e.status_code == 404


# ══════════════════════════════════════════════════════════════════════════════
# LokiConfig validation
# ══════════════════════════════════════════════════════════════════════════════

def test_loki_config_ok() -> None:
    cfg = LokiConfig(url="http://localhost:3100")
    assert cfg.url == "http://localhost:3100"


def test_loki_config_empty_url() -> None:
    with pytest.raises(ValueError, match="url"):
        LokiConfig(url="")


def test_loki_config_bad_timeout() -> None:
    with pytest.raises(ValueError, match="timeout"):
        LokiConfig(url="http://x", timeout=0)


def test_loki_config_missing_password() -> None:
    with pytest.raises(ValueError, match="password"):
        LokiConfig(url="http://x", username="u")


def test_loki_config_both_auth() -> None:
    with pytest.raises(ValueError, match="bearer_token"):
        LokiConfig(url="http://x", username="u", password="p", bearer_token="t")


# ══════════════════════════════════════════════════════════════════════════════
# LokiExporter
# ══════════════════════════════════════════════════════════════════════════════

def test_loki_push_ok() -> None:
    cfg = LokiConfig(url="http://loki:3100")
    c = _client(204)
    exp = LokiExporter(cfg, client=c)
    exp.push(["hello world"])
    c.post.assert_called_once()
    payload = c.post.call_args.kwargs["json"]
    assert "streams" in payload


def test_loki_push_with_labels() -> None:
    cfg = LokiConfig(url="http://loki:3100")
    c = _client(204)
    exp = LokiExporter(cfg, client=c)
    exp.push(["msg"], labels={"app": "test"})
    payload = c.post.call_args.kwargs["json"]
    assert payload["streams"][0]["stream"]["app"] == "test"


def test_loki_push_stream() -> None:
    cfg = LokiConfig(url="http://loki:3100")
    c = _client(204)
    exp = LokiExporter(cfg, client=c)
    stream = LokiStream(labels={"job": "x"}, lines=["line1"])
    exp.push_stream(stream)
    c.post.assert_called_once()


def test_loki_push_streams_batch() -> None:
    cfg = LokiConfig(url="http://loki:3100")
    c = _client(204)
    exp = LokiExporter(cfg, client=c)
    streams = [
        LokiStream(labels={"app": "a"}, lines=["a"]),
        LokiStream(labels={"app": "b"}, lines=["b"]),
    ]
    exp.push_streams(streams)
    payload = c.post.call_args.kwargs["json"]
    assert len(payload["streams"]) == 2


def test_loki_push_with_timestamps() -> None:
    cfg = LokiConfig(url="http://loki:3100")
    c = _client(204)
    exp = LokiExporter(cfg, client=c)
    exp.push(["t"], timestamps_ns=[1_000_000_000])
    payload = c.post.call_args.kwargs["json"]
    assert payload["streams"][0]["values"][0][0] == "1000000000"


def test_loki_context_manager() -> None:
    cfg = LokiConfig(url="http://loki:3100")
    c = _client(204)
    with LokiExporter(cfg, client=c) as exp:
        exp.push(["msg"])
    c.post.assert_called_once()


def test_loki_double_close() -> None:
    cfg = LokiConfig(url="http://loki:3100")
    exp = LokiExporter(cfg)
    exp.open()
    exp.close()
    exp.close()  # must not raise


def test_loki_http_error() -> None:
    cfg = LokiConfig(url="http://loki:3100")
    c = _client(500)
    exp = LokiExporter(cfg, client=c)
    with pytest.raises(ExporterError) as exc_info:
        exp.push(["err"])
    assert exc_info.value.status_code == 500


def test_loki_timeout_error() -> None:
    cfg = LokiConfig(url="http://loki:3100")
    c = MagicMock(spec=httpx.Client)
    c.post.side_effect = httpx.TimeoutException("timed out", request=MagicMock())
    exp = LokiExporter(cfg, client=c)
    with pytest.raises(ExporterError, match="timed out"):
        exp.push(["x"])


def test_loki_request_error() -> None:
    cfg = LokiConfig(url="http://loki:3100")
    c = MagicMock(spec=httpx.Client)
    c.post.side_effect = _connect_error()
    exp = LokiExporter(cfg, client=c)
    with pytest.raises(ExporterError, match="connection error"):
        exp.push(["x"])


def test_loki_bearer_auth() -> None:
    cfg = LokiConfig(url="http://loki:3100", bearer_token="mytoken")
    with patch("sketchlog.exporters.loki.httpx.Client") as MockClient:
        mock_instance = _client(204)
        MockClient.return_value = mock_instance
        exp = LokiExporter(cfg)
        exp.push(["line"])
    _, kwargs = MockClient.call_args
    assert kwargs.get("headers", {}).get("Authorization") == "Bearer mytoken"


def test_loki_basic_auth() -> None:
    cfg = LokiConfig(url="http://loki:3100", username="u", password="p")
    with patch("sketchlog.exporters.loki.httpx.Client") as MockClient:
        mock_instance = _client(204)
        MockClient.return_value = mock_instance
        exp = LokiExporter(cfg)
        exp.push(["line"])
    _, kwargs = MockClient.call_args
    assert kwargs.get("auth") == ("u", "p")


# ══════════════════════════════════════════════════════════════════════════════
# DatadogConfig validation
# ══════════════════════════════════════════════════════════════════════════════

def test_dd_config_ok() -> None:
    cfg = DatadogConfig(api_key="key123")
    assert cfg.site == "datadoghq.com"


def test_dd_config_empty_key() -> None:
    with pytest.raises(ValueError, match="api_key"):
        DatadogConfig(api_key="")


def test_dd_config_bad_timeout() -> None:
    with pytest.raises(ValueError, match="timeout"):
        DatadogConfig(api_key="k", timeout=-1)


# ══════════════════════════════════════════════════════════════════════════════
# DatadogExporter
# ══════════════════════════════════════════════════════════════════════════════

def test_dd_send_metric_ok() -> None:
    cfg = DatadogConfig(api_key="k")
    c = _client(202)
    exp = DatadogExporter(cfg, client=c)
    exp.send_metric(DatadogMetric("cpu", 0.5))
    c.post.assert_called_once()
    payload = c.post.call_args.kwargs["json"]
    assert payload["series"][0]["metric"] == "cpu"


def test_dd_send_metrics_batch() -> None:
    cfg = DatadogConfig(api_key="k")
    c = _client(202)
    exp = DatadogExporter(cfg, client=c)
    exp.send_metrics([
        DatadogMetric("cpu", 0.5, MetricType.GAUGE),
        DatadogMetric("reqs", 10, MetricType.COUNT),
    ])
    payload = c.post.call_args.kwargs["json"]
    assert len(payload["series"]) == 2


def test_dd_metric_prefix() -> None:
    cfg = DatadogConfig(api_key="k", metric_prefix="app.")
    c = _client(202)
    exp = DatadogExporter(cfg, client=c)
    exp.send_metric(DatadogMetric("latency", 1.0))
    payload = c.post.call_args.kwargs["json"]
    assert payload["series"][0]["metric"] == "app.latency"


def test_dd_default_tags() -> None:
    cfg = DatadogConfig(api_key="k", default_tags=["env:prod"])
    c = _client(202)
    exp = DatadogExporter(cfg, client=c)
    exp.send_metric(DatadogMetric("m", 1.0, tags=["app:x"]))
    payload = c.post.call_args.kwargs["json"]
    assert "env:prod" in payload["series"][0]["tags"]
    assert "app:x" in payload["series"][0]["tags"]


def test_dd_eu_site() -> None:
    cfg = DatadogConfig(api_key="k", site="datadoghq.eu")
    c = _client(202)
    exp = DatadogExporter(cfg, client=c)
    exp.send_metric(DatadogMetric("m", 1.0))
    url = c.post.call_args.args[0]
    assert "datadoghq.eu" in url


def test_dd_context_manager() -> None:
    cfg = DatadogConfig(api_key="k")
    c = _client(202)
    with DatadogExporter(cfg, client=c) as exp:
        exp.send_metric(DatadogMetric("m", 1.0))
    c.post.assert_called_once()


def test_dd_double_close() -> None:
    cfg = DatadogConfig(api_key="k")
    exp = DatadogExporter(cfg)
    exp.open()
    exp.close()
    exp.close()  # must not raise


def test_dd_http_error() -> None:
    cfg = DatadogConfig(api_key="k")
    c = _client(403)
    exp = DatadogExporter(cfg, client=c)
    with pytest.raises(ExporterError) as exc_info:
        exp.send_metric(DatadogMetric("m", 1.0))
    assert exc_info.value.status_code == 403


def test_dd_timeout_error() -> None:
    cfg = DatadogConfig(api_key="k")
    c = MagicMock(spec=httpx.Client)
    c.post.side_effect = httpx.TimeoutException("timeout", request=MagicMock())
    exp = DatadogExporter(cfg, client=c)
    with pytest.raises(ExporterError, match="timed out"):
        exp.send_metric(DatadogMetric("m", 1.0))


def test_dd_request_error() -> None:
    cfg = DatadogConfig(api_key="k")
    c = MagicMock(spec=httpx.Client)
    c.post.side_effect = _connect_error()
    exp = DatadogExporter(cfg, client=c)
    with pytest.raises(ExporterError, match="connection error"):
        exp.send_metric(DatadogMetric("m", 1.0))


def test_dd_host_tag() -> None:
    cfg = DatadogConfig(api_key="k")
    c = _client(202)
    exp = DatadogExporter(cfg, client=c)
    exp.send_metric(DatadogMetric("m", 1.0, host="server1"))
    payload = c.post.call_args.kwargs["json"]
    resources = payload["series"][0].get("resources", [])
    assert any(r.get("type") == "host" and r.get("name") == "server1" for r in resources)


# ══════════════════════════════════════════════════════════════════════════════
# NewRelicConfig validation
# ══════════════════════════════════════════════════════════════════════════════

def test_nr_config_ok() -> None:
    cfg = NewRelicConfig(api_key="NRII-key", account_id="123")
    assert cfg.region == NewRelicRegion.US


def test_nr_config_empty_key() -> None:
    with pytest.raises(ValueError, match="api_key"):
        NewRelicConfig(api_key="")


def test_nr_config_bad_timeout() -> None:
    with pytest.raises(ValueError, match="timeout"):
        NewRelicConfig(api_key="k", timeout=0)


# ══════════════════════════════════════════════════════════════════════════════
# NewRelicExporter — URL routing (exact hostname assertions)
# ══════════════════════════════════════════════════════════════════════════════

def test_nr_us_region_events_url() -> None:
    cfg = NewRelicConfig(api_key="k", account_id="123")
    exp = NewRelicExporter(cfg)
    parsed = urlparse(exp._events_url())
    assert parsed.hostname == "insights-collector.newrelic.com"


def test_nr_eu_region_events_url() -> None:
    cfg = NewRelicConfig(api_key="k", account_id="999", region=NewRelicRegion.EU)
    exp = NewRelicExporter(cfg)
    parsed = urlparse(exp._events_url())
    assert parsed.hostname == "insights-collector.eu01.nr-data.net"


def test_nr_us_region_metrics_url() -> None:
    cfg = NewRelicConfig(api_key="k", account_id="123")
    exp = NewRelicExporter(cfg)
    parsed = urlparse(exp._metrics_url())
    assert parsed.hostname == "metric-api.newrelic.com"


def test_nr_eu_region_metrics_url() -> None:
    cfg = NewRelicConfig(api_key="k", account_id="999", region=NewRelicRegion.EU)
    exp = NewRelicExporter(cfg)
    parsed = urlparse(exp._metrics_url())
    assert parsed.hostname == "metric-api.eu.newrelic.com"


# ══════════════════════════════════════════════════════════════════════════════
# NewRelicExporter — events
# ══════════════════════════════════════════════════════════════════════════════

def test_nr_send_event_ok() -> None:
    cfg = NewRelicConfig(api_key="k", account_id="123")
    c = _client(200)
    exp = NewRelicExporter(cfg, client=c)
    exp.send_event(NewRelicEvent("PageView", {"url": "/home"}))
    c.post.assert_called_once()
    payload = c.post.call_args.kwargs["json"]
    assert payload[0]["eventType"] == "PageView"


def test_nr_send_events_batch() -> None:
    cfg = NewRelicConfig(api_key="k", account_id="123")
    c = _client(200)
    exp = NewRelicExporter(cfg, client=c)
    exp.send_events([
        NewRelicEvent("Click", {"button": "submit"}),
        NewRelicEvent("View", {"page": "home"}),
    ])
    payload = c.post.call_args.kwargs["json"]
    assert len(payload) == 2


def test_nr_send_event_no_account_id() -> None:
    cfg = NewRelicConfig(api_key="k")
    c = _client(200)
    exp = NewRelicExporter(cfg, client=c)
    with pytest.raises(ValueError, match="account_id"):
        exp.send_event(NewRelicEvent("E", {}))


# ══════════════════════════════════════════════════════════════════════════════
# NewRelicExporter — metrics
# ══════════════════════════════════════════════════════════════════════════════

def test_nr_send_metric_ok() -> None:
    cfg = NewRelicConfig(api_key="k")
    c = _client(202)
    exp = NewRelicExporter(cfg, client=c)
    exp.send_metric(NewRelicMetric("cpu", 0.9, NewRelicMetricType.GAUGE))
    c.post.assert_called_once()
    payload = c.post.call_args.kwargs["json"]
    assert payload[0]["metrics"][0]["name"] == "cpu"


def test_nr_send_metrics_batch() -> None:
    cfg = NewRelicConfig(api_key="k")
    c = _client(202)
    exp = NewRelicExporter(cfg, client=c)
    exp.send_metrics([
        NewRelicMetric("m1", 1.0, NewRelicMetricType.GAUGE),
        NewRelicMetric("m2", 2.0, NewRelicMetricType.COUNT, interval_ms=1000),
    ])
    payload = c.post.call_args.kwargs["json"]
    assert len(payload[0]["metrics"]) == 2


def test_nr_summary_metric() -> None:
    cfg = NewRelicConfig(api_key="k")
    c = _client(202)
    exp = NewRelicExporter(cfg, client=c)
    summary_val = {"count": 10.0, "sum": 100.0, "min": 1.0, "max": 20.0}
    exp.send_metric(
        NewRelicMetric("latency", summary_val, NewRelicMetricType.SUMMARY, interval_ms=60000)
    )
    payload = c.post.call_args.kwargs["json"]
    assert payload[0]["metrics"][0]["value"] == summary_val


# ══════════════════════════════════════════════════════════════════════════════
# NewRelicExporter — lifecycle & errors
# ══════════════════════════════════════════════════════════════════════════════

def test_nr_context_manager() -> None:
    cfg = NewRelicConfig(api_key="k")
    c = _client(202)
    with NewRelicExporter(cfg, client=c) as exp:
        exp.send_metric(NewRelicMetric("m", 1.0))
    c.post.assert_called_once()


def test_nr_double_close() -> None:
    cfg = NewRelicConfig(api_key="k")
    exp = NewRelicExporter(cfg)
    exp.open()
    exp.close()
    exp.close()  # must not raise


def test_nr_http_error() -> None:
    cfg = NewRelicConfig(api_key="k")
    c = _client(400)
    exp = NewRelicExporter(cfg, client=c)
    with pytest.raises(ExporterError) as exc_info:
        exp.send_metric(NewRelicMetric("m", 1.0))
    assert exc_info.value.status_code == 400


def test_nr_timeout_error() -> None:
    cfg = NewRelicConfig(api_key="k")
    c = MagicMock(spec=httpx.Client)
    c.post.side_effect = httpx.TimeoutException("timeout", request=MagicMock())
    exp = NewRelicExporter(cfg, client=c)
    with pytest.raises(ExporterError, match="timed out"):
        exp.send_metric(NewRelicMetric("m", 1.0))


def test_nr_request_error() -> None:
    cfg = NewRelicConfig(api_key="k")
    c = MagicMock(spec=httpx.Client)
    c.post.side_effect = _connect_error()
    exp = NewRelicExporter(cfg, client=c)
    with pytest.raises(ExporterError, match="connection error"):
        exp.send_metric(NewRelicMetric("m", 1.0))
