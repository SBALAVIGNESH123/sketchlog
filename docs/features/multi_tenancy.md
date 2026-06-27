# Multi-Tenancy

SketchLog supports native **Multi-Tenancy** through isolated `namespaces`. This allows a single deployed SketchLog cluster to securely serve multiple teams, applications, or external customers without data bleeding or "noisy neighbor" problems.

## Namespaces

Every stream in SketchLog is uniquely identified by a composite key: `(namespace, stream_id)`.

By default, data is written to the `default` namespace. You can segment traffic by defining custom namespaces on the fly:

```bash
# Ingesting to team-alpha
curl -X POST "http://localhost:8000/v1/namespaces/team-alpha/streams/web/events"

# Ingesting to team-beta
curl -X POST "http://localhost:8000/v1/namespaces/team-beta/streams/web/events"
```

## Capacity Limits (Noisy Neighbor Protection)

To prevent a single tenant from exhausting server memory by creating millions of streams, SketchLog enforces an LRU capacity limit per namespace.

This is controlled by the `MAX_STREAMS_PER_NS` environment variable (default: `10,000`). 

When a namespace exceeds this limit, the least recently used streams are flushed to the database (if a `SKETCHLOG_DB_URI` is configured) and evicted from memory. This ensures that `team-alpha` cannot cause performance degradation for `team-beta`.

## Authentication & Authorization

While SketchLog itself focuses on fast ingestion, you can pair namespaces with API gateways or the built-in HTTP middleware to restrict access. A common pattern is to issue API keys mapped to specific namespaces.
