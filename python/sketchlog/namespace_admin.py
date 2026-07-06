"""
sketchlog.namespace_admin
~~~~~~~~~~~~~~~~~~~~~~~~~
Namespace admin API and CLI for multi-tenant SketchLog operations.

Provides:
  NamespaceInfo / NamespaceAdminConfig  dataclasses  (secret-safe)
  _fetch_namespaces()   stdlib urlopen
  _parse_namespace_response()
  render_text() / render_json()
  main() CLI:  sketchlog-namespace-admin
      --url  --token  --timeout  --format text|json  --demo  --top N
      exit 0=OK  1=WARN/error  2=bad config

stdlib only — no third-party dependencies.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
_VERSION = "1.0.0"
_TOKEN_ENV_VAR = "SKETCHLOG_ADMIN_TOKEN"
_QUOTA_WARN_FRACTION = 0.80
_STALE_ACTIVITY_S = 3600.0
_DEMO_SEED = 0xADM1N  # intentional; replaced below
_DEMO_SEED = 0x4D1_AD  # deterministic demo data seed


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _redact_url(url: str) -> str:
    """Remove userinfo from a URL for safe logging."""
    try:
        p = urllib.parse.urlparse(url)
        if p.username or p.password:
            host = p.hostname or ""
            if p.port:
                host = f"{host}:{p.port}"
            safe = p._replace(netloc=host)
            return urllib.parse.urlunparse(safe)
    except Exception:
        pass
    return url


def _fmt_bytes(n: int) -> str:
    units = ["B", "KiB", "MiB", "GiB", "TiB", "PiB", "EiB"]
    v = float(n)
    for u in units[:-1]:
        if abs(v) < 1024.0:
            return f"{v:.2f} {u}"
        v /= 1024.0
    return f"{v:.2f} {units[-1]}"


def _parse_bool(val: Any) -> bool:
    """Parse a bool field from a raw payload without mis-reading strings."""
    if isinstance(val, bool):
        return val
    if isinstance(val, str):
        return val.strip().lower() in ("true", "1", "yes")
    return bool(val)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TokenInfo:
    """Secret-safe token descriptor — raw secret never exposed."""
    token_id: str
    label: str
    scopes: List[str]
    created_at: Optional[float]
    expires_at: Optional[float]
    last_used_at: Optional[float]
    active: bool
    prefix: str = ""

    def __post_init__(self) -> None:
        errors: List[str] = []
        if not isinstance(self.token_id, str) or not self.token_id.strip():
            errors.append("token_id must be a non-empty string")
        if not isinstance(self.scopes, list):
            errors.append("scopes must be a list")
        if isinstance(self.active, bool):
            pass
        else:
            errors.append("active must be a bool")
        if errors:
            raise ValueError("TokenInfo errors: " + "; ".join(errors))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "token_id": self.token_id,
            "label": self.label,
            "scopes": list(self.scopes),
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "last_used_at": self.last_used_at,
            "active": self.active,
            "prefix": self.prefix,
        }


@dataclass(frozen=True)
class StreamSummary:
    """Lightweight per-stream stats within a namespace."""
    name: str
    sketch_count: int
    memory_bytes: int
    last_write_at: Optional[float]
    event_rate_hz: float

    def __post_init__(self) -> None:
        errors: List[str] = []
        if not isinstance(self.name, str) or not self.name.strip():
            errors.append("name must be a non-empty string")
        for fname in ("sketch_count", "memory_bytes"):
            v = getattr(self, fname)
            if isinstance(v, bool) or not isinstance(v, int) or v < 0:
                errors.append(f"{fname} must be a non-negative int")
        if isinstance(self.event_rate_hz, bool) or not isinstance(
            self.event_rate_hz, (int, float)
        ):
            errors.append("event_rate_hz must be a float")
        elif not math.isfinite(self.event_rate_hz) or self.event_rate_hz < 0.0:
            errors.append("event_rate_hz must be finite and >= 0")
        if errors:
            raise ValueError("StreamSummary errors: " + "; ".join(errors))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "sketch_count": self.sketch_count,
            "memory_bytes": self.memory_bytes,
            "last_write_at": self.last_write_at,
            "event_rate_hz": round(self.event_rate_hz, 4),
        }


@dataclass(frozen=True)
class AlertSummary:
    """Per-namespace alert counts — no payloads."""
    active_critical: int
    active_warning: int
    active_info: int
    silenced: int

    def __post_init__(self) -> None:
        for fname in ("active_critical", "active_warning", "active_info", "silenced"):
            v = getattr(self, fname)
            if isinstance(v, bool) or not isinstance(v, int) or v < 0:
                raise ValueError(f"{fname} must be a non-negative int")

    @property
    def total_active(self) -> int:
        return self.active_critical + self.active_warning + self.active_info

    def to_dict(self) -> Dict[str, Any]:
        return {
            "active_critical": self.active_critical,
            "active_warning": self.active_warning,
            "active_info": self.active_info,
            "silenced": self.silenced,
            "total_active": self.total_active,
        }


@dataclass(frozen=True)
class NamespaceInfo:
    """
    Full admin view of a single namespace.

    Fields
    ------
    name:              Namespace identifier.
    display_name:      Human-readable label (may equal name).
    stream_count:      Total number of streams.
    sketch_count:      Total sketch objects across all streams.
    memory_bytes:      Estimated memory usage in bytes.
    quota_bytes:       Configured memory quota (0 = unlimited).
    last_activity_at:  UNIX timestamp of most recent ingest (None = never).
    top_streams:       Up to N highest-traffic streams.
    tokens:            Secret-safe token descriptors.
    alerts:            Alert count summary.
    health:            "healthy" | "warn" | "degraded" | "unknown".
    tags:              Operator-defined key-value tags.
    """
    name: str
    display_name: str
    stream_count: int
    sketch_count: int
    memory_bytes: int
    quota_bytes: int
    last_activity_at: Optional[float]
    top_streams: List[StreamSummary]
    tokens: List[TokenInfo]
    alerts: AlertSummary
    health: str
    tags: Dict[str, str]

    _VALID_HEALTH = frozenset({"healthy", "warn", "degraded", "unknown"})

    def __post_init__(self) -> None:
        errors: List[str] = []
        if not isinstance(self.name, str) or not self.name.strip():
            errors.append("name must be a non-empty string")
        for fname in ("stream_count", "sketch_count", "memory_bytes", "quota_bytes"):
            v = getattr(self, fname)
            if isinstance(v, bool) or not isinstance(v, int) or v < 0:
                errors.append(f"{fname} must be a non-negative int")
        if self.health not in self._VALID_HEALTH:
            errors.append(f"health must be one of {sorted(self._VALID_HEALTH)}")
        if not isinstance(self.tags, dict):
            errors.append("tags must be a dict")
        if not isinstance(self.top_streams, list):
            errors.append("top_streams must be a list")
        if not isinstance(self.tokens, list):
            errors.append("tokens must be a list")
        if errors:
            raise ValueError("NamespaceInfo errors: " + "; ".join(errors))

    @property
    def quota_used_fraction(self) -> Optional[float]:
        if self.quota_bytes == 0:
            return None
        return self.memory_bytes / self.quota_bytes

    @property
    def quota_warn(self) -> bool:
        f = self.quota_used_fraction
        return f is not None and f >= _QUOTA_WARN_FRACTION

    @property
    def stale(self) -> bool:
        if self.last_activity_at is None:
            return False
        return (time.time() - self.last_activity_at) > _STALE_ACTIVITY_S

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "display_name": self.display_name,
            "stream_count": self.stream_count,
            "sketch_count": self.sketch_count,
            "memory_bytes": self.memory_bytes,
            "quota_bytes": self.quota_bytes,
            "quota_used_fraction": (
                round(self.quota_used_fraction, 6)
                if self.quota_used_fraction is not None else None
            ),
            "quota_warn": self.quota_warn,
            "last_activity_at": self.last_activity_at,
            "stale": self.stale,
            "top_streams": [s.to_dict() for s in self.top_streams],
            "tokens": [t.to_dict() for t in self.tokens],
            "alerts": self.alerts.to_dict(),
            "health": self.health,
            "tags": dict(self.tags),
        }


@dataclass(frozen=True)
class NamespaceAdminConfig:
    """
    Connection configuration for the Namespace Admin CLI.

    token is resolved in priority order:
      1. SKETCHLOG_ADMIN_TOKEN environment variable
      2. token field (inline — avoid in production scripts)

    url must use https:// (http:// rejected to protect credentials).
    """
    url: str
    token: str = ""
    timeout_s: int = 10
    top_streams: int = 5

    def __post_init__(self) -> None:
        errors: List[str] = []
        if not isinstance(self.url, str) or not self.url.startswith("https://"):
            errors.append("url must start with https://")
        if isinstance(self.timeout_s, bool) or not isinstance(self.timeout_s, int) or self.timeout_s < 1:
            errors.append("timeout_s must be a positive int")
        if isinstance(self.top_streams, bool) or not isinstance(self.top_streams, int) or self.top_streams < 1:
            errors.append("top_streams must be a positive int")
        if errors:
            raise ValueError("NamespaceAdminConfig errors: " + "; ".join(errors))

    def resolved_token(self) -> str:
        return os.environ.get(_TOKEN_ENV_VAR, "") or self.token


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def _parse_stream_summary(raw: Dict[str, Any]) -> Optional[StreamSummary]:
    try:
        return StreamSummary(
            name=str(raw.get("name", "")),
            sketch_count=int(raw.get("sketch_count", 0)),
            memory_bytes=int(raw.get("memory_bytes", 0)),
            last_write_at=float(raw["last_write_at"]) if raw.get("last_write_at") is not None else None,
            event_rate_hz=float(raw.get("event_rate_hz", 0.0)),
        )
    except (TypeError, ValueError, KeyError):
        return None


def _parse_token_info(raw: Dict[str, Any]) -> Optional[TokenInfo]:
    try:
        return TokenInfo(
            token_id=str(raw.get("token_id", "")),
            label=str(raw.get("label", "")),
            scopes=list(raw.get("scopes", [])),
            created_at=float(raw["created_at"]) if raw.get("created_at") is not None else None,
            expires_at=float(raw["expires_at"]) if raw.get("expires_at") is not None else None,
            last_used_at=float(raw["last_used_at"]) if raw.get("last_used_at") is not None else None,
            active=_parse_bool(raw.get("active", True)),
            prefix=str(raw.get("prefix", "")),
        )
    except (TypeError, ValueError, KeyError):
        return None


def _parse_alert_summary(raw: Any) -> AlertSummary:
    if not isinstance(raw, dict):
        raw = {}
    return AlertSummary(
        active_critical=int(raw.get("active_critical", 0)),
        active_warning=int(raw.get("active_warning", 0)),
        active_info=int(raw.get("active_info", 0)),
        silenced=int(raw.get("silenced", 0)),
    )


def _parse_namespace(raw: Any) -> Optional[NamespaceInfo]:
    if not isinstance(raw, dict):
        return None
    try:
        streams_raw = raw.get("top_streams", [])
        if not isinstance(streams_raw, list):
            streams_raw = []
        top_streams = [s for s in (_parse_stream_summary(x) for x in streams_raw) if s]

        tokens_raw = raw.get("tokens", [])
        if not isinstance(tokens_raw, list):
            tokens_raw = []
        tokens = [t for t in (_parse_token_info(x) for x in tokens_raw) if t]

        return NamespaceInfo(
            name=str(raw.get("name", "unknown")),
            display_name=str(raw.get("display_name", raw.get("name", "unknown"))),
            stream_count=int(raw.get("stream_count", 0)),
            sketch_count=int(raw.get("sketch_count", 0)),
            memory_bytes=int(raw.get("memory_bytes", 0)),
            quota_bytes=int(raw.get("quota_bytes", 0)),
            last_activity_at=(
                float(raw["last_activity_at"])
                if raw.get("last_activity_at") is not None else None
            ),
            top_streams=top_streams,
            tokens=tokens,
            alerts=_parse_alert_summary(raw.get("alerts")),
            health=str(raw.get("health", "unknown")),
            tags=dict(raw.get("tags", {})) if isinstance(raw.get("tags"), dict) else {},
        )
    except (TypeError, ValueError, KeyError):
        return None


def _parse_namespace_response(raw: Dict[str, Any]) -> List[NamespaceInfo]:
    entries = raw.get("namespaces", [])
    if not isinstance(entries, list):
        entries = []
    return [ns for ns in (_parse_namespace(e) for e in entries) if ns]


# ---------------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------------

def _fetch_namespaces(config: NamespaceAdminConfig) -> List[NamespaceInfo]:
    url = config.url.rstrip("/") + "/api/v1/admin/namespaces"
    headers: Dict[str, str] = {"Accept": "application/json"}
    tok = config.resolved_token()
    if tok:
        headers["Authorization"] = f"Bearer {tok}"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=config.timeout_s) as resp:  # nosec B310
            try:
                raw: Dict[str, Any] = json.loads(resp.read().decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise RuntimeError(f"Invalid JSON from {_redact_url(url)}: {exc}") from exc
    except urllib.error.HTTPError as exc:
        body = exc.read(300).decode("utf-8", errors="replace")
        raise RuntimeError(
            f"HTTP {exc.code} from {_redact_url(url)}: {body}"
        ) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(
            f"Connection failed to {_redact_url(url)}: {exc.reason}"
        ) from exc
    return _parse_namespace_response(raw)


# ---------------------------------------------------------------------------
# Demo data
# ---------------------------------------------------------------------------

def _build_demo_namespaces(top_n: int = 5) -> List[NamespaceInfo]:
    rng = random.Random(_DEMO_SEED)
    now = time.time()
    result: List[NamespaceInfo] = []
    teams = ["platform", "analytics", "frontend", "ml-pipeline", "security-ops"]
    for i, team in enumerate(teams):
        streams: List[StreamSummary] = []
        for j in range(top_n):
            streams.append(StreamSummary(
                name=f"stream-{j:02d}",
                sketch_count=rng.randint(1, 8),
                memory_bytes=rng.randint(4096, 512 * 1024),
                last_write_at=now - rng.uniform(0, 1800),
                event_rate_hz=round(rng.uniform(0.1, 250.0), 2),
            ))
        tokens = [TokenInfo(
            token_id=f"tok-{team}-{k:02d}",
            label=["ingest", "read-only", "admin"][k % 3],
            scopes=[["ingest"], ["query"], ["ingest", "query", "admin"]][k % 3],
            created_at=now - rng.uniform(0, 86400 * 30),
            expires_at=now + rng.uniform(0, 86400 * 90) if k % 2 == 0 else None,
            last_used_at=now - rng.uniform(0, 7200),
            active=True,
            prefix=f"sk_{team[:4]}{k:02d}",
        ) for k in range(2)]
        mem = rng.randint(1 * 1024 * 1024, 400 * 1024 * 1024)
        quota = 512 * 1024 * 1024
        result.append(NamespaceInfo(
            name=team,
            display_name=team.replace("-", " ").title(),
            stream_count=rng.randint(5, 80),
            sketch_count=rng.randint(20, 600),
            memory_bytes=mem,
            quota_bytes=quota,
            last_activity_at=now - rng.uniform(0, 7200) if i != 2 else now - 7200.0,
            top_streams=streams,
            tokens=tokens,
            alerts=AlertSummary(
                active_critical=rng.randint(0, 2),
                active_warning=rng.randint(0, 4),
                active_info=rng.randint(0, 6),
                silenced=rng.randint(0, 3),
            ),
            health=["healthy", "healthy", "warn", "healthy", "degraded"][i],
            tags={"team": team, "env": "demo"},
        ))
    return result


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

_HEALTH_ICON = {"healthy": "v", "warn": "?", "degraded": "x", "unknown": "-"}


def render_text(namespaces: List[NamespaceInfo], top_n: int = 5) -> str:
    lines: List[str] = []
    w = 90
    lines.append("+" + "-" * (w - 2) + "+")
    lines.append("|  SketchLog -- Namespace Admin Center" + " " * (w - 40) + "|")
    lines.append("+" + "-" * (w - 2) + "+")
    lines.append(f"  Namespaces : {len(namespaces)}")
    lines.append("")

    for ns in namespaces:
        icon = _HEALTH_ICON.get(ns.health, "-")
        quota_str = (
            f"{_fmt_bytes(ns.memory_bytes)} / {_fmt_bytes(ns.quota_bytes)}"
            f" ({(ns.quota_used_fraction or 0.0) * 100:.1f} %)"
            + (" [WARN]" if ns.quota_warn else "")
            if ns.quota_bytes > 0
            else f"{_fmt_bytes(ns.memory_bytes)} (no quota)"
        )
        stale_flag = " [STALE]" if ns.stale else ""
        lines.append(
            f"  [{icon}] {ns.display_name} ({ns.name})"
            f"  health={ns.health}{stale_flag}"
        )
        lines.append(
            f"      streams={ns.stream_count}  sketches={ns.sketch_count}"
            f"  memory={quota_str}"
        )
        if ns.alerts.total_active > 0 or ns.alerts.silenced > 0:
            lines.append(
                f"      alerts: crit={ns.alerts.active_critical}"
                f"  warn={ns.alerts.active_warning}"
                f"  info={ns.alerts.active_info}"
                f"  silenced={ns.alerts.silenced}"
            )
        if ns.tokens:
            tok_line = "  ".join(
                f"{t.prefix or t.token_id[:8]}... ({t.label})"
                + ("" if t.active else " [inactive]")
                for t in ns.tokens[:3]
            )
            lines.append(f"      tokens : {tok_line}")
        if ns.top_streams:
            lines.append(
                f"      top streams (by event rate, top {min(top_n, len(ns.top_streams))}):"
            )
            for s in ns.top_streams[:top_n]:
                lines.append(
                    f"        {s.name:<28} {s.event_rate_hz:>8.2f} ev/s"
                    f"  {_fmt_bytes(s.memory_bytes):>12}  sketches={s.sketch_count}"
                )
        if ns.tags:
            tag_str = "  ".join(f"{k}={v}" for k, v in ns.tags.items())
            lines.append(f"      tags : {tag_str}")
        lines.append("")

    return "\n".join(lines)


def render_json(namespaces: List[NamespaceInfo]) -> str:
    return json.dumps(
        {"namespaces": [ns.to_dict() for ns in namespaces]},
        indent=2,
        default=str,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> None:
    parser = argparse.ArgumentParser(
        prog="sketchlog-namespace-admin",
        description="SketchLog Namespace Admin — list tenants, quotas, streams, and tokens.",
    )
    parser.add_argument("--url", help="Admin API base URL (https://...)")
    parser.add_argument("--token", default="", help="Bearer token (prefer env var)")
    parser.add_argument("--timeout", type=int, default=10, help="Request timeout in seconds")
    parser.add_argument("--format", choices=["text", "json"], default="text")
    parser.add_argument("--top", type=int, default=5, help="Top streams to show per namespace")
    parser.add_argument("--demo", action="store_true", help="Run with built-in demo data")
    parser.add_argument("--version", action="version", version=f"%(prog)s {_VERSION}")
    args = parser.parse_args(argv)

    if args.demo:
        namespaces = _build_demo_namespaces(top_n=args.top)
    else:
        if not args.url:
            print("ERROR: --url is required (or use --demo)", file=sys.stderr)
            sys.exit(2)
        try:
            config = NamespaceAdminConfig(
                url=args.url,
                token=args.token,
                timeout_s=args.timeout,
                top_streams=args.top,
            )
        except ValueError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            sys.exit(2)
        try:
            namespaces = _fetch_namespaces(config)
        except RuntimeError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            sys.exit(1)

    if args.format == "json":
        print(render_json(namespaces))
    else:
        print(render_text(namespaces, top_n=args.top))

    warn = any(ns.health in ("warn", "degraded") or ns.quota_warn for ns in namespaces)
    sys.exit(1 if warn else 0)


if __name__ == "__main__":
    main()
