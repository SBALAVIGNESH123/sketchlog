from __future__ import annotations

import json
from pathlib import Path
from typing import Dict

import pytest

from sketchlog.agent import AgentConfigError, SketchLogAgent, load_config, parse_prometheus_text


def test_parse_prometheus_text_with_labels_and_non_finite_skip() -> None:
    text = """
# HELP http_request_duration_seconds duration
http_request_duration_seconds{route="/checkout",method="GET"} 0.123
http_requests_total{route="/checkout"} 41
broken_metric NaN
"""
    samples = parse_prometheus_text(text)
    assert len(samples) == 2
    assert samples[0].name == "http_request_duration_seconds"
    assert samples[0].labels["route"] == "/checkout"
    assert samples[0].value == pytest.approx(0.123)


def test_agent_forwards_latency_and_counter_delta(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = tmp_path / "agent.json"
    config_path.write_text(
        json.dumps(
            {
                "server": {"endpoint": "https://sketchlog.example.com", "namespace": "prod", "auth_token": "secret"},
                "prometheus": {"targets": [{"name": "app", "url": "https://app.example.com/metrics"}]},
                "mappings": [
                    {"metric": "request_duration_seconds", "kind": "latency", "stream": "api.latency", "value_scale": 1000},
                    {"metric": "requests_total", "kind": "event", "stream": "api.events", "event_name": "requests"},
                ],
            }
        ),
        encoding="utf-8",
    )
    agent = SketchLogAgent(load_config(str(config_path)))
    scrapes = ["request_duration_seconds 0.100\nrequests_total 10\n", "request_duration_seconds 0.125\nrequests_total 13\n"]
    sent: list[tuple[str, Dict[str, object]]] = []

    def fake_scrape(_target: object) -> str:
        return scrapes.pop(0)

    def fake_send(stream: str, batch: object) -> None:
        sent.append((stream, batch.as_payload()))

    monkeypatch.setattr(agent, "_scrape_target", fake_scrape)
    monkeypatch.setattr(agent, "_send_batch", fake_send)

    first = agent.run_once()
    second = agent.run_once()

    assert first.forwarded_items == 1
    assert second.forwarded_items == 2
    assert sent[0] == ("api.latency", {"latencies": [100.0], "uniques": [], "events": {}})
    assert sent[1] == ("api.latency", {"latencies": [125.0], "uniques": [], "events": {}})
    assert sent[2] == ("api.events", {"latencies": [], "uniques": [], "events": {"requests": 3}})


def test_agent_label_filter_and_absolute_event_mode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = tmp_path / "agent.json"
    config_path.write_text(
        json.dumps(
            {
                "server": {"endpoint": "http://localhost:8000"},
                "prometheus": {"targets": [{"name": "app", "url": "http://app/metrics"}]},
                "mappings": [
                    {
                        "metric": "errors_total",
                        "kind": "event",
                        "stream": "api.events",
                        "event_name": "errors",
                        "counter_mode": "absolute",
                        "label_filters": {"status": "500"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    agent = SketchLogAgent(load_config(str(config_path)))
    sent: list[tuple[str, Dict[str, object]]] = []
    monkeypatch.setattr(agent, "_scrape_target", lambda _target: 'errors_total{status="200"} 5\nerrors_total{status="500"} 2\n')
    monkeypatch.setattr(agent, "_send_batch", lambda stream, batch: sent.append((stream, batch.as_payload())))

    stats = agent.run_once()

    assert stats.forwarded_items == 1
    assert sent == [("api.events", {"latencies": [], "uniques": [], "events": {"errors": 2}})]


def test_config_validation_rejects_unsafe_stream_path(tmp_path: Path) -> None:
    config_path = tmp_path / "agent.json"
    config_path.write_text(
        json.dumps(
            {
                "server": {"endpoint": "http://localhost:8000"},
                "prometheus": {"targets": [{"name": "app", "url": "http://app/metrics"}]},
                "mappings": [{"metric": "x", "kind": "latency", "stream": "../bad"}],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(AgentConfigError):
        load_config(str(config_path))


def test_auth_token_env_is_preferred(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SKETCHLOG_AGENT_TOKEN", "from-env")
    config_path = tmp_path / "agent.json"
    config_path.write_text(
        json.dumps(
            {
                "server": {"endpoint": "http://localhost:8000", "auth_token": "inline", "auth_token_env": "SKETCHLOG_AGENT_TOKEN"},
                "prometheus": {"targets": [{"name": "app", "url": "http://app/metrics"}]},
                "mappings": [{"metric": "x", "kind": "latency", "stream": "x.latency"}],
            }
        ),
        encoding="utf-8",
    )
    cfg = load_config(str(config_path))
    assert cfg.server.resolved_auth_token() == "from-env"
