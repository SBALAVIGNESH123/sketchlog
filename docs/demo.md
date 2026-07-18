# Hosted Demo and Interactive Playground

SketchLog ships with a zero-dependency product evaluation playground that lets
visitors explore the main workflows in any browser with no installation
required.

Open the playground:
[https://sbalavignesh123.github.io/sketchlog/demo/](https://sbalavignesh123.github.io/sketchlog/demo/)

You can also open `demo/index.html` directly in a browser. The playground is a
single-page static app and does not require a server.

## What the playground demonstrates

### Guided product tour

The tour walks through the full launch story:

1. bounded-memory telemetry;
2. Cost and footprint estimator;
3. DDSketch percentiles;
4. HyperLogLog-style cardinality;
5. Count-Min frequency tracking;
6. stream writes and reads;
7. streaming SQL examples;
8. anomaly comparison;
9. SLO and canary analysis;
10. multi-tenant namespace isolation;
11. Sketch Mesh aggregation;
12. exporter payloads;
13. storage durability proof links;
14. local Docker proof instructions;
15. GitHub and local-trial call to action.

### Cost and footprint estimator

The estimator is a browser-facing version of the real
`sketchlog-cost-estimate` CLI/API model. It helps operators compare raw-event
retention against compact SketchLog summaries before deployment.

The playground estimator supports:

- events per day;
- average raw event size;
- retention period;
- sketch accuracy;
- streams per namespace;
- namespace or tenant count;
- raw-store compression ratio;
- storage backend profile: memory, PostgreSQL, or OmniKV.

It returns:

- raw uncompressed telemetry size;
- compressed raw-store baseline;
- compact SketchLog summary footprint;
- backend-adjusted footprint;
- hot-memory estimate;
- savings versus compressed raw telemetry;
- caveats and model details.

The same feature is also available from the Python package:

```bash
sketchlog-cost-estimate \
  --events-per-day 1000000 \
  --avg-event-bytes 512 \
  --retention-days 30 \
  --sketch-accuracy 0.01 \
  --streams 50 \
  --namespaces 5 \
  --raw-compression-ratio 4 \
  --storage-backend omnikv
```

See the
[cost and footprint estimator guide](cost-estimate.md)
for the full model, JSON output, and validation commands.

### Dashboard mode

The dashboard uses deterministic synthetic telemetry to show:

- p50, p95, and p99 latency cards;
- unique-user and session cardinality concepts;
- top event/error frequency concepts;
- anomaly, SLO burn, and canary risk summaries;
- namespace selection;
- Sketch Mesh node status cards;
- latency distribution charts.

### DDSketch quantile estimation

- Add arbitrary positive values such as latencies, request sizes, and durations.
- See p50, p95, and p99 estimates update in real time.
- Adjust the relative accuracy parameter and see the accuracy guarantee change.
- Inspect the visual bucket distribution chart.

### Stream operations

- Simulate writing JSON records to any stream path.
- Read the records back and inspect the log output.
- Demonstrate the core SketchLog stream abstraction without running a backend.

### Streaming SQL playground

The SQL panel provides copyable examples for:

- service percentile summaries;
- tenant isolation;
- top error/event frequency;
- stable versus canary comparison.

The SQL results are browser-side deterministic fixtures. They are useful for
understanding the product workflow, not as a replacement for the local proof
commands.

### Export payload preview

- Loki: preview the JSON body sent to `/loki/api/v1/push`.
- Datadog: preview the Metrics API v2 `series` payload.
- New Relic: preview the Insights Events API payload for US and EU regions.

No exporter request is sent from the browser.

### Real proof section

The playground links browser simulation to reproducible local evidence:

- Docker demo smoke verification;
- PostgreSQL durability proof stack;
- unified storage proof for memory, PostgreSQL, and OmniKV;
- realistic telemetry load proof.

## Browser demo mode versus local proof mode

The hosted playground is static because it runs on GitHub Pages.

| Mode | Where it runs | Purpose |
| --- | --- | --- |
| Browser demo mode | In the page with local JavaScript | Safe product tour, dashboard, sketches, streams, SQL examples, and exporter payload previews |
| Local proof mode | On your machine with Docker/Python | Real service verification, PostgreSQL durability proof, and storage proof commands |
| Future live backend mode | Optional future hosted API | Could provide a real shared backend later with authentication, rate limiting, and tenant isolation |

The page should not be interpreted as a hosted production SketchLog service.

## Running locally

The playground is pure HTML, CSS, and JavaScript.

```bash
python -m http.server 8080 --directory demo
```

Then open <http://localhost:8080>.

You can also open `demo/index.html` directly from the filesystem.

## Reproducible proof commands

Run the full Docker demo:

```bash
docker compose -f demo/compose.yml up --build --wait
python demo/smoke.py
docker compose -f demo/compose.yml down -v
```

Run the PostgreSQL durability proof:

```bash
python scripts/postgres_durability_proof.py --start --stop
```

Run unified storage proofs:

```bash
python scripts/storage_proof.py --backend memory
python scripts/storage_proof.py --backend postgres --postgres-start --postgres-stop
python scripts/storage_proof.py --backend omnikv
```

Run the realistic telemetry load proof:

```bash
python scripts/telemetry_load_proof.py --backend omnikv
```

## Deploying to GitHub Pages

The repository Pages workflow copies the static website, documentation, and
playground into the public GitHub Pages artifact.

The playground must keep working under:

```text
https://sbalavignesh123.github.io/sketchlog/demo/
```

## Architecture

| File | Role |
| --- | --- |
| `demo/index.html` | Single-page app with hero, guided tour, dashboard, sketches, streams, SQL, exporters, proof section, snippets, and CTA |
| `demo/assets/demo.css` | Dark responsive design system for screenshot-ready launch visuals |
| `demo/assets/demo.js` | DDSketch implementation, deterministic telemetry fixtures, stream simulation, SQL examples, exporter preview, snippets, and UI behavior |
| `demo/assets/estimator.js` | Browser implementation of the cost and footprint estimator aligned with the Python CLI/API model |

The playground has zero external dependencies, zero JavaScript frameworks, zero
build step, and works offline.

## DDSketch in the browser

The playground includes a browser implementation of DDSketch:

```javascript
const sketch = new DDSketch(0.01); // 1% relative accuracy
sketch.add(42.5);
sketch.add(150.0);
const p95 = sketch.quantile(0.95); // guaranteed +/-1% relative error
```

This demonstrates the same accuracy model used by the Python `sketchlog`
package.

## Security

- No external CDN dependencies.
- No frontend credentials.
- No cookies or tracking.
- No `eval()` or dynamic code execution.
- No outbound telemetry from the playground page.
