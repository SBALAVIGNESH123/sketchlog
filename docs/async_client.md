# Async Python Client SDK

Production-grade async Python client for SketchLog, built on `httpx`.

## Installation

```bash
pip install sketchlog
```

## Quick Start

```python
import asyncio
from sketchlog.async_client import AsyncClientConfig, AsyncSketchLogClient

cfg = AsyncClientConfig(base_url="http://localhost:7700", token="your-token")

async def main() -> None:
    async with AsyncSketchLogClient(cfg) as client:
        await client.ingest("prod", "latency_ms", [{"value": 42.0}])
        summary = await client.query_summary("prod", "latency_ms")
        print(summary)

asyncio.run(main())
```

## Configuration

| Field | Default | Description |
|---|---|---|
| `base_url` | required | Server URL |
| `token` | required | Auth token |
| `timeout` | 30.0 | Per-request timeout (seconds) |
| `max_retries` | 3 | Retries on 5xx / timeout |
| `backoff_base` | 0.5 | Exponential backoff base |
| `backoff_cap` | 30.0 | Maximum backoff (seconds) |
| `max_connections` | 100 | Connection pool size |
| `verify_ssl` | True | SSL verification |

## Streaming

```python
cfg = AsyncClientConfig(base_url="http://localhost:7700", token="your-token")
async with AsyncSketchLogClient(cfg) as client:
    async with client.subscribe_stream("prod", "latency_ms", interval_seconds=1.0) as events:
        async for summary in events:
            print(f"p99={summary['p99']:.1f}ms")
```

## Error Handling

```python
from sketchlog.async_client import (
    SketchLogAuthError,
    SketchLogRateLimitError,
    SketchLogServerError,
    SketchLogTimeoutError,
)

try:
    await client.ingest("prod", "latency_ms", [{"value": 1.0}])
except SketchLogAuthError:
    print("Invalid token")
except SketchLogRateLimitError as e:
    print(f"Rate limited — retry after {e.retry_after}s")
except SketchLogTimeoutError:
    print("Request timed out")
except SketchLogServerError:
    print("Server error")
```
