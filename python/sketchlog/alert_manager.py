"""
sketchlog.alert_manager
=======================
Production-grade Alert Management Center.

Capabilities
------------
* Alert / Silence / Route / ChannelConfig frozen dataclasses with full validation.
* AlertStore     – thread-safe in-memory store with optional JSON persistence.
* SilenceManager – create / expire / match silences and maintenance windows.
* AlertRouter    – route alerts to channels by namespace, stream, severity, tenant.
* Delivery adapters: Slack, Discord, PagerDuty, Opsgenie, webhook (stdlib only).
* DeliveryEngine – retry with exponential back-off, delivery status tracking.
* AlertManager   – facade: ingest → silence-check → route → deliver → history.
* main()         – sketchlog-alert-manager CLI entry point.
"""
from __future__ import annotations

import argparse
import dataclasses
import enum
import json
import math
import os
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from typing import Any, Dict, List, Optional, Sequence, Tuple

_SEVERITIES: Tuple[str, ...] = ("critical", "warning", "info")
_STATUSES: Tuple[str, ...] = ("firing", "resolved", "silenced")
_ADAPTERS: Tuple[str, ...] = ("slack", "discord", "pagerduty", "opsgenie", "webhook")
_MAX_RETRY = 3
_RETRY_BASE_S = 1.0
_DELIVERY_TIMEOUT_S = 10


class Severity(str, enum.Enum):
    CRITICAL = "critical"
    WARNING  = "warning"
    INFO     = "info"


class AlertStatus(str, enum.Enum):
    FIRING   = "firing"
    RESOLVED = "resolved"
    SILENCED = "silenced"


class DeliveryStatus(str, enum.Enum):
    PENDING = "pending"
    SENT    = "sent"
    FAILED  = "failed"
    SKIPPED = "skipped"


def _now() -> float:
    return time.time()


def _new_id() -> str:
    return str(uuid.uuid4())


# ── Alert ─────────────────────────────────────────────────────────────────────

@dataclasses.dataclass(frozen=True)
class Alert:
    """A single alert fired by SketchLog or injected externally."""

    name: str
    namespace: str
    stream: str
    severity: str
    status: str = "firing"
    labels: Dict[str, str] = dataclasses.field(default_factory=dict)
    annotations: Dict[str, str] = dataclasses.field(default_factory=dict)
    fired_at: float = dataclasses.field(default_factory=_now)
    resolved_at: Optional[float] = None
    tenant: str = ""
    alert_id: str = dataclasses.field(default_factory=_new_id)

    def __post_init__(self) -> None:
        errors: List[str] = []
        if not isinstance(self.name, str) or not self.name.strip():
            errors.append("name must be a non-empty string")
        if not isinstance(self.namespace, str) or not self.namespace.strip():
            errors.append("namespace must be a non-empty string")
        if not isinstance(self.stream, str):
            errors.append("stream must be a string")
        if self.severity not in _SEVERITIES:
            errors.append(f"severity must be one of {_SEVERITIES}; got {self.severity!r}")
        if self.status not in _STATUSES:
            errors.append(f"status must be one of {_STATUSES}; got {self.status!r}")
        if not isinstance(self.labels, dict):
            errors.append("labels must be a dict")
        if not isinstance(self.annotations, dict):
            errors.append("annotations must be a dict")
        if not isinstance(self.fired_at, (int, float)) or not math.isfinite(float(self.fired_at)):
            errors.append("fired_at must be a finite number")
        if self.resolved_at is not None:
            if not isinstance(self.resolved_at, (int, float)) or not math.isfinite(float(self.resolved_at)):
                errors.append("resolved_at must be a finite number or None")
        if errors:
            raise ValueError("Alert validation errors: " + "; ".join(errors))

    def resolve(self, resolved_at: Optional[float] = None) -> "Alert":
        return dataclasses.replace(
            self,
            status=AlertStatus.RESOLVED.value,
            resolved_at=resolved_at if resolved_at is not None else _now(),
        )

    def silence(self) -> "Alert":
        return dataclasses.replace(self, status=AlertStatus.SILENCED.value)

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)


