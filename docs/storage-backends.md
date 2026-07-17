# Storage backends and proof evidence

SketchLog stores compact stream summaries, not raw telemetry rows. A durable
backend is only needed when you want those summaries, mesh tombstones, and
checkpointed stream state to survive process restarts.

Use this page to choose a backend and reproduce the storage proof locally.

## Backend positioning

| Backend | Best for | Persistence | Notes |
| --- | --- | --- | --- |
| In-memory | Local demos, tests, notebooks, quick SDK experiments | No | Fastest way to try SketchLog. State intentionally disappears on restart. |
| PostgreSQL / SQLAlchemy | Server deployments and teams already operating SQL databases | Yes | Recommended default durable path for production server deployments. Uses SQLAlchemy async URLs such as `postgresql+asyncpg://...`. |
| OmniKV embedded | Local-first, edge, single-node, or embedded deployments that want durable state without a separate SQL service | Yes | Optional native bridge. Useful when SketchLog should keep compact state beside the application or node. |

SketchLog does not store every request, log line, or trace span in these
backends. It stores bounded sketch state: DDSketch latency distributions,
HyperLogLog-style cardinality, Count-Min Sketch counters, stream metadata, and
mesh deletion tombstones.

## Quick decision guide

- Use **in-memory** when you are evaluating APIs, writing unit tests, or running
  the hosted/demo experience.
- Use **PostgreSQL** when you are running a shared SketchLog server and already
  have database operations, backups, monitoring, and access controls.
- Use **OmniKV** when you want embedded/local durability for compact stream
  summaries and do not want to introduce a separate SQL database for that node.

## Configure in-memory mode

In-memory mode is the default when no durable backend is configured:

```bash
sketchlog-server --host 127.0.0.1 --port 8000
```

You can also make it explicit:

```bash
export SKETCHLOG_STORAGE_BACKEND=memory
sketchlog-server --host 127.0.0.1 --port 8000
```

Expected behavior: writes and queries work while the process is alive, but
restart returns an empty registry.

## Configure PostgreSQL storage

Install the server extras:

```bash
python -m pip install "sketchlog[server]"
```

Set a SQLAlchemy async database URL:

```bash
export SKETCHLOG_DB_URI="postgresql+asyncpg://sketchlog:change-me@localhost:5432/sketchlog"
sketchlog-server --host 0.0.0.0 --port 8000
```

For the checked-in Docker Compose proof stack:

```bash
python scripts/storage_proof.py --backend postgres --postgres-start --postgres-stop
```

Expected final line:

```text
PASS SketchLog storage proof
```

The PostgreSQL proof writes telemetry through the HTTP API, restarts the
SketchLog server, verifies restored metrics, deletes the stream, checks durable
mesh tombstones, restarts again, and verifies that deleted state does not
resurrect.

For database operations guidance, see
[Production Database Hardening](db-hardening.md).

## Configure OmniKV storage

Install SketchLog and the optional OmniKV Python/native bridge into the same
environment:

```bash
python -m pip install sketchlog
python -m pip install "git+https://github.com/SBALAVIGNESH123/OmniKV.git#subdirectory=bindings/python"
```

Configure the embedded backend:

```bash
export SKETCHLOG_STORAGE_BACKEND=omnikv
export SKETCHLOG_OMNIKV_DATA_DIR=/var/lib/sketchlog/omnikv
export SKETCHLOG_OMNIKV_NAMESPACE=sketchlog
export SKETCHLOG_OMNIKV_MODULE=omnikv

sketchlog-server --host 0.0.0.0 --port 8000
```

Run the proof:

```bash
python scripts/storage_proof.py --backend omnikv
```

Expected final line:

```text
PASS SketchLog storage proof
```

The OmniKV proof uses the installed native bridge, writes stream state, reopens
the embedded database, verifies metrics after restart, deletes the stream with a
mesh tombstone, reopens again, and verifies that deleted state does not
resurrect.

For bridge details and operational notes, see
[OmniKV Storage Backend](omnikv-storage.md).

## Run one proof command across backends

Use the unified proof runner for release checks, local validation, screenshots,
and launch videos:

```bash
python scripts/storage_proof.py --backend memory
python scripts/storage_proof.py --backend postgres --postgres-start --postgres-stop
python scripts/storage_proof.py --backend omnikv
```

If optional dependencies are not available on a development machine, record
skips instead of failing:

```bash
python scripts/storage_proof.py --backend all --allow-missing-optional
```

## Realistic telemetry load proof

For a larger proof using deterministic production-shaped API telemetry, run:

```bash
python scripts/telemetry_load_proof.py --backend memory
python scripts/telemetry_load_proof.py --backend postgres --postgres-start --postgres-stop
python scripts/telemetry_load_proof.py --backend omnikv
```

Expected final line:

```text
PASS SketchLog telemetry load proof
```

The load proof generates JSONL-style events with timestamps, services, routes,
HTTP methods, statuses, users, regions, tenants, labels, and heavy-tailed
latencies. It then verifies:

- p50, p95, and p99 through Streaming SQL;
- HyperLogLog-style unique-user cardinality;
- top Count-Min Sketch event counters;
- compact sketch memory versus raw JSONL bytes;
- durable restart recovery for PostgreSQL and OmniKV.

## Example evidence from the deterministic fixture

These numbers come from the checked-in deterministic load proof on one local
run. They are reproducible evidence for this implementation, not a universal
performance guarantee for every workload or machine.

| Measurement | Observed value |
| --- | --- |
| Raw telemetry fixture | 2,500 API-like events |
| Raw JSONL size | 935,441 bytes |
| Compact sketch state | 90,176 bytes |
| Raw-to-compact ratio | 10.374x |
| Exact p95 | 241.748 ms |
| SketchLog p95 | 242.289 ms |
| Exact p99 | 414.012 ms |
| SketchLog p99 | 415.778 ms |
| Exact unique users | 815 |
| SketchLog unique estimate | 800 |

The important production property is bounded-memory behavior: as the raw event
stream grows, SketchLog keeps compact summaries for operational questions
instead of retaining every raw event.

## What this does not prove

- It does not prove SketchLog replaces logs, traces, APMs, or databases.
- It does not make probabilistic sketches exact.
- It does not guarantee the same throughput on every machine.
- It does not remove the need for backups, TLS, authentication, and operational
  monitoring when using durable storage.

The proof is designed to be honest and reproducible: it shows storage,
recovery, deletion, tombstone, percentile, cardinality, frequency, and memory
behavior with a deterministic workload.
