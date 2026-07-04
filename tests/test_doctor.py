from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping

import pytest

from sketchlog.doctor import CheckStatus, DoctorOptions, exit_code, format_text, main, redact_url, run_doctor


def _healthy_request(url: str, *, headers: Mapping[str, str], timeout: float) -> tuple[int, str, str]:
    assert timeout > 0
    if url.endswith("/health"):
        return 200, '{"status":"ok"}', "application/json"
    if url.endswith("/ready"):
        return 200, '{"status":"ready"}', "application/json"
    if url.endswith("/metrics"):
        return 200, "# HELP sketchlog_http_requests_total Total\n# TYPE sketchlog_http_requests_total counter\nsketchlog_http_requests_total 1\n", "text/plain"
    raise AssertionError(f"unexpected URL {url}")


def test_doctor_healthy_deployment_with_warnings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sketchlog.doctor._request_text", _healthy_request)

    report = run_doctor(DoctorOptions(endpoint="http://localhost:8000"))

    assert report.summary.status is CheckStatus.WARN
    assert report.summary.fail_count == 0
    assert any(check.name == "health" and check.status is CheckStatus.PASS for check in report.checks)
    assert any(check.name == "metrics" and check.status is CheckStatus.PASS for check in report.checks)
    assert "SKETCHLOG_AUTH_TOKEN" in format_text(report)
    assert exit_code(report) == 0


def test_doctor_strict_mode_turns_warnings_into_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sketchlog.doctor._request_text", _healthy_request)

    report = run_doctor(DoctorOptions(endpoint="http://localhost:8000", strict=True))

    assert report.summary.status is CheckStatus.FAIL
    assert report.summary.fail_count == 0
    assert exit_code(report) == 1


def test_doctor_failure_when_required_endpoint_is_down(monkeypatch: pytest.MonkeyPatch) -> None:
    def failing_request(url: str, *, headers: Mapping[str, str], timeout: float) -> tuple[int, str, str]:
        if url.endswith("/health"):
            return 503, "service unavailable", "text/plain"
        return _healthy_request(url, headers=headers, timeout=timeout)

    monkeypatch.setattr("sketchlog.doctor._request_text", failing_request)

    report = run_doctor(DoctorOptions(endpoint="https://sketchlog.example.com", auth_token="super-secret", expect_auth=True, expect_tls=True))

    assert report.summary.status is CheckStatus.FAIL
    assert any(check.name == "health" and check.status is CheckStatus.FAIL for check in report.checks)
    rendered = format_text(report)
    assert "Auth token supplied" in rendered
    assert "super-secret" not in rendered


