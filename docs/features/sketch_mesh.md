# Sketch Mesh

Mesh mode exchanges bounded version vectors and JSON origin snapshots,
then merges visible origins at query time. It is eventually consistent.
Deletion creates a versioned origin tombstone so stale relays cannot resurrect
the deleted snapshot.

Every peer origin must be listed in `SKETCHLOG_PEER_ALLOWLIST`. Discovered
addresses are canonicalized, credentials/paths/query strings are rejected,
redirects are disabled, membership is capped, and every internal request needs
`SKETCHLOG_CLUSTER_SECRET`.

Digest requests, responses, and snapshot syncs are capped by
`SKETCHLOG_MAX_MESH_PAYLOAD_BYTES` (40 MiB by default, 64 MiB maximum).
Large multi-stream exchanges are split across requests and anti-entropy rounds;
the configured limit must accommodate the largest individual serialized stream.
Deletion markers are retained for correctness and bounded by
`SKETCHLOG_MAX_LOCAL_TOMBSTONES`; when the cap is reached, new deletes fail
closed with HTTP 503 instead of allowing stale snapshots to be resurrected.
With SQL storage, state deletion and tombstone persistence share one
transaction.

```bash
export SKETCHLOG_NODE_ID=node-1
export SKETCHLOG_ADVERTISED_ADDRESS=http://10.0.0.1:8000
export SKETCHLOG_PEERS=http://10.0.0.2:8000
export SKETCHLOG_PEER_ALLOWLIST=http://10.0.0.1:8000,http://10.0.0.2:8000
export SKETCHLOG_CLUSTER_SECRET='rotate-this-secret'
sketchlog-server
```

Use the Helm chart for stable Kubernetes identities. Increasing replica count
without `mesh.enabled=true` is rejected because independent in-memory replicas
behind a load balancer do not form a coherent service.

During partitions, each node serves the origins it has observed. After rejoin,
newer snapshots and tombstones converge. Mesh uses deterministic Python state
for cross-node reproducibility, so benchmark mesh capacity separately.
