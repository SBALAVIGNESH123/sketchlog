# Standalone server

Install and start the versioned entry point:

```bash
pip install "sketchlog[server]"
sketchlog-server
```

The module entry point binds `127.0.0.1:8000` by default. Containers set an
explicit externally reachable host.

## Configuration

| Variable | Default | Meaning |
|---|---:|---|
| `SKETCHLOG_HOST` | `127.0.0.1` | Module-entry-point bind address |
| `SKETCHLOG_PORT` | `8000` | Bind port |
| `SKETCHLOG_MAX_STREAMS` | `1000` | Process-wide resident stream cap |
| `SKETCHLOG_NAMESPACE_QUOTA_MB` | `50` | Per-namespace planning quota, using 130 KiB per stream |
| `SKETCHLOG_MAX_BATCH_SIZE` | `10000` | Maximum submitted items per batch |
| `SKETCHLOG_MAX_REQUEST_BYTES` | `1048576` | Public request body limit |
| `SKETCHLOG_MAX_MESH_PAYLOAD_BYTES` | `41943040` | Authenticated digest/sync request and peer-response limit; range 1 KiB–64 MiB |
| `SKETCHLOG_MAX_LOCAL_TOMBSTONES` | `100000` | Per-node durable deletion-marker cap; range 1–1,000,000 |
| `SKETCHLOG_AUTH_TOKEN` | unset | Administrator token for every `/v1/*` endpoint |
| `SKETCHLOG_NAMESPACE_TOKENS` | unset | JSON token-to-namespace-list authorization map |
| `SKETCHLOG_TLS_CERT`, `SKETCHLOG_TLS_KEY` | unset | Both are required to enable direct TLS |
| `SKETCHLOG_DB_URI` | unset | SQLAlchemy async URI; enables durable save/load |
| `SKETCHLOG_MEMORY_THRESHOLD` | `90` | Readiness failure percentage, in `(0,100]` |
| `SKETCHLOG_MEMORY_LIMIT_BYTES` | auto | Explicit readiness memory limit; cgroup v2 is auto-detected |
| `SKETCHLOG_ANOMALY_SENSITIVITY` | `0.2` | Default KS anomaly threshold in `(0,1]` |
| `SKETCHLOG_NODE_ID` | process-derived | Mesh origin identity |
| `SKETCHLOG_PEERS` | unset | Comma-separated seed origins |
| `SKETCHLOG_ADVERTISED_ADDRESS` | unset | This node's allowlisted mesh origin |
| `SKETCHLOG_PEER_ALLOWLIST` | peers | Exact outbound/discovered peer origins |
| `SKETCHLOG_CLUSTER_SECRET` | unset | Required whenever mesh is enabled |
| `SKETCHLOG_SYNC_INTERVAL` | `5.0` | Mesh anti-entropy interval in seconds |

With storage enabled, eviction applies backpressure and removes a stream only
after its durable save succeeds. Without storage, eviction is intentionally
ephemeral. File checkpoints and restored database payloads are validated as
untrusted data before native allocation.

`GET /health` is process liveness. `GET /ready` checks the effective memory
limit and configured storage dependency. `GET /metrics` exports bounded route
templates; it never labels series with namespace or stream IDs.

## Helm

Single-node mode is the default. The chart rejects `replicaCount > 1` unless
mesh is explicitly enabled:

```bash
helm upgrade --install sketchlog ./charts/sketchlog \
  --set replicaCount=3 \
  --set mesh.enabled=true \
  --set-string mesh.clusterSecret="$CLUSTER_SECRET"
```

Mesh mode renders a StatefulSet, headless peer service, stable node IDs,
allowlisted pod origins, and a Secret. Autoscaling is accepted only in mesh
mode and renders an `autoscaling/v2` HPA.

The default pod runs as UID 10001 with a read-only root filesystem, dropped
capabilities, `RuntimeDefault` seccomp, and no mounted service-account token.
Use `extraEnv` with `secretKeyRef` for server authentication or database
credentials instead of committing secret values to a Helm values file.

The generated public contract is committed at `protocol/openapi.yaml` and the
interactive form is available at `/docs`.