# ── Silence ───────────────────────────────────────────────────────────────────

@dataclasses.dataclass(frozen=True)
class Silence:
    """Suppresses matching alerts for a time window."""

    starts_at: float
    ends_at: float
    match_namespace: str = ""
    match_stream: str = ""
    match_severity: str = ""
    match_tenant: str = ""
    match_labels: Dict[str, str] = dataclasses.field(default_factory=dict)
    comment: str = ""
    created_by: str = ""
    silence_id: str = dataclasses.field(default_factory=_new_id)

    def __post_init__(self) -> None:
        errors: List[str] = []
        for fn in ("starts_at", "ends_at"):
            v = getattr(self, fn)
            if not isinstance(v, (int, float)) or not math.isfinite(float(v)):
                errors.append(f"{fn} must be a finite number")
        if (
            math.isfinite(float(self.starts_at))
            and math.isfinite(float(self.ends_at))
            and float(self.ends_at) <= float(self.starts_at)
        ):
            errors.append("ends_at must be strictly greater than starts_at")
        if self.match_severity and self.match_severity not in _SEVERITIES:
            errors.append(f"match_severity must be one of {_SEVERITIES}; got {self.match_severity!r}")
        if not isinstance(self.match_labels, dict):
            errors.append("match_labels must be a dict")
        if errors:
            raise ValueError("Silence validation errors: " + "; ".join(errors))

    def is_active(self, at: Optional[float] = None) -> bool:
        t = at if at is not None else _now()
        return float(self.starts_at) <= t < float(self.ends_at)

    def matches(self, alert: Alert, at: Optional[float] = None) -> bool:
        if not self.is_active(at):
            return False
        if self.match_namespace and self.match_namespace != alert.namespace:
            return False
        if self.match_stream and self.match_stream != alert.stream:
            return False
        if self.match_severity and self.match_severity != alert.severity:
            return False
        if self.match_tenant and self.match_tenant != alert.tenant:
            return False
        for k, v in self.match_labels.items():
            if alert.labels.get(k) != v:
                return False
        return True

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)


# ── Route ─────────────────────────────────────────────────────────────────────

@dataclasses.dataclass(frozen=True)
class Route:
    """Maps matching alerts to one or more delivery channels."""

    channels: List[str]
    match_namespace: str = ""
    match_stream: str = ""
    match_severity: str = ""
    match_tenant: str = ""
    match_labels: Dict[str, str] = dataclasses.field(default_factory=dict)
    continue_matching: bool = False

    def __post_init__(self) -> None:
        errors: List[str] = []
        if not isinstance(self.channels, list) or not self.channels:
            errors.append("channels must be a non-empty list")
        if self.match_severity and self.match_severity not in _SEVERITIES:
            errors.append(f"match_severity must be one of {_SEVERITIES}; got {self.match_severity!r}")
        if errors:
            raise ValueError("Route validation errors: " + "; ".join(errors))

    def matches(self, alert: Alert) -> bool:
        if self.match_namespace and self.match_namespace != alert.namespace:
            return False
        if self.match_stream and self.match_stream != alert.stream:
            return False
        if self.match_severity and self.match_severity != alert.severity:
            return False
        if self.match_tenant and self.match_tenant != alert.tenant:
            return False
        for k, v in self.match_labels.items():
            if alert.labels.get(k) != v:
                return False
        return True

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)


# ── ChannelConfig ──────────────────────────────────────────────────────────────

