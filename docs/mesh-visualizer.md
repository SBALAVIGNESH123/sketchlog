# Sketch Mesh Visualizer

Visualize the state of a distributed Sketch Mesh cluster: node membership,
gossip convergence, snapshot freshness, anti-entropy sync rates, and partition
warnings — all from a single command.

---

## Quick start

```bash
pip install sketchlog

# Live cluster
sketchlog-mesh-viz --url https://sketchlog.example.com

# Demo mode (no server required)
sketchlog-mesh-viz --demo
```

A passing run prints a banner like:

```text
╔══════════════════════════════════════════════════════════════════════════════════════════════════════════════╗
║  SketchLog — Sketch Mesh Cluster Visualizer                                                                  ║
╚══════════════════════════════════════════════════════════════════════════════════════════════════════════════╝

  Self       : node-000  (10.0.0.1:7946)
  Checked at : 2026-07-06T12:00:00Z
  Gossip convergence : 2.341 s

  Peers : 5 active  1 suspect  1 dead  (total 7)
  Cluster totals : 840 streams  7350 sketches  mean AE rate 2.6183/s

  NODE ID                  ADDRESS                STATE      GOSSIP(s)    SNAP(s)  STREAMS  SKETCHES     AE /s
  ──────────────────────────────────────────────────────────────────────────────────────────────────────────
  node-001                 10.0.0.2:7946        ✓ active       1.200        5.000       42      385    1.0000
  node-002                 10.0.0.3:7946        ✓ active       0.800        4.200       38      310    2.1000
  ...

  Overall status : PASS ✓
```

---

## CLI reference

| Flag | Default | Description |
|---|---|---|
| `--url URL` | — | SketchLog server base URL (`https://` required). Required unless `--demo`. |
| `--token TOKEN` | — | Auth bearer token. Prefer `SKETCHLOG_AUTH_TOKEN` env var. |
| `--timeout SEC` | `10` | HTTP timeout in seconds. |
| `--gossip-warn SEC` | `10.0` | Warn if gossip convergence exceeds this. |
| `--snapshot-stale SEC` | `30.0` | Warn if peer snapshot is older than this. |
| `--format text\|json` | `text` | Output format. |
| `--demo` | off | Run with synthetic data — no server needed. |

---

## Exit codes

| Code | Meaning |
|---|---|
| `0` | All peers healthy — `PASS` |
| `1` | Warning or failure condition detected |
| `2` | Bad config / missing required argument |

---

## JSON output

```bash
sketchlog-mesh-viz --demo --format json
```

```json
{
  "self_node_id": "node-000",
  "self_address": "10.0.0.1:7946",
  "checked_at": 1750000000.0,
  "overall_status": "warn",
  "gossip_convergence_s": 4.231,
  "gossip_slow": false,
  "partition_detected": false,
  "aggregate": {
    "total_streams": 840,
    "total_sketches": 7350,
    "mean_anti_entropy_rate": 2.6183
  },
  "peer_counts": {
    "active": 5,
    "suspect": 1,
    "dead": 1,
    "total": 7
  },
  "peers": [
    {
      "node_id": "node-001",
      "address": "10.0.0.2:7946",
      "state": "active",
      "gossip_age_s": 1.2,
      "snapshot_age_s": 5.0,
      "snapshot_stale": false,
      "stream_count": 42,
      "sketch_count": 385,
      "anti_entropy_rate": 1.0
    }
  ]
}
```

---

## Metric reference

| Metric | Description | Warn threshold |
|---|---|---|
| `gossip_convergence_s` | Estimated time for gossip to propagate across the cluster | `> 10 s` (configurable) |
| `gossip_age_s` (per peer) | Seconds since this node last received gossip from the peer | `> gossip-warn` |
| `snapshot_age_s` (per peer) | Seconds since the last anti-entropy snapshot sync with this peer | `> 30 s` (configurable) |
| `anti_entropy_rate` (per peer) | Anti-entropy syncs per second (rolling window) | — |
| `stream_count` (per peer) | Number of SketchLog streams the peer is responsible for | — |
| `sketch_count` (per peer) | Total sketch objects (DDSketch, HLL, CMS) on the peer | — |

---

## Node states

| State | Icon | Meaning |
|---|---|---|
| `active` | ✓ | Peer is reachable and participating in gossip |
| `suspect` | ? | Gossip heartbeat missed; under failure-detection timer |
| `dead` | ✗ | Peer declared dead by the failure detector |

---

## Overall status logic

| Condition | Status |
|---|---|
| Any dead peer fraction ≥ 50 % | `FAIL` |
| `partition_detected: true` from server | `FAIL` |
| Any dead or suspect peer | `WARN` |
| Any stale snapshot | `WARN` |
| Gossip convergence above threshold | `WARN` |
| All peers active, fresh snapshots, fast gossip | `PASS` |

---

## Partition warnings

When ≥ 50 % of known peers are dead, the visualizer raises a `FAIL` and
prints a partition warning regardless of the server-side `partition_detected`
flag. This gives a local-view safety net even when the server has not yet
declared a partition.

---

## Python API

```python
from sketchlog.mesh_visualizer import (
    _build_demo_status,
    _fetch_mesh_status,
    MeshVisualizerConfig,
    render_text,
)

# Live fetch
cfg = MeshVisualizerConfig(
    url="https://sketchlog.example.com",
    gossip_warn_s=10.0,
    snapshot_stale_s=30.0,
)
status = _fetch_mesh_status(cfg)
print(render_text(status))
print(status.overall_status().value)   # 'pass' | 'warn' | 'fail'
print(status.to_dict())                # JSON-serialisable dict
```

---

## sketchlog-doctor integration

```bash
# Include mesh check in a doctor run
sketchlog-mesh-viz --url https://sketchlog.example.com --format json \
  | jq '.overall_status'
```

Exit code `0` / `1` integrates naturally with shell health-check pipelines
and Kubernetes `livenessProbe` / `readinessProbe` exec probes.

---

## Caveats

1. **Read-only** — this tool only reads cluster state. It does not perform any
   repairs, forced rejoins, or membership changes.
2. **Local view** — metrics reflect the SketchLog server's current gossip view,
   not a ground-truth global snapshot.
3. **Clock skew** — `snapshot_age_s` and `gossip_age_s` depend on the server's
   local clock. Large NTP skew can cause false stale warnings.
4. **Synthetic demo** — `--demo` output uses a fixed seed and does not represent
   real production data.
5. **Stdlib only** — no external HTTP or metrics libraries; raw `urllib` is used.
6. **HTTPS required** — plaintext `http://` URLs are rejected to protect auth
   tokens in transit.
