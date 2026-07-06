# Namespace Admin Center

The `sketchlog-namespace-admin` CLI and Python API give operators a
**secret-safe, read-only view** of every namespace in a multi-tenant
SketchLog deployment.

---

## Quick start

```bash
pip install sketchlog

# Live cluster
export SKETCHLOG_ADMIN_TOKEN="sk_live_..."
sketchlog-namespace-admin --url https://sketchlog.example.com

# Built-in demo (no server required)
sketchlog-namespace-admin --demo
sketchlog-namespace-admin --demo --format json
```

---

## CLI reference

| Flag | Default | Description |
|---|---|---|
| `--url URL` | — | Admin API base URL (`https://` required) |
| `--token TOKEN` | `""` | Bearer token (prefer `SKETCHLOG_ADMIN_TOKEN` env var) |
| `--timeout N` | `10` | Request timeout in seconds |
| `--format text\|json` | `text` | Output format |
| `--top N` | `5` | Top streams to display per namespace |
| `--demo` | off | Run with built-in synthetic data |
| `--version` | — | Print version and exit |

### Exit codes

| Code | Meaning |
|---|---|
| `0` | All namespaces healthy and within quota |
| `1` | At least one namespace is `warn`/`degraded` or over quota threshold; also returned for fetch or transport errors |
| `2` | Bad configuration (missing `--url`, `http://` URL, invalid flags) |

---

## Example output

```text
+----------------------------------------------------------------------------------------+
|  SketchLog -- Namespace Admin Center                                                   |
+----------------------------------------------------------------------------------------+
  Namespaces : 5

  [v] Platform (platform)  health=healthy
      streams=42  sketches=318  memory=128.00 MiB / 512.00 MiB (25.0 %)
      tokens : sk_plat00... (ingest)  sk_plat01... (read-only)
      top streams (by event rate, top 5):
        stream-00                        182.30 ev/s     48.00 KiB  sketches=3
        stream-01                         95.12 ev/s    128.00 KiB  sketches=5
      tags : team=platform  env=production

  [?] Ml-Pipeline (ml-pipeline)  health=warn  [STALE]
      streams=15  sketches=90  memory=420.00 MiB / 512.00 MiB (82.0 %) [WARN]
      alerts: crit=1  warn=2  info=0  silenced=1
      ...
```

---

## JSON output schema

```json
{
  "namespaces": [
    {
      "name": "platform",
      "display_name": "Platform",
      "stream_count": 42,
      "sketch_count": 318,
      "memory_bytes": 134217728,
      "quota_bytes": 536870912,
      "quota_used_fraction": 0.25,
      "quota_warn": false,
      "last_activity_at": 1751234567.0,
      "stale": false,
      "top_streams": [ ... ],
      "tokens": [
        {
          "token_id": "tok-platform-00",
          "label": "ingest",
          "scopes": ["ingest"],
          "created_at": 1748000000.0,
          "expires_at": 1759000000.0,
          "last_used_at": 1751230000.0,
          "active": true,
          "prefix": "sk_plat00"
        }
      ],
      "alerts": {
        "active_critical": 0, "active_warning": 0,
        "active_info": 0, "silenced": 0, "total_active": 0
      },
      "health": "healthy",
      "tags": {"team": "platform", "env": "production"}
    }
  ]
}
```

---

## Secret-safe design

- Raw token values are **never** stored in `TokenInfo` and cannot leak through
  the API or CLI output.
- Token `prefix` (first ≤ 8 characters) is exposed for identification only.
- `SKETCHLOG_ADMIN_TOKEN` env var takes priority over `--token` to avoid
  secrets appearing in process listings.
- Credential-containing URLs (e.g. `https://user:pass@host`) are redacted in
  error messages before display.
- Only `https://` URLs are accepted; `http://` is rejected at config parse time.

---

## Python API

```python
from sketchlog.namespace_admin import (
    NamespaceAdminConfig, _fetch_namespaces, render_json
)

config = NamespaceAdminConfig(
    url="https://sketchlog.example.com",
    top_streams=10,
)
namespaces = _fetch_namespaces(config)
for ns in namespaces:
    print(ns.name, ns.memory_bytes, ns.health)
print(render_json(namespaces))
```

---

## API endpoint expected

`GET /api/v1/admin/namespaces`  →  `{"namespaces": [ ... ]}`

Malformed or missing peer entries are skipped gracefully; the CLI never
crashes on a partial response.

---

## Building on existing alerting

Each `NamespaceInfo.alerts` field is a lightweight count summary sourced
from the existing SketchLog alerting engine (`alert_manager`).  The admin
view intentionally shows counts only — use `sketchlog-alert-manager` to
list, silence, or route individual alerts.

---

## Caveats

1. **Read-only** — this tool never mutates namespace state.
2. **Snapshot in time** — values reflect the server response at the moment of
   the request; no streaming or polling.
3. **Memory figures are estimates** — actual resident set may differ depending
   on allocator overhead and GC pressure.
4. **Quota enforcement is server-side** — this tool warns at ≥ 80 % usage but
   cannot enforce limits.
5. **`stale` flag uses local clock** — avoid interpreting it if your local
   clock and the server clock are significantly skewed.
6. **Demo data is synthetic** — `--demo` uses a fixed seed and does not
   reflect any real deployment.
