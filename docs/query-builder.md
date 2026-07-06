# Query Builder for Streaming SQL

`sketchlog-query-builder` is an offline CLI and Python API that makes SketchLog
Streaming SQL queries easier to write, validate, and share.

---

## Quick start

```bash
pip install sketchlog

# Validate a query
sketchlog-query-builder validate "SELECT id FROM my_stream;"

# Autocomplete a prefix
sketchlog-query-builder complete "APPROX"

# Show example templates
sketchlog-query-builder templates

# Explain a query offline
sketchlog-query-builder explain "SELECT APPROX_QUANTILE(latency_ms,0.99) FROM s GROUP BY TUMBLE(ts,INTERVAL '1' MINUTE) EMIT FINAL;"

# Generate a copyable curl command
sketchlog-query-builder api-request "SELECT id FROM s;" --server-url https://myhost:8080

# Save a query
sketchlog-query-builder save my-p99 "SELECT APPROX_QUANTILE(latency_ms,0.99) AS p99 FROM s EMIT FINAL;"

# List saved queries
sketchlog-query-builder list-saved

# Show query history
sketchlog-query-builder history
```

---

## CLI reference

| Subcommand | Required args | Key options |
|---|---|---|
| `complete` | `prefix` | `--streams STREAM ...` |
| `validate` | `sql` or `@file` | `--format text\|json` |
| `explain` | `sql` or `@file` | `--format text\|json` |
| `api-request` | `sql`, `--server-url` | `--namespace`, `--output json\|curl\|python` |
| `templates` | — | `--format text\|json` |
| `save` | `name`, `sql` | `--description`, `--tags`, `--store` |
| `list-saved` | — | `--tag`, `--store`, `--format` |
| `history` | — | `--limit`, `--store`, `--format` |

All subcommands accept `--format text` (default) or `--format json`.

---

## Exit codes

| Code | Meaning |
|---|---|
| `0` | Success / query valid |
| `1` | Query invalid (validate/explain) |
| `2` | Bad config or missing required argument |

---

## Autocomplete

```bash
sketchlog-query-builder complete "APPROX"
```

Returns keywords, aggregate functions, scalar functions, stream names (when
`--streams` is provided), and column names (Python API only).

```bash
sketchlog-query-builder --format json complete "COUNT"
```

```json
[
  {"label": "COUNT()", "kind": "aggregate_function", "detail": ""},
  ...
]
```

---

## Query validation

```bash
sketchlog-query-builder validate "SELECT * FROM my_stream"
```

```text
  Status : VALID
  WARN   [SELECT_STAR] (pos 0): SELECT * fetches all columns. Prefer explicit column names for production queries.
  WARN   [MISSING_SEMICOLON]: Query does not end with a semicolon.
```

Validation checks:

| Code | Severity | Description |
|---|---|---|
| `EMPTY_QUERY` | error | Query is empty |
| `MISSING_SELECT` | error | No SELECT clause |
| `MISSING_FROM` | error | No FROM clause |
| `UNBALANCED_PAREN` | error | Mismatched parentheses |
| `UNKNOWN_FUNCTION` | warning | Unrecognised function name |
| `MISSING_EMIT` | warning | Windowed query without EMIT FINAL/CHANGES |
| `SELECT_STAR` | warning | SELECT * in production query |
| `MISSING_SEMICOLON` | warning | No trailing semicolon |

---

## Explain plan

```bash
sketchlog-query-builder explain "SELECT service, APPROX_QUANTILE(latency_ms, 0.99) AS p99 FROM request_latency GROUP BY service, TUMBLE(ts, INTERVAL '5' MINUTE) EMIT FINAL;"
```

```text
Query Explain Plan
========================================
  1. Scan stream(s): request_latency
  2. Assign TUMBLE window
  3. Accumulate sketch(es)
  4. Group by key(s)
  5. Emit FINAL results

  Streams referenced : request_latency
  Uses sketch ops    : yes
  Uses window        : yes
  Note               : Sketch functions use approximate algorithms; results are probabilistic.
```

The explain plan is **offline and heuristic**. For the authoritative server-side
plan, use `EXPLAIN` directly against the SketchLog SQL endpoint.

