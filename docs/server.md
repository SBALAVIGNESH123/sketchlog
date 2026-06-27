# Standalone Server

SketchLog can be run as a standalone network service. This allows non-Python applications (e.g., Go, Node.js, Rust, Java) to stream observations into SketchLog via a simple HTTP API.

## Design Constraints
- **In-Memory Volatility**: The server is explicitly an ephemeral aggregation node. Metrics are kept entirely in memory for extreme performance. If the server crashes or restarts, state is lost.
- **Edge Aggregation**: For distributed systems, deploy a SketchLog server as a sidecar or local edge aggregator for each cluster or availability zone.
- **TLS and Authentication**: The server runs unencrypted HTTP. Do NOT expose it directly to the public internet. Use a reverse proxy (like Envoy, Nginx, or an API Gateway) to handle TLS termination and token authentication.

## Installation and Startup

To run the server, install SketchLog with the `server` extra:

```bash
pip install "sketchlog[server]"
```

Start the server using the module entrypoint:

```bash
python -m sketchlog.server
```

By default, the server listens on `0.0.0.0:8000`.
You can customize its behavior using environment variables:

| Variable | Default | Description |
|---|---|---|
| `SKETCHLOG_HOST` | `0.0.0.0` | IP address to bind to |
| `SKETCHLOG_PORT` | `8000` | Port to bind to |
| `SKETCHLOG_MAX_STREAMS` | `1000` | Maximum number of independent streams to track (must be >= 1). Uses LRU eviction. |
| `SKETCHLOG_MAX_BATCH_SIZE` | `10000` | Maximum number of data points (latencies + uniques + events) per ingestion request. |
| `SKETCHLOG_MAX_REQUEST_BYTES`| `1048576`| Maximum size of the request body in bytes (default 1MB). Upstream proxies may also enforce bounds. |

## Deployment (Docker & Kubernetes)

SketchLog comes with a production-ready `Dockerfile` and a Kubernetes Helm chart.

### Docker

You can build and run the standalone server using Docker:

```bash
# Build the image
docker build -t sketchlog:latest .

# Run the container
docker run -p 8000:8000 -e SKETCHLOG_DB_URI="sqlite+aiosqlite:///sketchlog.db" sketchlog:latest
```

### Kubernetes (Helm)

We provide a fully featured Helm chart in the `charts/sketchlog` directory. It supports setting up the Deployment, Service, and the Sketch Mesh distributed clustering out of the box.

```bash
# Install the Helm chart
helm install sketchlog-cluster ./charts/sketchlog --namespace observability --create-namespace

# Scale up to enable Sketch Mesh P2P federation
helm upgrade sketchlog-cluster ./charts/sketchlog --namespace observability --set replicaCount=3
```

## HTTP API Reference

The server uses a **Bounded Stream Registry**, meaning you can push metrics to arbitrarily named streams (e.g. `prod-api-latency`, `staging-db-latency`). The server will automatically create new streams as it receives them.

An interactive OpenAPI (Swagger) interface is available by visiting `http://localhost:8000/docs` while the server is running.

### 1. Ingest Events
`POST /v1/streams/{stream_id}/events`

Ingest a batch of telemetry.

**Request Payload:**
```json
{
  "latencies": [10.5, 20.1, 105.0],
  "uniques": ["user_123", "user_456"],
  "events": {
    "cache_miss": 5,
    "db_query": 1
  }
}
```

*Response:* `202 Accepted`

### 2. Query Metrics
`GET /v1/streams/{stream_id}/metrics`

Retrieve the aggregated metrics for a stream.

*Response:* `200 OK`
```json
{
  "stream_id": "prod-api-latency",
  "p50": 10.5,
  "p90": 20.1,
  "p99": 105.0,
  "p99_9": 105.0,
  "unique_count": 2,
  "total_events": 11,
  "memory_footprint_bytes": 86112
}
```

### 3. Query Specific Event Count
`GET /v1/streams/{stream_id}/events?name={event_name}`

Retrieve the count for a specific named event.

*Response:* `200 OK`
```json
{
  "stream_id": "prod-api-latency",
  "event_name": "cache_miss",
  "count": 5
}
```

### 4. Reset Stream
`DELETE /v1/streams/{stream_id}`

Delete a stream entirely, resetting its internal sketches and clearing memory.

*Response:* `204 No Content`

### 5. Kubernetes Probes
- `GET /health`: Returns `{"status": "ok"}`
- `GET /ready`: Returns `{"status": "ready"}`
