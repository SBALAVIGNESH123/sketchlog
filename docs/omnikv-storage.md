# OmniKV storage backend

SketchLog can use OmniKV as an optional durable storage backend for bounded
stream state and Sketch Mesh tombstones.

The default behavior does not change. If no durable storage backend is
configured, SketchLog continues to run with its in-memory registry. Existing
SQLAlchemy deployments continue to use `SKETCHLOG_DB_URI`.

## When to use it

Use OmniKV storage when you want SketchLog stream state to survive process
restart without introducing a separate SQL database.

SketchLog stores compact sketch checkpoints, not raw event arrays:

- stream state envelopes under `sketchlog/v1/streams/...`;
- mesh tombstones under `sketchlog/v1/tombstones/...`;
- zlib-compressed, size-bounded SketchLog state payloads;
- per-SketchLog-namespace key isolation inside the OmniKV embedded namespace.

## Configuration

Install SketchLog and the OmniKV Python/native bridge into the same Python
environment:

```bash
python -m pip install sketchlog
python -m pip install "git+https://github.com/SBALAVIGNESH123/OmniKV.git#subdirectory=bindings/python"
```

Set the backend explicitly:

```bash
export SKETCHLOG_STORAGE_BACKEND=omnikv
export SKETCHLOG_OMNIKV_DATA_DIR=/var/lib/sketchlog/omnikv
export SKETCHLOG_OMNIKV_NAMESPACE=sketchlog
export SKETCHLOG_OMNIKV_MODULE=omnikv

sketchlog-server --host 0.0.0.0 --port 8000
```

Optional settings:

| Variable | Default | Purpose |
| --- | --- | --- |
| `SKETCHLOG_STORAGE_BACKEND` | inferred | Use `omnikv`, `sqlalchemy`, `memory`, or `none`. |
| `SKETCHLOG_OMNIKV_DATA_DIR` | none | Required path for OmniKV-backed storage. |
| `SKETCHLOG_OMNIKV_NAMESPACE` | `sketchlog` | OmniKV embedded namespace for SketchLog keys. |
| `SKETCHLOG_OMNIKV_MODULE` | `omnikv` | Python/native bridge module to import. |

If `SKETCHLOG_OMNIKV_DATA_DIR` is set and no backend is selected, SketchLog
selects the OmniKV backend automatically. If `SKETCHLOG_DB_URI` is set and no
backend is selected, SketchLog selects SQLAlchemy storage.

`SKETCHLOG_DB_URI` and `SKETCHLOG_STORAGE_BACKEND=omnikv` are mutually
exclusive.

## Bridge contract

SketchLog is Python. OmniKV's embedded API is Rust. The storage backend expects
an installed Python/native bridge module exposing one of these shapes:

```python
client = omnikv.open_embedded("/var/lib/sketchlog/omnikv", namespace="sketchlog")
```

or:

```python
client = omnikv.EmbeddedOmniKv.open(
    "/var/lib/sketchlog/omnikv",
    namespace="sketchlog",
)
```

The returned client must expose:

```python
client.put(key: str, value: str)
client.get(key: str) -> str | None
client.delete(key: str)
client.scan_prefix(prefix: str, limit: int | None = None)
```

Optional methods:

```python
client.sync()
client.close()
client.stats()
```

If the bridge is missing or incomplete, SketchLog fails closed during storage
initialization instead of silently falling back to another durable backend.

## Real bridge smoke proof

After installing the OmniKV bridge, run the SketchLog smoke proof:

```bash
python scripts/omnikv_bridge_smoke.py
```

Expected final output:

```text
PASS SketchLog OmniKV bridge smoke
```

The smoke writes a real `StreamLog` through the installed `omnikv` module,
closes the backend, reopens it, verifies the stream survived, deletes the
stream with a mesh tombstone, reopens again, verifies the stream did not
resurrect, and verifies the tombstone survived.

## End-to-end HTTP smoke proof

To prove the full server path, run the end-to-end smoke:

```bash
python scripts/omnikv_e2e_smoke.py
```

Expected final output:

```text
PASS SketchLog OmniKV E2E smoke
```

This proof starts a real SketchLog HTTP server with:

- `SKETCHLOG_STORAGE_BACKEND=omnikv`;
- a real installed `omnikv` Python/native bridge;
- Sketch Mesh enabled for durable deletion tombstones.

It then writes telemetry through the HTTP ingest endpoint, gracefully restarts
the server, verifies metrics are still readable from OmniKV, deletes the stream
in mesh mode, verifies the tombstone exists in the embedded backend, restarts
again, and verifies stale stream state does not resurrect.

The pytest wrapper is marked `omnikv_real` and skips clearly when the bridge is
not installed:

```bash
python -m pytest tests/test_omnikv_e2e_smoke.py -m omnikv_real -v
```

## Unified storage proof runner

For launch screenshots, demos, and local confidence checks, use the unified
storage proof runner:

```bash
python scripts/storage_proof.py --backend memory
python scripts/storage_proof.py --backend omnikv
python scripts/storage_proof.py --backend postgres --postgres-start --postgres-stop
```

The runner prints a human-readable status table plus a JSON evidence report
with environment metadata, timings, restart behavior, delete verification, and
tombstone status for each selected backend. Use `--backend all
--allow-missing-optional` when you want one command that records unavailable
optional backends as skipped instead of failing.

## Durability behavior

The backend persists complete compressed `StreamLog` snapshots. On restart,
SketchLog reloads the requested stream from OmniKV on first access.

The backend preserves SQLAlchemy-compatible semantics:

- newer stream checkpoints replace older checkpoints;
- stale checkpoints are ignored using the stream `last_updated` timestamp;
- delete returns whether a checkpoint existed;
- mesh tombstones retain the highest observed version per stream key.

For mesh deletes, the embedded backend writes and syncs the tombstone before it
removes the stream checkpoint. The OmniKV bridge contract does not require a
cross-key transaction primitive, so this ordering is intentionally recoverable:
after an interrupted delete, cleanup can be retried, but a durable tombstone is
not lost after the stream checkpoint has been removed.

## Operational notes

- Keep `SKETCHLOG_OMNIKV_DATA_DIR` on durable local storage.
- Back up the OmniKV data directory using OmniKV's backup/restore tooling.
- Run SketchLog's `/ready` endpoint after startup; it checks durable storage
  health when a backend is configured.
- Use one SketchLog process per OmniKV embedded data directory unless the
  underlying OmniKV bridge enforces a cross-process database lock.
