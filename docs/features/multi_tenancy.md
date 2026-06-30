# Namespace isolation

Every stream is keyed by `(namespace, stream_id)`. A process-wide
`SKETCHLOG_MAX_STREAMS` cap and a namespace memory quota bound resident state.
When storage is enabled, eviction waits for a successful durable save; without
storage, evicted state is intentionally discarded.

For security isolation, configure namespace-scoped tokens as JSON:

```bash
export SKETCHLOG_NAMESPACE_TOKENS='{
  "tenant-a-secret": ["tenant-a"],
  "tenant-b-secret": ["tenant-b", "tenant-b-staging"]
}'
```

Send the selected token in `X-SketchLog-Auth-Token`. The policy is enforced for
ingest, reads, deletes, diffing, SLOs, anomaly checks, SQL, aggregation, and
WebSockets. `SKETCHLOG_AUTH_TOKEN`, when configured, is an administrator token
that can access all namespaces.

If neither setting is configured, namespaces are organizational labels only
and provide no security boundary. Use TLS directly or at a trusted gateway;
tokens sent over plaintext HTTP are not protected.
