"""Production readiness checks for SketchLog deployments.

This module intentionally uses only the Python standard library so the doctor
command can run in minimal environments before optional server dependencies are
installed. It checks a remote/local SketchLog HTTP endpoint plus optional local
configuration files and returns deterministic PASS/WARN/FAIL results.
"""

from __future__ import annotations

import argparse
import json
import re
import ssl
import sys
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen


class CheckStatus(str, Enum):
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"


@dataclass(frozen=True)
class DoctorCheck:
    category: str
    name: str
    status: CheckStatus
    message: str
    detail: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        return data


@dataclass(frozen=True)
class DoctorSummary:
    status: CheckStatus
    pass_count: int
    warn_count: int
    fail_count: int
    strict: bool

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        return data


@dataclass(frozen=True)
class DoctorReport:
    endpoint: str
    summary: DoctorSummary
    checks: List[DoctorCheck]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "endpoint": self.endpoint,
            "summary": self.summary.to_dict(),
            "checks": [check.to_dict() for check in self.checks],
        }


@dataclass(frozen=True)
class DoctorOptions:
    endpoint: str
    auth_token: Optional[str] = None
    cluster_token: Optional[str] = None
    timeout_seconds: float = 3.0
    strict: bool = False
    expect_auth: bool = False
    expect_tls: bool = False
    expect_storage: bool = False
    mesh_enabled: bool = False
    namespace_quota_mb: Optional[float] = None
    repo_root: Optional[Path] = None
    otel_config: Optional[Path] = None
    prometheus_config: Optional[Path] = None
    grafana_dashboard: Optional[Path] = None
    retention_policy: Optional[Path] = None
    backup_policy: Optional[Path] = None


_SECRET_QUERY_KEYS = {"token", "auth", "authorization", "password", "secret", "key", "apikey", "api_key"}
_BEARER_RE = re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+\-/=]+")
_TOKEN_RE = re.compile(r"(?i)(token=)[^\s&]+")


def redact_text(value: str) -> str:
    """Redact obvious credentials from diagnostic text."""
    value = _BEARER_RE.sub(r"\1<redacted>", value)
    value = _TOKEN_RE.sub(r"\1<redacted>", value)
    return value


def redact_url(value: str) -> str:
    """Return a URL safe for logs and JSON reports."""
    parts = urlsplit(value)
    netloc = parts.netloc
    if "@" in netloc:
        host = netloc.rsplit("@", 1)[1]
        netloc = f"<redacted>@{host}"
    query_items = []
    for key, val in parse_qsl(parts.query, keep_blank_values=True):
        query_items.append((key, "<redacted>" if key.lower() in _SECRET_QUERY_KEYS else val))
    return urlunsplit((parts.scheme, netloc, parts.path, urlencode(query_items), parts.fragment))


def _join_url(endpoint: str, path: str) -> str:
    base = endpoint.rstrip("/")
    return f"{base}{path}"


def _request_text(url: str, *, headers: Mapping[str, str], timeout: float) -> tuple[int, str, str]:
    request = Request(url, headers=dict(headers), method="GET")
    try:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310 - user-supplied endpoint is the point of this diagnostic tool.
            body = response.read(64_000).decode("utf-8", errors="replace")
            return int(response.status), body, response.headers.get("content-type", "")
    except HTTPError as exc:
        body = exc.read(16_000).decode("utf-8", errors="replace")
        return int(exc.code), body, exc.headers.get("content-type", "")
    except (URLError, TimeoutError, OSError, ssl.SSLError) as exc:
        raise ConnectionError(redact_text(str(exc))) from exc


def _status_from_http(status: int) -> CheckStatus:
    return CheckStatus.PASS if 200 <= status < 300 else CheckStatus.FAIL


def _http_check(
    checks: List[DoctorCheck],
    *,
    category: str,
    name: str,
    url: str,
    headers: Mapping[str, str],
    timeout: float,
    success_message: str,
    content_predicate: Optional[tuple[str, str]] = None,
) -> Optional[str]:
    try:
        status, body, content_type = _request_text(url, headers=headers, timeout=timeout)
    except ConnectionError as exc:
        checks.append(DoctorCheck(category, name, CheckStatus.FAIL, f"{name} endpoint is unreachable", str(exc)))
        return None

    if _status_from_http(status) is CheckStatus.FAIL:
        checks.append(
            DoctorCheck(
                category,
                name,
                CheckStatus.FAIL,
                f"{name} endpoint returned HTTP {status}",
                redact_text(body[:500]),
            )
        )
        return body

    if content_predicate:
        expected, warning = content_predicate
        haystack = f"{content_type}\n{body}"
        if expected not in haystack:
            checks.append(DoctorCheck(category, name, CheckStatus.WARN, warning))
            return body

    checks.append(DoctorCheck(category, name, CheckStatus.PASS, success_message))
    return body


