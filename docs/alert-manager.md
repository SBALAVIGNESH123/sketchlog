# Alert Management Center

The SketchLog **Alert Management Center** adds a production-ready operational
layer on top of SketchLog's existing alerting engine.

- **Active alerts view** — see exactly what is firing right now.
- **Alert history** — searchable by namespace, stream, or severity.
- **Silences and maintenance windows** — suppress expected noise.
- **Routing rules** — critical alerts to PagerDuty, warnings to Slack,
  everything else to a default webhook.
- **Delivery adapters** — Slack, Discord, PagerDuty, Opsgenie, webhook
  (standard library only, zero extra packages).
- **Retry logic** — 3-attempt exponential back-off, delivery status tracking.
- **Recovery notifications** — auto-sent when an alert resolves.

---

## Quick start

```bash
pip install sketchlog
sketchlog-alert-manager --format json
```

---

## Configuration file

```json
{
  "persist_path": "/var/lib/sketchlog/alerts.json",
  "default_channels": ["ops-webhook"],
  "channels": [
    {
      "name": "ops-webhook",
      "adapter": "webhook",
      "url": "https://hooks.example.com/sketchlog",
      "token_env": "SKETCHLOG_WEBHOOK_TOKEN"
    },
    {
      "name": "slack-oncall",
      "adapter": "slack",
      "url": "https://hooks.slack.com/services/T.../B.../..."
    },
    {
      "name": "pagerduty-p1",
      "adapter": "pagerduty",
      "url": "https://events.pagerduty.com/v2/enqueue",
      "token_env": "PAGERDUTY_ROUTING_KEY"
    },
    {
      "name": "discord-dev",
      "adapter": "discord",
      "url": "https://discord.com/api/webhooks/..."
    }
  ],
  "routes": [
    {"channels": ["pagerduty-p1"], "match_severity": "critical"},
    {"channels": ["slack-oncall"],  "match_severity": "warning"}
  ]
}
```

```bash
sketchlog-alert-manager --config /etc/sketchlog/alert-manager.json
```

---

## CLI reference

| Flag | Description |
|---|---|
| `--config FILE` | JSON config |
| `--format text\|json` | Output format (default: `text`) |
| `--ingest FILE` | Ingest a JSON list of alert dicts |
| `--add-silence FILE` | Add a silence from a JSON dict |
| `--resolve ALERT_ID` | Resolve an active alert by ID |

Exit codes: `0` = success, `2` = config/input error.

---

## Silences and maintenance windows

```json
{
  "starts_at": 1751750400,
  "ends_at":   1751836800,
  "match_namespace": "prod",
  "match_severity":  "warning",
  "comment": "Scheduled maintenance",
  "created_by": "ops-team"
}
```

```bash
sketchlog-alert-manager --config /etc/sketchlog/alert-manager.json \
  --add-silence /tmp/maint.json
```

---

## Routing rules

| Matcher | Behaviour |
|---|---|
| `match_namespace` | Exact namespace (empty = any) |
| `match_stream` | Exact stream name |
| `match_severity` | `critical`, `warning`, or `info` |
| `match_tenant` | Exact tenant |
| `match_labels` | All key/value pairs must match |
| `continue_matching` | If `true`, keep evaluating subsequent routes |

First match wins unless `continue_matching: true`.
Unmatched alerts go to `default_channels`.

---

## Delivery adapters

| Adapter | Auth |
|---|---|
| `slack` | None (webhook URL is self-authenticating) |
| `discord` | None (webhook URL) |
| `pagerduty` | `token_env` = routing key |
| `opsgenie` | `token_env` = API key |
| `webhook` | Optional `token_env` -> `Authorization: Bearer ...` |

---

## Python API

```python
from sketchlog.alert_manager import Alert, AlertManager, ChannelConfig, Route, Silence
from time import time

am = AlertManager(
    channels=[ChannelConfig(name="ops", adapter="webhook",
                            url="https://hooks.example.com/sk",
                            token_env="SK_WEBHOOK_TOKEN")],
    routes=[Route(channels=["pd"], match_severity="critical")],
    default_channels=["ops"],
)

records = am.ingest(Alert(name="HighLatency", namespace="prod",
                          stream="api_latency", severity="critical"))

am.add_silence(Silence(starts_at=time(), ends_at=time()+3600,
                       match_namespace="prod", match_severity="warning"))
am.resolve(records[0].alert_id if records else "")
print(am.active_alerts())
print(am.alert_history(namespace="prod"))
```

---

## How this builds on existing SketchLog alerting

SketchLog's alerting engine fires events when sketch thresholds are crossed.
The Alert Management Center adds:

1. Operational visibility (active list + searchable history)
2. Noise control (silences / maintenance windows)
3. Multi-channel routing (severity / namespace / label fan-out)
4. Delivery reliability (retry, status tracking, recovery notifications)

Inject alerts via `AlertManager.ingest()` or `--ingest`.
The existing alert engine needs no modification.

---

## Caveats

1. **Stdlib only** — delivery blocks the calling thread; wrap in a thread pool
   for high-volume use.
2. **In-memory state** — lost on restart unless `persist_path` is set.
3. **Single-process** — not safe across multiple processes against the same
   `persist_path` without an external lock.
4. **No deduplication** — same alert fired twice is stored twice.
5. **Token security** — always use `token_env`; never commit inline `token`
   values to source control.
6. **Opsgenie close URL** — update `url` to the alias-close endpoint if needed.
