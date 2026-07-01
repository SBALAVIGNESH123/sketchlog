# SketchLog Launch Demo

This deterministic demo proves the advertised SketchLog paths with real data:

- HTTP ingestion and live WebSocket state
- DDSketch percentiles and bounded memory
- HyperLogLog cardinality
- Count-Min Sketch event counts
- streaming SQL
- baseline anomaly detection
- isolated tenant namespaces
- Prometheus export

## Start

Requirements: Docker Desktop with at least 4 GB of memory available.

From the repository root:

```bash
docker compose -f demo/compose.yml up --build --wait
```

When every service reports `healthy`, open:

<http://localhost:4173>

The `verifier` service checks the dashboard, REST API, Prometheus output,
streaming SQL, anomaly result, namespace isolation, and a real WebSocket frame.
`docker compose --wait` fails if any of those paths fail.

To use another local port:

```powershell
$env:SKETCHLOG_DEMO_PORT = "4180"
docker compose -f demo/compose.yml up --build --wait
```

## Stop

```bash
docker compose -f demo/compose.yml down --remove-orphans
```

The demo does not use persistent volumes, credentials, or external telemetry.
Restarting it creates the same baseline and initial live distribution.

## Recording sequence

1. Wait until the top-right badge reads **SYSTEM LIVE**.
2. Show P50, P99, unique users, event count, and sketch memory.
3. Pause on the latency CDF and live heatmap.
4. Point out that event count rises while the sketch remains compact.
5. Show the anomaly score against the healthy baseline.
6. Show the real streaming SQL result and its execution time.
7. Finish with the two isolated `checkout` streams under different namespaces.

Use a 1920×1080 browser window at 100% zoom. Record 60–90 seconds and avoid
showing terminals, account pages, tokens, browser bookmarks, or personal tabs.
