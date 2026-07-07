# RBAC and Audit Logging

SketchLog's role-based access control (RBAC) module enforces per-namespace
permissions on every API call and records every access decision to a
tamper-evident, SOC2-ready audit log.

## Quick start

```bash
sketchlog-rbac-check --demo
```

Sample output:

```text
SketchLog RBAC configuration check
  Grants     : 4
  Audit out  : stdout
  HMAC       : enabled

Result: PASS
```

## Roles

| Role | Ingest | Query | Admin |
|------|--------|-------|-------|
| `admin` | ✅ | ✅ | ✅ |
| `read_write` | ✅ | ✅ | ❌ |
| `ingest` | ✅ | ❌ | ❌ |
| `read` | ❌ | ✅ | ❌ |

## Configuration

```json
{
  "grants": [
    {"token_id": "prod-admin",   "role": "admin",      "namespaces": []},
    {"token_id": "svc-ingest",   "role": "ingest",     "namespaces": ["prod"]},
    {"token_id": "svc-reader",   "role": "read",       "namespaces": ["prod","staging"]},
    {"token_id": "svc-rw",       "role": "read_write", "namespaces": ["staging"]}
  ],
  "audit_file": "/var/log/sketchlog/audit.jsonl",
  "audit_hmac_key": "YOUR_SECRET_KEY",
  "enabled": true
}
```

```bash
sketchlog-rbac-check --config rbac.json
```

## Audit log format

Each line is a JSON object:

```json
{
  "ts": 1720000000.123,
  "token_id": "svc-reader",
  "action": "query",
  "namespace": "prod",
  "stream": "latency",
  "result": "ALLOW",
  "reason": "ok",
  "role": "read",
  "hmac_tag": "e3b0c44298fc1c149afb..."
}
```

- **`result`** — `ALLOW` or `DENY`
- **`hmac_tag`** — HMAC-SHA256 of the event payload for tamper detection

## Python API

```python
from sketchlog.rbac import Role, Permission, TokenGrant, RBACConfig, RBACEnforcer

config = RBACConfig(
    grants=[
        TokenGrant("my-token", Role.READ_WRITE, ["prod"]),
    ],
    audit_file="/var/log/sketchlog/audit.jsonl",
    audit_hmac_key=os.environ["SKETCHLOG_HMAC_KEY"],
)
enforcer = RBACEnforcer(config)

if enforcer.check("my-token", Permission.INGEST, "prod", "latency"):
    # proceed
    pass
```

## Wiring into server.py

Pass `RBACConfig` to `sketchlog.server.create_app()`:

```python
from sketchlog.rbac import RBACConfig, TokenGrant, Role
from sketchlog.server import create_app

app = create_app(rbac_config=RBACConfig(grants=[...], audit_hmac_key="..."))
```

## CLI reference

| Flag | Default | Description |
|------|---------|-------------|
| `--config` | — | Path to JSON config file |
| `--format` | `text` | Output format: `text` or `json` |
| `--demo` | off | Run with built-in demo grants |

Exit codes: `0` = PASS/WARN, `1` = FAIL, `2` = bad config.

## Caveats

1. Tokens are validated by `token_id` string only — rotate tokens regularly.
2. HMAC integrity requires keeping `audit_hmac_key` secret; rotate if compromised.
3. Audit log is append-only — implement external rotation (logrotate, fluentd).
4. RBAC does not replace network-level controls (TLS, firewall).
5. Empty `namespaces` list means the token has access to all namespaces.
6. `enabled: false` disables all checks — use only for development.
