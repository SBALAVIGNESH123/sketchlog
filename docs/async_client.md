# Async Client SDK

`AsyncSketchLogClient` is SketchLog's production-grade async Python client.

## Installation

```bash
pip install 'sketchlog[async]'
```

## Quick start

```python
import asyncio
from sketchlog.async_client import AsyncSketchLogClient, SketchLogRateLimitError

async def main():
    async with AsyncSketchLogClient("http://localhost:7700", token="my-token") as c:
        # Ingest telemetry
        await c.ingest("production", "latency_ms", [12.0, 34.0, 56.0, 99.0])

        # Query percentiles
        p99 = await c.query_percentile("production", "latency_ms", 0.99)
        print(f"p99 latency: {p99:.1f}ms")

        # Check health
        health = await c.health()
        print(f"Server: {health['status']}")

asyncio.run(main())
```

## Configuration

```python
from sketchlog.async_client import AsyncSketchLogClient, AsyncClientConfig

config = AsyncClientConfig(
    base_url="https://sketchlog.example.com",
    token="prod-token",
    timeout_seconds=30.0,
    max_connections=100,
    max_retries=3,
    retry_backoff_base=0.5,
    retry_backoff_max=30.0,
    retry_jitter=True,
    verify_ssl=True,
)

async with AsyncSketchLogClient(config=config) as c:
    ...
```

## API reference

### Ingest

| Method | Description |
|---|---|
| `ingest(ns, stream, values)` | Ingest a batch of numeric telemetry values |
| `ingest_event(ns, stream, event)` | Record a discrete event (for frequency/cardinality) |

### Query

| Method | Description |
|---|---|
| `query_percentile(ns, stream, q)` | DDSketch percentile (0.0–1.0) |
| `query_count(ns, stream)` | Total event count |
| `query_cardinality(ns, stream)` | HyperLogLog unique cardinality estimate |
| `query_frequency(ns, stream, event)` | Count-Min Sketch frequency estimate |
| `query_summary(ns, stream)` | Full summary (p50, p95, p99, count, cardinality) |

### Namespaces & Streams

| Method | Description |
|---|---|
| `list_namespaces()` | List all namespaces |
| `list_streams(ns)` | List all streams in a namespace |
| `delete_stream(ns, stream)` | Delete a stream and all its data |

### Health

| Method | Description |
|---|---|
| `health()` | Server health check |
| `info()` | Server version and build info |

### Streaming subscription

```python
from sketchlog.async_client import AsyncSketchLogClient, SketchLogRateLimitError

async with AsyncSketchLogClient("http://localhost:7700", token="my-token") as client:
    async with client.subscribe_stream("prod", "latency_ms", interval_seconds=1.0) as events:
        async for summary in events:
            print(f"p99={summary['p99']:.1f}ms  count={summary['count']}")
```

## Error handling

```python
from sketchlog.async_client import (
    SketchLogAuthError,
    SketchLogRateLimitError,
    SketchLogServerError,
    SketchLogTimeoutError,
    SketchLogError,
)

try:
    await client.ingest("prod", "latency_ms", [1.0])
except SketchLogAuthError:
    print("Invalid token")
except SketchLogRateLimitError as e:
    print(f"Rate limited — retry after {e.retry_after_seconds}s")
except SketchLogServerError:
    print("Server error — check server logs")
except SketchLogTimeoutError:
    print("Request timed out")
except SketchLogError as e:
    print(f"Client error: {e}")
```

## Caveats

- All state is in-memory — client restart resets connection pool.
- `subscribe_stream` polls at `interval_seconds` intervals — not a true push subscription.
- Retry is not applied to `SketchLogAuthError` or `SketchLogRateLimitError`.