@dataclasses.dataclass(frozen=True)
class ChannelConfig:
    """Configuration for a single notification channel."""

    name: str
    adapter: str
    url: str
    token: str = ""
    token_env: str = ""
    timeout_s: int = _DELIVERY_TIMEOUT_S
    send_resolved: bool = True

    def __post_init__(self) -> None:
        errors: List[str] = []
        if not isinstance(self.name, str) or not self.name.strip():
            errors.append("name must be a non-empty string")
        if self.adapter not in _ADAPTERS:
            errors.append(f"adapter must be one of {_ADAPTERS}; got {self.adapter!r}")
        if not isinstance(self.url, str) or not self.url.startswith(("http://", "https://")):
            errors.append("url must start with http:// or https://")
        if not isinstance(self.timeout_s, int) or self.timeout_s < 1:
            errors.append("timeout_s must be a positive integer")
        if errors:
            raise ValueError("ChannelConfig validation errors: " + "; ".join(errors))

    def resolved_token(self) -> str:
        if self.token_env:
            val = os.environ.get(self.token_env, "")
            if val:
                return val
        return self.token


# ── DeliveryRecord ────────────────────────────────────────────────────────────

@dataclasses.dataclass
class DeliveryRecord:
    alert_id: str
    channel: str
    adapter: str
    status: str = DeliveryStatus.PENDING.value
    attempts: int = 0
    last_attempt_at: Optional[float] = None
    last_error: str = ""
    sent_at: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)


# ── Adapters ──────────────────────────────────────────────────────────────────