---

## API request generation

```bash
sketchlog-query-builder api-request \
  "SELECT APPROX_QUANTILE(latency_ms,0.99) AS p99 FROM s EMIT FINAL;" \
  --server-url https://myhost:8080 \
  --namespace prod \
  --output curl
```

```bash
curl -s -X POST 'https://myhost:8080/api/v1/namespaces/prod/query' \
  -H 'Content-Type: application/json' \
  -d '{
  "sql": "SELECT APPROX_QUANTILE(latency_ms,0.99) AS p99 FROM s EMIT FINAL;",
  "timeout_ms": 30000
}'
```

Use `--output python` for a self-contained Python snippet, or `--output json`
for the raw request object.

### Auth token

Set `SKETCHLOG_TOKEN` in your environment — it is preferred over `--token`.
Tokens are never stored in saved queries or history.

---

## Example templates

```bash
sketchlog-query-builder templates
```

Five built-in templates covering the most common SketchLog patterns:

| Template | Description |
|---|---|
| p99 latency per service (5 min tumble) | DDSketch APPROX_QUANTILE per service |
| top-10 error events per minute | COUNT with ORDER BY / LIMIT |
| unique active users (HLL) | APPROX_COUNT_DISTINCT per hour |
| sketch merge across shards | SKETCH_MERGE + SKETCH_QUANTILE |
| frequency top-K items | APPROX_FREQUENCY + ORDER BY |

---

## Saved queries and history

Queries and history are stored in a local JSON file (default:
`sketchlog_queries.json`). Use `--store /path/to/file.json` to use a custom
path.

```bash
# Save
sketchlog-query-builder save p99-prod \
  "SELECT APPROX_QUANTILE(latency_ms,0.99) FROM s EMIT FINAL;" \
  --description "Production p99 latency" \
  --tags prod latency

# List with tag filter
sketchlog-query-builder list-saved --tag prod

# Show last 10 history entries
sketchlog-query-builder history --limit 10
```

History is capped at 200 entries. The store file is written atomically
(write-then-rename) to avoid corruption.

---

## Python API

```python
from sketchlog.query_builder import (
    autocomplete,
    validate_query,
    explain_query,
    build_api_request,
    QueryStore,
    SavedQuery,
    HistoryEntry,
    QueryBuilderConfig,
)

# Autocomplete
items = autocomplete("APPROX", streams=["request_latency", "error_events"])

# Validate
result = validate_query("SELECT APPROX_QUANTILE(latency_ms,0.99) FROM s EMIT FINAL;")
if result.valid:
    plan = explain_query(result.query if hasattr(result, 'query') else "...")

# Build API request
cfg = QueryBuilderConfig(server_url="https://myhost:8080", namespace="prod")
req = build_api_request(sql, cfg.server_url, cfg.namespace, cfg.resolved_token())
print(req.to_curl())

# Save / load
store = QueryStore(persist_path="my_queries.json")
store.save(SavedQuery(name="my-p99", sql="SELECT ...", tags=["prod"]))
```

---

## Security notes

- `server_url` must start with `https://` — `http://` is rejected at config construction.
- The auth token is loaded from `SKETCHLOG_TOKEN` env var and is never echoed
  in output or stored in the query store.
- Saved queries store SQL text only — no credentials.

---

## Caveats

1. **Offline explain only** — the explain plan is a heuristic; use the server
   `EXPLAIN` endpoint for the authoritative plan.
2. **Validation is syntactic** — the validator does not have schema information;
   column/stream names are not verified against a live catalog.
3. **Unknown function warnings** — custom UDFs registered server-side will
   appear as `UNKNOWN_FUNCTION` warnings; suppress with `-- noqa` comments.
4. **History is process-local** — entries are appended by the CLI process that
   ran the command; concurrent writes from multiple processes are not
   coordinated.
5. **Store file is best-effort** — if the persist path is unwritable, queries
   still work in-memory; a warning is not surfaced to avoid breaking pipelines.
6. **Streaming SQL dialect** — the validator targets SketchLog Streaming SQL;
   standard ANSI SQL not using streaming extensions may produce false warnings.
