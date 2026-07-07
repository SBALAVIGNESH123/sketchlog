"""sketchlog.rbac — Role-Based Access Control and structured audit logging.

Roles
-----
admin      : full read/write/admin on all namespaces
ingest     : write-only (ingest events) on assigned namespaces
read       : read-only (query) on assigned namespaces
read_write : ingest + query on assigned namespaces

Audit log
---------
Every access decision (ALLOW / DENY) is recorded as a structured JSON event
with HMAC-SHA256 integrity tag (tamper-evident, SOC2-ready).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence

__all__ = [
    "Role",
    "Permission",
    "TokenGrant",
    "RBACConfig",
    "AuditEvent",
    "AuditLogger",
    "RBACEnforcer",
    "check_rbac",
    "main",
]

logger = logging.getLogger(__name__)

# ── enumerations ─────────────────────────────────────────────────────────────

class Role(str, Enum):
    ADMIN      = "admin"
    INGEST     = "ingest"
    READ       = "read"
    READ_WRITE = "read_write"


class Permission(str, Enum):
    INGEST = "ingest"
    QUERY  = "query"
    ADMIN  = "admin"


_ROLE_PERMISSIONS: Dict[Role, frozenset] = {
    Role.ADMIN:      frozenset({Permission.INGEST, Permission.QUERY, Permission.ADMIN}),
    Role.INGEST:     frozenset({Permission.INGEST}),
    Role.READ:       frozenset({Permission.QUERY}),
    Role.READ_WRITE: frozenset({Permission.INGEST, Permission.QUERY}),
}

_WILDCARD = "*"

# ── dataclasses ──────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class TokenGrant:
    """Maps a token_id to a role and optional namespace scope."""
    token_id:   str
    role:       Role
    namespaces: Sequence[str] = field(default_factory=list)   # empty = all

    def __post_init__(self) -> None:
        errors: List[str] = []
        if not isinstance(self.token_id, str) or not self.token_id.strip():
            errors.append("token_id must be a non-empty string")
        if not isinstance(self.role, Role):
            try:
                object.__setattr__(self, "role", Role(self.role))
            except ValueError:
                errors.append(f"role must be one of {[r.value for r in Role]}")
        if not isinstance(self.namespaces, (list, tuple)):
            errors.append("namespaces must be a list")
        if errors:
            raise ValueError("TokenGrant errors: " + "; ".join(errors))

    def allows_namespace(self, namespace: str) -> bool:
        # Admin tokens with empty namespaces get global access;
        # non-admin tokens must list explicit namespaces.
        if not self.namespaces:
            return self.role == Role.ADMIN
        return namespace in self.namespaces or _WILDCARD in self.namespaces

    def has_permission(self, permission: Permission) -> bool:
        return permission in _ROLE_PERMISSIONS.get(self.role, frozenset())


@dataclass(frozen=True)
class RBACConfig:
    """RBAC configuration: token registry and audit settings."""
    grants:           List[TokenGrant]
    audit_file:       Optional[str]  = None   # path or None=stdout
    audit_hmac_key:   str            = ""     # empty = no HMAC
    audit_syslog:     bool           = False  # send audit events to syslog (Unix only)
    enabled:          bool           = True

    def __post_init__(self) -> None:
        errors: List[str] = []
        if not isinstance(self.grants, list):
            errors.append("grants must be a list")
        if self.audit_file is not None and not isinstance(self.audit_file, str):
            errors.append("audit_file must be a string path or None")
        if not isinstance(self.audit_hmac_key, str):
            errors.append("audit_hmac_key must be a string")
        if not isinstance(self.enabled, bool):
            object.__setattr__(self, "enabled", bool(self.enabled))
        if errors:
            raise ValueError("RBACConfig errors: " + "; ".join(errors))

    def token_grant(self, token_id: str) -> Optional[TokenGrant]:
        for g in self.grants:
            if g.token_id == token_id:
                return g
        return None


@dataclass(frozen=True)
class AuditEvent:
    """Single immutable audit log entry."""
    ts:         float
    token_id:   str
    action:     str          # e.g. "ingest", "query", "admin"
    namespace:  str
    stream:     str
    result:     str          # "ALLOW" or "DENY"
    reason:     str
    role:       Optional[str] = None
    hmac_tag:   Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ts":        self.ts,
            "token_id":  self.token_id,
            "action":    self.action,
            "namespace": self.namespace,
            "stream":    self.stream,
            "result":    self.result,
            "reason":    self.reason,
            "role":      self.role,
            "hmac_tag":  self.hmac_tag,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), separators=(",", ":"))


# ── audit logger ─────────────────────────────────────────────────────────────

class AuditLogger:
    """Thread-safe structured audit logger with optional HMAC integrity."""

    def __init__(self, config: RBACConfig) -> None:
        self._config  = config
        self._hmac_key = config.audit_hmac_key.encode() if config.audit_hmac_key else b""
        self._file: Optional[Any] = None
        self._lock = threading.Lock()
        self._syslog = False
        if config.audit_syslog:
            try:
                import syslog as _syslog  # Unix only
                self._syslog_mod = _syslog
                self._syslog = True
            except ImportError:
                pass  # Windows — syslog unavailable, fall back to stdout
        if config.audit_file:
            os.makedirs(os.path.dirname(os.path.abspath(config.audit_file)), exist_ok=True)
            self._file = open(config.audit_file, "a", encoding="utf-8")

    def _compute_hmac(self, payload: str) -> Optional[str]:
        if not self._hmac_key:
            return None
        return hmac.new(self._hmac_key, payload.encode(), hashlib.sha256).hexdigest()

    def log(
        self,
        token_id: str,
        action: str,
        namespace: str,
        stream: str,
        result: str,
        reason: str,
        role: Optional[str] = None,
    ) -> AuditEvent:
        ts   = time.time()
        base = json.dumps({
            "ts": ts, "token_id": token_id, "action": action,
            "namespace": namespace, "stream": stream,
            "result": result, "reason": reason, "role": role,
        }, separators=(",", ":"))
        tag = self._compute_hmac(base)
        event = AuditEvent(
            ts=ts, token_id=token_id, action=action,
            namespace=namespace, stream=stream,
            result=result, reason=reason, role=role, hmac_tag=tag,
        )
        line = event.to_json() + "\n"
        with self._lock:
            if self._file:
                self._file.write(line)
                self._file.flush()
            elif self._syslog:
                self._syslog_mod.syslog(self._syslog_mod.LOG_INFO, line.rstrip())
            else:
                sys.stdout.write(line)
                sys.stdout.flush()
        return event

    def close(self) -> None:
        if self._file:
            self._file.close()
            self._file = None


# ── RBAC enforcer ────────────────────────────────────────────────────────────

class RBACEnforcer:
    """Central enforcement point — checks permission and emits audit event."""

    def __init__(self, config: RBACConfig) -> None:
        self._config = config
        self._audit  = AuditLogger(config)

    def check(
        self,
        token_id: str,
        permission: Permission,
        namespace: str,
        stream:    str = "",
    ) -> bool:
        """Return True if allowed; always emits an audit event."""
        if not self._config.enabled:
            self._audit.log(token_id, permission.value, namespace, stream,
                            "ALLOW", "rbac_disabled")
            return True

        grant = self._config.token_grant(token_id)
        if grant is None:
            self._audit.log(token_id, permission.value, namespace, stream,
                            "DENY", "unknown_token")
            return False

        if not grant.allows_namespace(namespace):
            self._audit.log(token_id, permission.value, namespace, stream,
                            "DENY", "namespace_not_allowed", role=grant.role.value)
            return False

        if not grant.has_permission(permission):
            self._audit.log(token_id, permission.value, namespace, stream,
                            "DENY", "permission_not_granted", role=grant.role.value)
            return False

        self._audit.log(token_id, permission.value, namespace, stream,
                        "ALLOW", "ok", role=grant.role.value)
        return True

    def close(self) -> None:
        self._audit.close()


# ── check_rbac helper ─────────────────────────────────────────────────────────

@dataclass(frozen=True)
class RBACCheckResult:
    status:      str    # PASS / WARN / FAIL
    grants_count: int
    audit_output: str
    hmac_enabled: bool
    issues:       List[str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status":       self.status,
            "grants_count": self.grants_count,
            "audit_output": self.audit_output,
            "hmac_enabled": self.hmac_enabled,
            "issues":       self.issues,
        }


def check_rbac(config: RBACConfig) -> RBACCheckResult:
    """Validate RBAC config for production readiness."""
    issues: List[str] = []

    if not config.enabled:
        issues.append("RBAC is disabled — all requests will be allowed")

    if not config.grants:
        issues.append("No token grants configured")

    admin_count = sum(1 for g in config.grants if g.role == Role.ADMIN)
    if admin_count == 0 and config.enabled:
        issues.append("No admin token configured — cannot manage namespaces")

    if not config.audit_hmac_key:
        issues.append("WARN: audit_hmac_key not set — audit log is not tamper-evident")
    elif len(config.audit_hmac_key) < 32:
        issues.append("WARN: audit_hmac_key is too short (< 32 chars) — use a strong random secret")
    elif config.audit_hmac_key in ("demo-hmac-key-change-in-production", "changeme", "secret", "test"):
        issues.append("WARN: audit_hmac_key appears to be a demo/weak value — replace with a strong random secret")

    audit_output = config.audit_file if config.audit_file else "stdout"

    if not issues:
        status = "PASS"
    elif any(i.startswith("WARN") for i in issues) and len(issues) == 1:
        status = "WARN"
    else:
        status = "FAIL" if not config.enabled or not config.grants else "WARN"

    return RBACCheckResult(
        status=status,
        grants_count=len(config.grants),
        audit_output=audit_output,
        hmac_enabled=bool(config.audit_hmac_key),
        issues=issues,
    )


# ── CLI ───────────────────────────────────────────────────────────────────────

def main(argv: Optional[List[str]] = None) -> int:
    import argparse
    p = argparse.ArgumentParser(
        prog="sketchlog-rbac-check",
        description="Validate SketchLog RBAC configuration and emit a demo audit event.",
    )
    p.add_argument("--config",  help="Path to JSON RBAC config file")
    p.add_argument("--format",  choices=["text", "json"], default="text")
    p.add_argument("--demo",    action="store_true", help="Run with demo config")
    args = p.parse_args(argv)

    if args.demo or not args.config:
        config = RBACConfig(
            grants=[
                TokenGrant("admin-token-1",  Role.ADMIN,      []),
                TokenGrant("ingest-token-1", Role.INGEST,     ["prod"]),
                TokenGrant("reader-token-1", Role.READ,       ["prod", "staging"]),
                TokenGrant("rw-token-1",     Role.READ_WRITE, ["staging"]),
            ],
            audit_file=None,
            audit_hmac_key="demo-hmac-key-change-in-production",
            enabled=True,
        )
    else:
        try:
            with open(args.config, encoding="utf-8") as fh:
                raw = json.load(fh)
            if not isinstance(raw, dict):
                print("ERROR: config must be a JSON object", file=sys.stderr)
                return 2
            grants = [TokenGrant(**g) for g in raw.get("grants", [])]
            config = RBACConfig(
                grants=grants,
                audit_file=raw.get("audit_file"),
                audit_hmac_key=raw.get("audit_hmac_key", ""),
                enabled=raw.get("enabled", True),
            )
        except (ValueError, TypeError, KeyError) as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2

    result = check_rbac(config)

    if args.format == "json":
        print(json.dumps(result.to_dict(), indent=2))
    else:
        print("SketchLog RBAC configuration check")
        print(f"  Grants     : {result.grants_count}")
        print(f"  Audit out  : {result.audit_output}")
        print(f"  HMAC       : {'enabled' if result.hmac_enabled else 'DISABLED'}")
        for issue in result.issues:
            print(f"  {'WARN' if issue.startswith('WARN') else 'ISSUE'}: {issue}")
        print(f"\nResult: {result.status}")

    return 0 if result.status in ("PASS", "WARN") else 1


if __name__ == "__main__":
    sys.exit(main())