def _file_check(checks: List[DoctorCheck], *, category: str, name: str, path: Optional[Path], missing: str, present: str) -> None:
    if path is None:
        checks.append(DoctorCheck(category, name, CheckStatus.WARN, missing))
        return
    if not path.exists():
        checks.append(DoctorCheck(category, name, CheckStatus.FAIL, f"Configured file does not exist: {path}"))
        return
    if not path.is_file():
        checks.append(DoctorCheck(category, name, CheckStatus.FAIL, f"Configured path is not a file: {path}"))
        return
    checks.append(DoctorCheck(category, name, CheckStatus.PASS, present, str(path)))


def _contains_any(path: Path, needles: Iterable[str]) -> bool:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    lowered = text.lower()
    return any(needle.lower() in lowered for needle in needles)


def run_doctor(options: DoctorOptions) -> DoctorReport:
    checks: List[DoctorCheck] = []
    endpoint = options.endpoint.strip()
    safe_endpoint = redact_url(endpoint)
    parts = urlsplit(endpoint)

    if parts.scheme not in {"http", "https"} or not parts.netloc:
        checks.append(DoctorCheck("configuration", "endpoint", CheckStatus.FAIL, "Endpoint must be an absolute http:// or https:// URL", safe_endpoint))
        return _build_report(safe_endpoint, checks, options.strict)

    checks.append(DoctorCheck("configuration", "endpoint", CheckStatus.PASS, "Endpoint URL is valid", safe_endpoint))

    headers: Dict[str, str] = {"User-Agent": "sketchlog-doctor/1.0"}
    if options.auth_token:
        headers["X-SketchLog-Auth-Token"] = options.auth_token

    if parts.scheme == "https":
        checks.append(DoctorCheck("security", "tls", CheckStatus.PASS, "Endpoint uses HTTPS"))
    else:
        status = CheckStatus.FAIL if options.expect_tls else CheckStatus.WARN
        checks.append(DoctorCheck("security", "tls", status, "Endpoint does not use HTTPS; use TLS for production deployments"))

    if options.auth_token:
        checks.append(DoctorCheck("security", "auth-token", CheckStatus.PASS, "Auth token supplied for protected /v1 endpoints"))
    elif options.expect_auth:
        checks.append(DoctorCheck("security", "auth-token", CheckStatus.FAIL, "Auth token expected but not supplied"))
    else:
        checks.append(DoctorCheck("security", "auth-token", CheckStatus.WARN, "No auth token supplied; ensure production deployments require SKETCHLOG_AUTH_TOKEN or namespace tokens"))

    _http_check(
        checks,
        category="server",
        name="health",
        url=_join_url(endpoint, "/health"),
        headers=headers,
        timeout=options.timeout_seconds,
        success_message="/health endpoint is reachable",
        content_predicate=("ok", "/health did not include the expected ok status"),
    )
    _http_check(
        checks,
        category="server",
        name="ready",
        url=_join_url(endpoint, "/ready"),
        headers=headers,
        timeout=options.timeout_seconds,
        success_message="/ready endpoint reports readiness",
        content_predicate=("ready", "/ready did not include the expected ready status"),
    )
    metrics_body = _http_check(
        checks,
        category="observability",
        name="metrics",
        url=_join_url(endpoint, "/metrics"),
        headers=headers,
        timeout=options.timeout_seconds,
        success_message="/metrics endpoint exposes Prometheus text",
        content_predicate=("sketchlog_", "/metrics is reachable but does not appear to include SketchLog metrics"),
    )
    if metrics_body is not None and "# HELP" in metrics_body and "# TYPE" in metrics_body:
        checks.append(DoctorCheck("observability", "prometheus-format", CheckStatus.PASS, "Prometheus HELP/TYPE metadata found"))
    elif metrics_body is not None:
        checks.append(DoctorCheck("observability", "prometheus-format", CheckStatus.WARN, "Prometheus metadata was not found in /metrics output"))

    if options.expect_storage:
        ready_failures = [check for check in checks if check.name == "ready" and check.status is CheckStatus.FAIL]
        if ready_failures:
            checks.append(DoctorCheck("storage", "storage-readiness", CheckStatus.FAIL, "Storage readiness could not be confirmed because /ready failed"))
        else:
            checks.append(DoctorCheck("storage", "storage-readiness", CheckStatus.PASS, "Storage expected and /ready passed"))
    else:
        checks.append(DoctorCheck("storage", "storage-readiness", CheckStatus.WARN, "Durable storage was not explicitly verified; pass --expect-storage for production DB checks"))

    if options.namespace_quota_mb is None:
        checks.append(DoctorCheck("multi-tenancy", "namespace-quota", CheckStatus.WARN, "Namespace quota was not provided; configure quotas for multi-tenant deployments"))
    elif options.namespace_quota_mb <= 0:
        checks.append(DoctorCheck("multi-tenancy", "namespace-quota", CheckStatus.FAIL, "Namespace quota must be positive"))
    else:
        checks.append(DoctorCheck("multi-tenancy", "namespace-quota", CheckStatus.PASS, f"Namespace quota configured: {options.namespace_quota_mb:g} MB"))

    if options.mesh_enabled:
        if options.cluster_token:
            checks.append(DoctorCheck("mesh", "cluster-secret", CheckStatus.PASS, "Cluster token supplied for mesh checks"))
        else:
            checks.append(DoctorCheck("mesh", "cluster-secret", CheckStatus.FAIL, "Mesh mode enabled but no cluster token was supplied"))
    else:
        checks.append(DoctorCheck("mesh", "cluster-secret", CheckStatus.WARN, "Mesh checks skipped; pass --mesh-enabled when auditing a mesh deployment"))

    _check_optional_configs(checks, options)
    return _build_report(safe_endpoint, checks, options.strict)