def test_doctor_json_output_and_redaction(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr("sketchlog.doctor._request_text", _healthy_request)

    code = main([
        "--endpoint",
        "https://user:pass@example.com:8443?token=abc123&region=iad",
        "--auth-token",
        "super-secret",
        "--expect-auth",
        "--expect-tls",
        "--format",
        "json",
    ])

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["endpoint"] == "https://<redacted>@example.com:8443?token=%3Credacted%3E&region=iad"
    assert "super-secret" not in json.dumps(payload)
    assert payload["summary"]["fail_count"] == 0


def test_doctor_optional_config_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sketchlog.doctor._request_text", _healthy_request)
    otel = tmp_path / "collector.yaml"
    otel.write_text("exporters:\n  sketchlog:\n    endpoint: http://localhost:8000\n", encoding="utf-8")
    prometheus = tmp_path / "prometheus.yml"
    prometheus.write_text("scrape_configs:\n- job_name: sketchlog\n  metrics_path: /metrics\n", encoding="utf-8")
    dashboard = tmp_path / "dashboard.json"
    dashboard.write_text("{}", encoding="utf-8")
    retention = tmp_path / "retention.yaml"
    retention.write_text("retention: 30d\n", encoding="utf-8")
    backup = tmp_path / "backup.yaml"
    backup.write_text("backup: nightly\n", encoding="utf-8")

    report = run_doctor(
        DoctorOptions(
            endpoint="https://sketchlog.example.com",
            auth_token="token",
            expect_auth=True,
            expect_tls=True,
            expect_storage=True,
            namespace_quota_mb=128,
            mesh_enabled=True,
            cluster_token="cluster-secret",
            otel_config=otel,
            prometheus_config=prometheus,
            grafana_dashboard=dashboard,
            retention_policy=retention,
            backup_policy=backup,
        )
    )

    assert report.summary.fail_count == 0
    assert any(check.name == "otel-config-content" and check.status is CheckStatus.PASS for check in report.checks)
    assert any(check.name == "namespace-quota" and check.status is CheckStatus.PASS for check in report.checks)
    assert any(check.name == "cluster-secret" and check.status is CheckStatus.PASS for check in report.checks)


def test_doctor_invalid_endpoint_fails_without_network() -> None:
    report = run_doctor(DoctorOptions(endpoint="localhost:8000"))

    assert report.summary.status is CheckStatus.FAIL
    assert report.summary.fail_count == 1
    assert report.checks[0].name == "endpoint"


def test_redact_url_removes_credentials_and_secret_query_values() -> None:
    safe = redact_url("https://user:pass@example.com/path?token=abc&region=iad&api_key=xyz")

    assert "user" not in safe
    assert "pass" not in safe
    assert "abc" not in safe
    assert "xyz" not in safe
    assert "region=iad" in safe



def test_doctor_rejects_query_fragments_when_building_probe_urls(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []

    def healthy_request(url: str, *, headers: Mapping[str, str], timeout: float) -> tuple[int, str, str]:
        seen.append(url)
        return _healthy_request(url, headers=headers, timeout=timeout)

    monkeypatch.setattr("sketchlog.doctor._request_text", healthy_request)
    run_doctor(DoctorOptions(endpoint="https://sketchlog.example.com/base?token=secret#frag", auth_token="super-secret"))

    assert "https://sketchlog.example.com/base/health" in seen
    assert all("token=secret" not in url and "#frag" not in url for url in seen)


def test_doctor_skips_follow_up_metrics_format_after_http_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def failing_metrics(url: str, *, headers: Mapping[str, str], timeout: float) -> tuple[int, str, str]:
        if url.endswith("/metrics"):
            return 503, "sketchlog_api_key=super-secret", "text/plain"
        return _healthy_request(url, headers=headers, timeout=timeout)

    monkeypatch.setattr("sketchlog.doctor._request_text", failing_metrics)
    report = run_doctor(DoctorOptions(endpoint="https://sketchlog.example.com", auth_token="super-secret"))

    assert any(check.name == "metrics" and check.status is CheckStatus.FAIL for check in report.checks)
    assert not any(check.name == "prometheus-format" for check in report.checks)
    rendered = format_text(report)
    assert "super-secret" not in rendered
    assert "api_key=<redacted>" in rendered


def test_doctor_does_not_accept_loose_health_ready_substrings(monkeypatch: pytest.MonkeyPatch) -> None:
    def misleading_request(url: str, *, headers: Mapping[str, str], timeout: float) -> tuple[int, str, str]:
        if url.endswith("/health"):
            return 200, '{"status":"not_ok"}', "application/json"
        if url.endswith("/ready"):
            return 200, "not ready", "text/plain"
        return _healthy_request(url, headers=headers, timeout=timeout)

    monkeypatch.setattr("sketchlog.doctor._request_text", misleading_request)
    report = run_doctor(DoctorOptions(endpoint="https://sketchlog.example.com", expect_storage=True))

    assert any(check.name == "health" and check.status is CheckStatus.WARN for check in report.checks)
    assert any(check.name == "ready" and check.status is CheckStatus.WARN for check in report.checks)
    assert any(check.name == "storage-readiness" and check.status is CheckStatus.WARN for check in report.checks)


def test_doctor_sends_cluster_token_on_probes(monkeypatch: pytest.MonkeyPatch) -> None:
    observed_headers: list[Mapping[str, str]] = []

    def header_request(url: str, *, headers: Mapping[str, str], timeout: float) -> tuple[int, str, str]:
        observed_headers.append(dict(headers))
        return _healthy_request(url, headers=headers, timeout=timeout)

    monkeypatch.setattr("sketchlog.doctor._request_text", header_request)
    run_doctor(DoctorOptions(endpoint="https://sketchlog.example.com", cluster_token="cluster-secret", mesh_enabled=True))

    assert observed_headers
    assert all(headers.get("X-SketchLog-Cluster-Token") == "cluster-secret" for headers in observed_headers)


def test_doctor_cli_rejects_non_positive_timeout() -> None:
    with pytest.raises(SystemExit):
        main(["--endpoint", "https://sketchlog.example.com", "--timeout", "0"])