def _http_post(url: str, payload: Dict[str, Any],
               headers: Dict[str, str], timeout: int) -> None:
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        url, data=body,
        headers={**headers, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec B310
            st = resp.status
    except urllib.error.HTTPError as exc:
        raw = exc.read(300).decode(errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {raw}") from exc
    if not (200 <= st < 300):
        raise RuntimeError(f"HTTP {st}: unexpected response")


def _build_slack_payload(alert: Alert) -> Dict[str, Any]:
    emoji = {"critical": ":red_circle:", "warning": ":large_yellow_circle:",
             "info": ":large_blue_circle:"}.get(alert.severity, ":white_circle:")
    text = (f"{emoji} *[{alert.status.upper()}]* *{alert.name}*\n"
            f"Namespace: `{alert.namespace}`  Stream: `{alert.stream or chr(8212)}`  "
            f"Severity: `{alert.severity}`")
    if alert.annotations.get("description"):
        text += f"\n>{alert.annotations['description']}"
    return {"text": text}


def _build_discord_payload(alert: Alert) -> Dict[str, Any]:
    color = {"critical": 0xE53935, "warning": 0xFB8C00,
             "info": 0x1E88E5}.get(alert.severity, 0x90A4AE)
    embed: Dict[str, Any] = {
        "title": f"[{alert.status.upper()}] {alert.name}",
        "color": color,
        "fields": [
            {"name": "Namespace", "value": alert.namespace, "inline": True},
            {"name": "Stream",    "value": alert.stream or "N/A", "inline": True},
            {"name": "Severity",  "value": alert.severity, "inline": True},
        ],
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(alert.fired_at)),
    }
    if alert.annotations.get("description"):
        embed["description"] = alert.annotations["description"]
    return {"embeds": [embed]}


def _build_pagerduty_payload(alert: Alert, routing_key: str) -> Dict[str, Any]:
    action = "resolve" if alert.status == AlertStatus.RESOLVED.value else "trigger"
    return {
        "routing_key": routing_key,
        "event_action": action,
        "dedup_key": alert.alert_id,
        "payload": {
            "summary": alert.name,
            "source": (f"{alert.namespace}/{alert.stream}"
                       if alert.stream else alert.namespace),
            "severity": alert.severity,
            "custom_details": {**alert.labels, **alert.annotations},
        },
    }


def _build_opsgenie_payload(
    alert: Alert, api_key: str
) -> Tuple[Dict[str, Any], Dict[str, str]]:
    headers = {"Authorization": f"GenieKey {api_key}"}
    if alert.status == AlertStatus.RESOLVED.value:
        payload: Dict[str, Any] = {"source": "sketchlog-alert-manager",
                                    "note": "Resolved by SketchLog"}
    else:
        payload = {
            "message": alert.name,
            "alias": alert.alert_id,
            "description": alert.annotations.get("description", ""),
            "source": "sketchlog-alert-manager",
            "priority": {"critical": "P1", "warning": "P3",
                         "info": "P5"}.get(alert.severity, "P3"),
            "tags": [f"{k}:{v}" for k, v in alert.labels.items()],
            "details": alert.annotations,
        }
    return payload, headers


def _build_webhook_payload(alert: Alert) -> Dict[str, Any]:
    return alert.to_dict()


def _deliver_to_channel(alert: Alert, cfg: ChannelConfig) -> None:
    token = cfg.resolved_token()
    headers: Dict[str, str] = {}
    if cfg.adapter == "slack":
        payload = _build_slack_payload(alert)
    elif cfg.adapter == "discord":
        payload = _build_discord_payload(alert)
    elif cfg.adapter == "pagerduty":
        payload = _build_pagerduty_payload(alert, token)
    elif cfg.adapter == "opsgenie":
        payload, extra = _build_opsgenie_payload(alert, token)
        headers.update(extra)
    else:
        payload = _build_webhook_payload(alert)
        if token:
            headers["Authorization"] = f"Bearer {token}"
    _http_post(cfg.url, payload, headers, cfg.timeout_s)


# ── DeliveryEngine ────────────────────────────────────────────────────────────

class DeliveryEngine:
    def __init__(self, channels: Optional[Dict[str, ChannelConfig]] = None) -> None:
        self._channels: Dict[str, ChannelConfig] = channels or {}
        self._records: Dict[str, DeliveryRecord] = {}
        self._lock = threading.Lock()

    def add_channel(self, cfg: ChannelConfig) -> None:
        with self._lock:
            self._channels[cfg.name] = cfg

    def deliver(self, alert: Alert, channel_names: List[str]) -> List[DeliveryRecord]:
        records: List[DeliveryRecord] = []
        for ch in channel_names:
            cfg = self._channels.get(ch)
            if cfg is None:
                rec = DeliveryRecord(
                    alert_id=alert.alert_id, channel=ch, adapter="unknown",
                    status=DeliveryStatus.FAILED.value,
                    last_error=f"channel {ch!r} not configured",
                )
                self._store(rec); records.append(rec); continue

            if alert.status == AlertStatus.RESOLVED.value and not cfg.send_resolved:
                rec = DeliveryRecord(
                    alert_id=alert.alert_id, channel=ch, adapter=cfg.adapter,
                    status=DeliveryStatus.SKIPPED.value,
                )
                self._store(rec); records.append(rec); continue

            rec = DeliveryRecord(alert_id=alert.alert_id, channel=ch,
                                 adapter=cfg.adapter)
            self._store(rec)
            for attempt in range(1, _MAX_RETRY + 1):
                rec.attempts = attempt
                rec.last_attempt_at = _now()
                try:
                    _deliver_to_channel(alert, cfg)
                    rec.status = DeliveryStatus.SENT.value
                    rec.sent_at = _now()
                    rec.last_error = ""
                    break
                except Exception as exc:
                    rec.last_error = str(exc)[:300]
                    if attempt < _MAX_RETRY:
                        time.sleep(_RETRY_BASE_S * (2 ** (attempt - 1)))
                    else:
                        rec.status = DeliveryStatus.FAILED.value
            self._store(rec); records.append(rec)
        return records

    def _store(self, rec: DeliveryRecord) -> None:
        with self._lock:
            self._records[f"{rec.alert_id}:{rec.channel}"] = rec

    def get_record(self, alert_id: str, channel: str) -> Optional[DeliveryRecord]:
        with self._lock:
            return self._records.get(f"{alert_id}:{channel}")

    def all_records(self) -> List[DeliveryRecord]:
        with self._lock:
            return list(self._records.values())


# ── AlertStore ────────────────────────────────────────────────────────────────

class AlertStore:
    def __init__(self, persist_path: Optional[str] = None) -> None:
        self._active: Dict[str, Alert] = {}
        self._history: List[Alert] = []
        self._lock = threading.Lock()
        self._persist_path = persist_path
        if persist_path and os.path.isfile(persist_path):
            self._load(persist_path)

    def upsert(self, alert: Alert) -> None:
        with self._lock:
            if alert.status in (AlertStatus.RESOLVED.value, AlertStatus.SILENCED.value):
                self._active.pop(alert.alert_id, None)
                self._history.append(alert)
            else:
                self._active[alert.alert_id] = alert
            if self._persist_path:
                self._save_locked()

    def get_active(self, alert_id: str) -> Optional[Alert]:
        with self._lock:
            return self._active.get(alert_id)

    def list_active(self) -> List[Alert]:
        with self._lock:
            return list(self._active.values())

    def list_history(self, namespace: str = "", stream: str = "",
                     severity: str = "", limit: int = 200) -> List[Alert]:
        with self._lock:
            results = [
                a for a in reversed(self._history)
                if (not namespace or a.namespace == namespace)
                and (not stream or a.stream == stream)
                and (not severity or a.severity == severity)
            ]
        return results[:limit]

    def _save_locked(self) -> None:
        assert self._persist_path is not None
        data = {"active":  [a.to_dict() for a in self._active.values()],
                "history": [a.to_dict() for a in self._history]}
        tmp = self._persist_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
        os.replace(tmp, self._persist_path)

    def _load(self, path: str) -> None:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        for d in data.get("active", []):
            a = Alert(**d); self._active[a.alert_id] = a
        for d in data.get("history", []):
            self._history.append(Alert(**d))


# ── SilenceManager ────────────────────────────────────────────────────────────

class SilenceManager:
    def __init__(self) -> None:
        self._silences: Dict[str, Silence] = {}
        self._lock = threading.Lock()

    def add(self, silence: Silence) -> None:
        with self._lock:
            self._silences[silence.silence_id] = silence

    def remove(self, silence_id: str) -> bool:
        with self._lock:
            return self._silences.pop(silence_id, None) is not None

    def is_silenced(self, alert: Alert, at: Optional[float] = None) -> bool:
        t = at if at is not None else _now()
        with self._lock:
            return any(s.matches(alert, t) for s in self._silences.values())

    def list_active(self, at: Optional[float] = None) -> List[Silence]:
        t = at if at is not None else _now()
        with self._lock:
            return [s for s in self._silences.values() if s.is_active(t)]

    def list_all(self) -> List[Silence]:
        with self._lock:
            return list(self._silences.values())


# ── AlertRouter ───────────────────────────────────────────────────────────────

class AlertRouter:
    def __init__(self, routes: Optional[List[Route]] = None,
                 default_channels: Optional[List[str]] = None) -> None:
        self._routes = list(routes or [])
        self._default_channels = list(default_channels or [])

    def add_route(self, route: Route) -> None:
        self._routes.append(route)

    def resolve_channels(self, alert: Alert) -> List[str]:
        matched: List[str] = []
        for route in self._routes:
            if route.matches(alert):
                matched.extend(ch for ch in route.channels if ch not in matched)
                if not route.continue_matching:
                    return matched
        return matched if matched else list(self._default_channels)


# ── AlertManager ──────────────────────────────────────────────────────────────

class AlertManager:
    """Top-level facade: ingest -> silence-check -> route -> deliver -> history."""

    def __init__(self, channels: Optional[List[ChannelConfig]] = None,
                 routes: Optional[List[Route]] = None,
                 default_channels: Optional[List[str]] = None,
                 persist_path: Optional[str] = None) -> None:
        ch_map = {c.name: c for c in (channels or [])}
        self.store    = AlertStore(persist_path=persist_path)
        self.silences = SilenceManager()
        self.router   = AlertRouter(routes=routes,
                                    default_channels=default_channels or [])
        self.delivery = DeliveryEngine(channels=ch_map)

    def ingest(self, alert: Alert) -> List[DeliveryRecord]:
        if self.silences.is_silenced(alert):
            self.store.upsert(alert.silence())
            return []
        self.store.upsert(alert)
        channels = self.router.resolve_channels(alert)
        return self.delivery.deliver(alert, channels) if channels else []

    def resolve(self, alert_id: str) -> Optional[List[DeliveryRecord]]:
        alert = self.store.get_active(alert_id)
        if alert is None:
            return None
        resolved = alert.resolve()
        self.store.upsert(resolved)
        channels = self.router.resolve_channels(resolved)
        return self.delivery.deliver(resolved, channels) if channels else []

    def add_silence(self, silence: Silence) -> None:
        self.silences.add(silence)

    def remove_silence(self, silence_id: str) -> bool:
        return self.silences.remove(silence_id)

    def add_channel(self, cfg: ChannelConfig) -> None:
        self.delivery.add_channel(cfg)

    def add_route(self, route: Route) -> None:
        self.router.add_route(route)

    def active_alerts(self) -> List[Alert]:
        return self.store.list_active()

    def alert_history(self, namespace: str = "", stream: str = "",
                      severity: str = "", limit: int = 200) -> List[Alert]:
        return self.store.list_history(namespace=namespace, stream=stream,
                                       severity=severity, limit=limit)

    def active_silences(self) -> List[Silence]:
        return self.silences.list_active()

    def delivery_status(self) -> List[DeliveryRecord]:
        return self.delivery.all_records()


# ── CLI ───────────────────────────────────────────────────────────────────────

def _print_status(am: AlertManager, fmt: str) -> None:
    data: Dict[str, Any] = {
        "active_alerts":    [a.to_dict() for a in am.active_alerts()],
        "active_silences":  [s.to_dict() for s in am.active_silences()],
        "delivery_records": [r.to_dict() for r in am.delivery_status()],
    }
    if fmt == "json":
        print(json.dumps(data, indent=2))
    else:
        print(f"Active alerts   : {len(data['active_alerts'])}")
        print(f"Active silences : {len(data['active_silences'])}")
        print(f"Delivery records: {len(data['delivery_records'])}")
        for a in data["active_alerts"]:
            print(f"  [{a['severity'].upper()}] {a['name']}  "
                  f"ns={a['namespace']}  status={a['status']}")


def main(argv: Optional[Sequence[str]] = None) -> None:  # noqa: C901
    p = argparse.ArgumentParser(
        prog="sketchlog-alert-manager",
        description="SketchLog Alert Management Center.",
    )
    p.add_argument("--config", metavar="FILE")
    p.add_argument("--format", choices=["text", "json"], default="text", dest="fmt")
    p.add_argument("--ingest", metavar="FILE")
    p.add_argument("--add-silence", metavar="FILE")
    p.add_argument("--resolve", metavar="ALERT_ID")
    args = p.parse_args(argv)

    cfg: Dict[str, Any] = {}
    if args.config:
        try:
            with open(args.config, encoding="utf-8") as fh:
                cfg = json.load(fh)
        except Exception as exc:
            print(f"ERROR: cannot load config: {exc}", file=sys.stderr)
            sys.exit(2)

    try:
        channels = [ChannelConfig(**c) for c in cfg.get("channels", [])]
        routes   = [Route(**r) for r in cfg.get("routes", [])]
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(2)

    am = AlertManager(channels=channels, routes=routes,
                      default_channels=cfg.get("default_channels", []),
                      persist_path=cfg.get("persist_path"))

    if args.ingest:
        try:
            with open(args.ingest, encoding="utf-8") as fh:
                raw = json.load(fh)
        except Exception as exc:
            print(f"ERROR: cannot load alerts: {exc}", file=sys.stderr)
            sys.exit(2)
        for ad in raw:
            try:
                am.ingest(Alert(**ad))
            except ValueError as exc:
                print(f"WARN: skipping: {exc}", file=sys.stderr)

    if args.add_silence:
        try:
            with open(args.add_silence, encoding="utf-8") as fh:
                sd = json.load(fh)
            am.add_silence(Silence(**sd))
        except Exception as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            sys.exit(2)

    if args.resolve:
        if am.resolve(args.resolve) is None:
            print(f"WARN: alert {args.resolve!r} not found in active alerts.",
                  file=sys.stderr)

    _print_status(am, args.fmt)


if __name__ == "__main__":
    main()
