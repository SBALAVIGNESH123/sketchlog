<p align="center">
  <img src="website-standalone/logo.png" alt="SketchLog" width="80" />
</p>

<h1 align="center">SketchLog</h1>

<p align="center">
  <strong>Bounded-memory telemetry sketches for streaming metrics, live analytics, and production operations.</strong>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/status-beta-orange" alt="Status" />
  <img src="https://img.shields.io/badge/python-3.10%2B-blue" alt="Python Version" />
  <img src="https://img.shields.io/badge/license-MIT-green" alt="License" />
  <a href="https://github.com/SBALAVIGNESH123/sketchlog/actions">
    <img src="https://img.shields.io/github/actions/workflow/status/SBALAVIGNESH123/sketchlog/ci.yml?label=CI" alt="CI" />
  </a>
</p>

SketchLog compresses high-throughput telemetry streams into bounded-memory,
mergeable statistical summaries. It combines DDSketch for latency percentiles,
HyperLogLog for cardinality, and Count-Min Sketch for event frequencies, then
adds production surfaces for APIs, dashboards, alerts, exporters, Kubernetes,
and multi-language SDKs.

The goal is simple: keep the operational signal without retaining every raw
event.

## Why SketchLog

- Bounded memory per stream instead of unbounded raw-event storage.
- Explicit approximation guarantees for percentiles, cardinality, and frequency.
- Mergeable sketch state for distributed systems and edge deployments.
- Live query, dashboard, SLO, anomaly, and export workflows built on the same
  compact telemetry model.
- Production-minded release engineering with CI, coverage, security scanning,
  docs, Helm packaging, container checks, and public demo verification.

SketchLog is currently a production-minded open-source beta. The core data
structures and release pipeline are heavily tested, while some higher-level
operational features continue to mature.

## Live demo

