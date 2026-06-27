# Streaming SQL

SketchLog supports a real-time **Streaming SQL** interface, allowing you to query live sketches using familiar syntax. Rather than pulling raw telemetry into a data warehouse, you query the pre-aggregated O(1) structures directly at the edge or on the central server.

## Supported Queries

You can execute queries against the `sketchlog` namespaces:

```sql
-- Get the 99th percentile latency for a specific service
SELECT p99(latency) FROM "default/my-service";

-- Get the exact number of unique users seen today
SELECT count_unique(users) FROM "default/auth-service";

-- Get the estimated frequency of a specific error code
SELECT event_count(errors, 'HTTP_500') FROM "default/web-api";
```

## How It Works

When a SQL query is received, the SQL Engine:
1. Parses the query into an AST.
2. Identifies the namespace and stream.
3. Maps aggregate functions (`p99`, `count_unique`) directly to the underlying `DDSketch` or `HyperLogLog` operations in O(1) time.
4. Returns the instantaneous approximated result.

## Usage (HTTP API)

The `/v1/query` endpoint accepts raw SQL POST requests:

```bash
curl -X POST "http://localhost:8000/v1/query" \
  -H "Content-Type: application/json" \
  -d '{"query": "SELECT p99(latency) FROM default/payment-gateway"}'
```

Response:
```json
{
  "query": "SELECT p99(latency) FROM default/payment-gateway",
  "results": [
    {
      "stream": "default/payment-gateway",
      "metric": "p99(latency)",
      "value": 142.3
    }
  ],
  "execution_time_ms": 0.12
}
```
