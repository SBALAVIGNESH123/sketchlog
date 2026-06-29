# Streaming SQL

`POST /v1/query` evaluates a bounded aggregate grammar against an existing live
stream. It does not retain or reconstruct raw rows.

```sql
SELECT p99(latency) FROM "default/payment"
SELECT count_unique(users) FROM "default/auth"
SELECT event_count(errors, 'HTTP_500') FROM "default/web"
```

```bash
curl -X POST http://localhost:8000/v1/query \
  -H 'Content-Type: application/json' \
  -H "X-SketchLog-Auth-Token: $TOKEN" \
  -d '{"query":"SELECT p99(latency) FROM \"default/payment\""}'
```

Supported aggregates are `p50`, `p95`, `p99`, `count_unique` (alias
`unique_count`), and `event_count`. Live queries allow one
`[namespace/]stream` source and no `GROUP BY`; grouping remains available only
in the embedded `SQLStreamEngine` row-ingestion API.

`HAVING` uses a bounded arithmetic/comparison/boolean AST. Function calls,
attributes, indexing, comprehensions, and arbitrary Python evaluation are not
allowed. Request length is capped at 4,096 characters and namespace
authorization is enforced after parsing the source.

Percentiles and cardinality are approximate according to their underlying
sketches. Event counts are Count-Min upper-bound estimates.