def _check_optional_configs(checks: List[DoctorCheck], options: DoctorOptions) -> None:
    _file_check(
        checks,
        category="integrations",
        name="otel-config",
        path=options.otel_config,
        missing="OpenTelemetry Collector config not provided; skip if Collector is not used",
        present="OpenTelemetry Collector config file exists",
    )
    if options.otel_config and options.otel_config.exists() and options.otel_config.is_file():
        if _contains_any(options.otel_config, ["sketchlog", "exporters:"]):
            checks.append(DoctorCheck("integrations", "otel-config-content", CheckStatus.PASS, "Collector config mentions SketchLog/exporters"))
        else:
            checks.append(DoctorCheck("integrations", "otel-config-content", CheckStatus.WARN, "Collector config does not mention SketchLog/exporters"))

    _file_check(
        checks,
        category="observability",
        name="prometheus-config",
        path=options.prometheus_config,
        missing="Prometheus scrape config not provided; skip if Prometheus is not used",
        present="Prometheus scrape config file exists",
    )
    if options.prometheus_config and options.prometheus_config.exists() and options.prometheus_config.is_file():
        if _contains_any(options.prometheus_config, ["/metrics", "metrics_path", "sketchlog"]):
            checks.append(DoctorCheck("observability", "prometheus-config-content", CheckStatus.PASS, "Prometheus config appears to reference SketchLog metrics"))
        else:
            checks.append(DoctorCheck("observability", "prometheus-config-content", CheckStatus.WARN, "Prometheus config does not appear to reference SketchLog metrics"))

    _file_check(
        checks,
        category="observability",
        name="grafana-dashboard",
        path=options.grafana_dashboard,
        missing="Grafana dashboard/plugin config not provided; skip if Grafana is not used",
        present="Grafana dashboard/plugin file exists",
    )
    _file_check(
        checks,
        category="storage",
        name="retention-policy",
        path=options.retention_policy,
        missing="Retention policy not provided; define retention before long-running production use",
        present="Retention policy file exists",
    )
    _file_check(
        checks,
        category="storage",
        name="backup-policy",
        path=options.backup_policy,
        missing="Backup policy not provided; define backup/restore before durable production use",
        present="Backup policy file exists",
    )

    if options.repo_root is not None:
        _repo_docs_check(checks, options.repo_root)


def _repo_docs_check(checks: List[DoctorCheck], repo_root: Path) -> None:
    expected = [
        repo_root / "docs" / "grafana.md",
        repo_root / "docs" / "grafana-datasource-plugin.md",
        repo_root / "docs" / "otel-collector.md",
        repo_root / "dashboards" / "sketchlog-overview.json",
    ]
    missing = [str(path) for path in expected if not path.exists()]
    if missing:
        checks.append(DoctorCheck("documentation", "repo-artifacts", CheckStatus.WARN, "Some local docs/dashboard artifacts are missing", "; ".join(missing)))
    else:
        checks.append(DoctorCheck("documentation", "repo-artifacts", CheckStatus.PASS, "Grafana and OpenTelemetry documentation artifacts are present"))