Open the hosted playground:
[https://sbalavignesh123.github.io/sketchlog/demo/](https://sbalavignesh123.github.io/sketchlog/demo/)

Run the deterministic product demo and its end-to-end verifier:

```bash
docker compose -f demo/compose.yml up --build --wait
```

Open <http://localhost:4173>.

The demo includes live ingestion, percentile sketches, cardinality metrics,
Streaming SQL examples, anomaly evidence, tenant isolation, exporter examples,
and operational readiness checks. See the [demo runbook](demo/README.md) for
ports, cleanup, verification, and recording guidance.

## Core capabilities

| Area | Capabilities |
| --- | --- |
| Sketch engine | DDSketch, HyperLogLog, Count-Min Sketch, bounded sparse stores, merge contracts |
| Server | FastAPI service, HTTP ingestion, WebSocket updates, health/readiness, OpenAPI contract |
| Streaming analytics | Streaming SQL, query builder, sketch diffing, anomaly comparison, Smart SLO engine |
| Multi-tenancy | Namespace quotas, namespace-scoped auth, RBAC checks, tenant-safe metrics |
| Distributed mode | Authenticated Sketch Mesh, versioned tombstones, peer allowlists, convergence tests |
| Dashboards | React dashboard SDK, standalone demo dashboard, Grafana dashboard, Grafana datasource plugin |
| Integrations | Prometheus, OpenTelemetry, OpenTelemetry Collector component, Loki, Datadog, New Relic |
| Clients | Python embedded API, Python async client, TypeScript client, Go HTTP client, native Go sketches |
| Deployment | Docker image, Docker Compose demo, Helm chart, Kubernetes Operator manifests |
| Operations | Doctor checks, alert manager, rate limiting, TLS/mTLS helpers, DB hardening, benchmark lab |
| Runtime targets | Python, C++, TypeScript, Go, WebAssembly, Linux eBPF collector |

## Architecture

```mermaid
flowchart LR
    subgraph Producers
        Python[Python SDK]
        TypeScript[TypeScript SDK]
        Go[Go SDK]
        OTel[OpenTelemetry]
        EBPF[Linux eBPF]
    end

    Python --> API[SketchLog API]
    TypeScript --> API
    Go --> API
    OTel --> API
    EBPF --> API

    API --> Registry[Namespace registry]
    Registry --> Stream[Bounded StreamLog]
    Stream --> DDS[DDSketch]
    Stream --> HLL[HyperLogLog]
    Stream --> CMS[Count-Min Sketch]
    Stream --> Analytics[SQL, SLO, diff, anomaly]
    Stream <--> Mesh[Authenticated Sketch Mesh]
    API --> Dashboard[Live dashboards]
    API --> Exporters[Prometheus, OTel, Loki, Datadog, New Relic]
```

Each stream stores compact sketch state rather than raw telemetry. See the
[architecture guide](https://sbalavignesh123.github.io/sketchlog/docs/architecture/)
for merge behavior, windowing, drift detection, memory limits, and runtime
details.

## Installation

Install the embedded Python library:

```bash
pip install sketchlog
```

Install the standalone server:

```bash
pip install "sketchlog[server]"
sketchlog-server --host 127.0.0.1 --port 8000
```

Install the TypeScript client:

```bash
npm install @sketchlog/client
```

Install the Go client:

```bash
go get github.com/SBALAVIGNESH123/sketchlog/clients/go@v1.2.4
```

## Quickstart

### Python embedded sketch

```python
from sketchlog import StreamLog

log = StreamLog()
log.add_latency(42.5)
log.add_batch([15.0, 88.2, 42.1])
log.add_unique("user_12345")
log.add_event("cache_miss", count=5)

print(f"p99 latency: {log.p99():.2f} ms")
```

### Python async client

```python
from sketchlog.async_client import AsyncSketchLogClient

async with AsyncSketchLogClient("http://localhost:8000") as client:
    await client.ingest_events(
        namespace="production",
        stream="api.latency",
        latencies=[42.5, 15.0, 88.2],
        uniques=["user_12345"],
        events={"cache_miss": 5},
    )
```

### TypeScript client

```typescript
import { SketchLogClient } from '@sketchlog/client';

const client = new SketchLogClient({ endpoint: 'http://localhost:8000' });

await client.ingestEvents('production_api', {
  latencies: [42.5, 15.0, 88.2, 42.1],
  uniques: ['user_12345'],
  events: { cache_miss: 5 },
});
```

### Go client

```go
import "github.com/SBALAVIGNESH123/sketchlog/clients/go"

client := sketchlog.NewClient(sketchlog.ClientOptions{
    Endpoint: "http://localhost:8000",
})

batch := sketchlog.EventBatch{
    Latencies: []float64{42.5, 15.0, 88.2, 42.1},
    Uniques:   []string{"user_12345"},
    Events:    map[string]int64{"cache_miss": 5},
}

err := client.IngestEvents(ctx, "production_api", batch)
```

## Deployment

Run the server with Docker:

```bash
docker run --rm -p 8000:8000 ghcr.io/sbalavignesh123/sketchlog:1.2.4
```

Install with Helm:

```bash
helm upgrade --install sketchlog oci://ghcr.io/sbalavignesh123/charts/sketchlog \
  --version 1.2.4
```

Run mesh mode on Kubernetes:

```bash
helm upgrade --install sketchlog ./charts/sketchlog \
  --set replicaCount=3 \
  --set mesh.enabled=true \
  --set-string mesh.clusterSecret="$CLUSTER_SECRET"
```

SketchLog also includes Kubernetes Operator manifests and documentation for
declarative cluster management.

### Optional OmniKV-backed storage

SketchLog can persist compact stream checkpoints and mesh tombstones into an
OmniKV embedded database when the OmniKV Python/native bridge is installed:

```bash
export SKETCHLOG_STORAGE_BACKEND=omnikv
export SKETCHLOG_OMNIKV_DATA_DIR=/var/lib/sketchlog/omnikv
export SKETCHLOG_OMNIKV_NAMESPACE=sketchlog

sketchlog-server --host 0.0.0.0 --port 8000
```

This is opt-in. Existing in-memory and SQLAlchemy storage paths remain
unchanged. See the
[OmniKV storage backend guide](https://sbalavignesh123.github.io/sketchlog/docs/omnikv-storage/)
for the bridge contract and operational notes.

## Documentation

The full documentation is published at
[sbalavignesh123.github.io/sketchlog/docs](https://sbalavignesh123.github.io/sketchlog/docs/).

Important sections:

- [Architecture](https://sbalavignesh123.github.io/sketchlog/docs/architecture/)
- [Benchmarks](https://sbalavignesh123.github.io/sketchlog/docs/benchmarks/)
- [Formal guarantees](https://sbalavignesh123.github.io/sketchlog/docs/guarantees/)
- [Client SDKs](https://sbalavignesh123.github.io/sketchlog/docs/sdks/)
- [Async Python client](https://sbalavignesh123.github.io/sketchlog/docs/async_client/)
- [OmniKV storage backend](https://sbalavignesh123.github.io/sketchlog/docs/omnikv-storage/)
- [Export integrations](https://sbalavignesh123.github.io/sketchlog/docs/exporters/)
- [Kubernetes Operator](https://sbalavignesh123.github.io/sketchlog/docs/kubernetes-operator/)
- [RBAC](https://sbalavignesh123.github.io/sketchlog/docs/rbac/)
- [Runbooks](https://sbalavignesh123.github.io/sketchlog/docs/runbooks/)
- [Threat model](https://sbalavignesh123.github.io/sketchlog/docs/threat_model/)

Build docs locally:

```bash
pip install ".[docs]"
mkdocs serve
```

## Development

Install the development environment:

```bash
make dev-install
```

Run checks:

```bash
make test
make test-go
make test-ts
make docs
```

Run the full demo:

```bash
make demo
```

## What SketchLog is not

SketchLog is a streaming metrics compression layer. It is deliberately not:

- A tracing system. It does not store request paths, spans, correlation IDs, or causal chains.
- A time-series database. It does not provide raw historical drill-down or full label indexing.
- A raw log storage system. It keeps compact summaries, not every event payload.
- An exact analytics engine. Results are probabilistic with documented error bounds.

## Community

- GitHub: <https://github.com/SBALAVIGNESH123/sketchlog>
- Documentation: <https://sbalavignesh123.github.io/sketchlog/docs/>
- Slack: <https://join.slack.com/t/sketchlog/shared_invite/zt-41kc03dnl-tiyHm4Gr2CbaJWuGHxdbiQ>

## License

SketchLog is released under the MIT License.
