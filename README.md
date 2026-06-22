# SketchLog

![Status](https://img.shields.io/badge/status-beta-orange)
![Python Version](https://img.shields.io/badge/python-3.9%2B-blue)
![License](https://img.shields.io/badge/license-MIT-green)

**Streaming metrics compression engine. 100M events in 93 KB with bounded error.**

SketchLog allows you to ingest high-throughput event streams and extract accurate
percentiles and cardinalities in constant memory. It combines **DDSketch** for latencies,
**HyperLogLog** for unique items, and **Count-Min Sketch** for event frequencies.

Instead of storing arrays of events or exporting raw telemetry, SketchLog compresses
the statistical shape of your data in real time, making it ideal for continuous
monitoring, edge devices, and memory-constrained environments.

---

## 📚 Documentation

The full documentation is available at [SketchLog Documentation Site](https://sbalavignesh123.github.io/sketchlog/) (or via `/docs` in the repository).
It includes:
- **Architecture**: Details on distributed merges, drift detection, and memory footprint.
- **Benchmarks**: Memory footprint scaling and CPU throughput limits.
- **Guarantees**: Formal mathematical proofs of error bounds.
- **Integrations**: How to integrate with Prometheus, FastAPI, and OpenTelemetry.
- **API Reference**: Comprehensive listing of classes and methods.
- **Contributing**: Guidelines for contributing to SketchLog.

### Local Documentation Build
To build and preview the documentation locally:
```bash
pip install mkdocs-material
mkdocs serve
```

---

## Installation

### 1. The Core Library (Python)
If you just want to use the high-performance sketching data structures directly in Python:
```bash
pip install sketchlog
```

### 2. The Standalone Server
To run SketchLog as an independent network service (like Redis or Prometheus):
```bash
pip install "sketchlog[server]"
uvicorn sketchlog.server:app --port 8080
```

---

## Quickstart

### 🐍 Python (Embedded Library)
```python
from sketchlog import StreamLog

log = StreamLog()
log.add_latency(42.5)
log.add_batch([15.0, 88.2, 42.1])
log.add_unique("user_12345")
log.add_event("cache_miss", count=5)

print(f"p99 Latency:  {log.p99():.2f}ms")
```

### 🟨 TypeScript / Node.js SDK
Connect to the standalone server via HTTP:
```bash
npm install @sketchlog/client
```
```typescript
import { SketchLogClient } from '@sketchlog/client';

const client = new SketchLogClient({ endpoint: 'http://localhost:8080' });

// Non-blocking, buffered ingest
await client.ingestEvents('production_api', {
  latencies: [42.5, 15.0, 88.2, 42.1],
  uniques: ["user_12345"],
  events: { "cache_miss": 5 }
});
```

### 🐹 Go SDK
```bash
go get github.com/SBALAVIGNESH123/sketchlog-go
```
```go
import "github.com/SBALAVIGNESH123/sketchlog-go"

client := sketchlog.NewClient(sketchlog.ClientOptions{
    Endpoint: "http://localhost:8080",
})

batch := sketchlog.EventBatch{
    Latencies: []float64{42.5, 15.0, 88.2, 42.1},
    Uniques:   []string{"user_12345"},
    Events:    map[string]int64{"cache_miss": 5},
}

err := client.IngestEvents(ctx, "production_api", batch)
```

## Distributed Clustering (Beta)

SketchLog supports multi-node clustering without requiring external coordination services like Redis.

You can run a cluster using the following environment variables:
```bash
SKETCHLOG_NODE_ID="node-1"
SKETCHLOG_PEERS="http://node2:8000,http://node3:8000"
SKETCHLOG_CLUSTER_SECRET="your-secret-token"
uvicorn sketchlog.server:app --port 8000
```

> **Performance Trade-off**: Enabling clustering forces `deterministic=True` for all streams, bypassing the C++ high-performance path and using the pure Python backend. This is necessary because C++ data structures currently do not support serialization and snapshot extraction. Be aware that enabling clustering incurs a significant (~46x) performance penalty for metrics ingestion on the server.

## Community

Join us in Slack! [Join SketchLog Slack](https://join.slack.com/t/sketchlog/shared_invite/zt-41kc03dnl-tiyHm4Gr2CbaJWuGHxdbiQ)

---

## What this is not

SketchLog is a streaming metrics compression layer. It is deliberately not:

- **Not a tracing system.** No request paths, no correlation IDs, no causal chains.
- **Not a time-series database.** No historical drill-down, no label indexing.
- **Not an observability platform.** No raw log storage, no ad-hoc queries.
- **Not exact.** All results are probabilistic with bounded error. If you need exact percentiles, use numpy.

---

MIT License