def _build_report(endpoint: str, checks: List[DoctorCheck], strict: bool) -> DoctorReport:
    pass_count = sum(1 for check in checks if check.status is CheckStatus.PASS)
    warn_count = sum(1 for check in checks if check.status is CheckStatus.WARN)
    fail_count = sum(1 for check in checks if check.status is CheckStatus.FAIL)
    if fail_count:
        status = CheckStatus.FAIL
    elif strict and warn_count:
        status = CheckStatus.FAIL
    elif warn_count:
        status = CheckStatus.WARN
    else:
        status = CheckStatus.PASS
    return DoctorReport(
        endpoint=endpoint,
        summary=DoctorSummary(status, pass_count, warn_count, fail_count, strict),
        checks=checks,
    )


def format_text(report: DoctorReport) -> str:
    lines = [
        "SketchLog production readiness check",
        f"Endpoint: {report.endpoint}",
        "",
    ]
    width = max((len(check.category) + len(check.name) + 3 for check in report.checks), default=20)
    for check in report.checks:
        label = f"{check.category}/{check.name}"
        lines.append(f"{check.status.value.upper():<5} {label:<{width}} {check.message}")
        if check.detail:
            lines.append(f"      detail: {redact_text(check.detail)}")
    lines.extend(
        [
            "",
            f"Result: {report.summary.status.value} "
            f"({report.summary.pass_count} pass, {report.summary.warn_count} warn, {report.summary.fail_count} fail)",
        ]
    )
    if report.summary.strict and report.summary.status is CheckStatus.FAIL and report.summary.fail_count == 0:
        lines.append("Strict mode treats warnings as failures.")
    return "\n".join(lines)


def exit_code(report: DoctorReport) -> int:
    return 0 if report.summary.status is not CheckStatus.FAIL else 1


def _parse_args(argv: Optional[Sequence[str]]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit a SketchLog deployment for production readiness")
    parser.add_argument("--endpoint", required=True, help="SketchLog base URL, for example http://localhost:8000")
    parser.add_argument("--auth-token", help="Optional X-SketchLog-Auth-Token value")
    parser.add_argument("--cluster-token", help="Optional X-SketchLog-Cluster-Token value for mesh deployments")
    parser.add_argument("--timeout", type=float, default=3.0, help="HTTP timeout in seconds")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument("--strict", action="store_true", help="Return non-zero when warnings are present")
    parser.add_argument("--expect-auth", action="store_true", help="Fail if no auth token is supplied")
    parser.add_argument("--expect-tls", action="store_true", help="Fail if endpoint is not HTTPS")
    parser.add_argument("--expect-storage", action="store_true", help="Treat /ready as a storage readiness gate")
    parser.add_argument("--mesh-enabled", action="store_true", help="Require mesh cluster token checks")
    parser.add_argument("--namespace-quota-mb", type=float, help="Configured namespace quota in MB")
    parser.add_argument("--repo-root", type=Path, help="Optional local SketchLog repo root for docs/dashboard artifact checks")
    parser.add_argument("--otel-config", type=Path, help="Optional OpenTelemetry Collector config path")
    parser.add_argument("--prometheus-config", type=Path, help="Optional Prometheus scrape config path")
    parser.add_argument("--grafana-dashboard", type=Path, help="Optional Grafana dashboard/plugin config path")
    parser.add_argument("--retention-policy", type=Path, help="Optional retention policy path")
    parser.add_argument("--backup-policy", type=Path, help="Optional backup policy path")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parse_args(argv)
    report = run_doctor(
        DoctorOptions(
            endpoint=args.endpoint,
            auth_token=args.auth_token,
            cluster_token=args.cluster_token,
            timeout_seconds=args.timeout,
            strict=args.strict,
            expect_auth=args.expect_auth,
            expect_tls=args.expect_tls,
            expect_storage=args.expect_storage,
            mesh_enabled=args.mesh_enabled,
            namespace_quota_mb=args.namespace_quota_mb,
            repo_root=args.repo_root,
            otel_config=args.otel_config,
            prometheus_config=args.prometheus_config,
            grafana_dashboard=args.grafana_dashboard,
            retention_policy=args.retention_policy,
            backup_policy=args.backup_policy,
        )
    )
    if args.format == "json":
        print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    else:
        print(format_text(report))
    return exit_code(report)


if __name__ == "__main__":
    raise SystemExit(main())
