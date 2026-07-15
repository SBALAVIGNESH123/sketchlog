# PostgreSQL durability proof

This demo proves that SketchLog can persist bounded stream state and mesh
tombstones through PostgreSQL using the SQLAlchemy storage backend.

## Start the stack

```bash
docker compose -f demo/postgres/compose.yml up --build -d --wait
```

The stack starts:

- PostgreSQL 16;
- the SketchLog server configured with `SKETCHLOG_DB_URI`;
- single-node mesh mode so delete operations create durable tombstones.

## Run the proof

```bash
python scripts/postgres_durability_proof.py
```

Expected final output:

```text
PASS PostgreSQL durability proof
```

The proof script verifies:

1. SketchLog becomes ready with PostgreSQL storage configured.
2. Telemetry is written into a namespaced stream.
3. Metrics are available before restart.
4. Restarting the server preserves the stream state.
5. Deleting the stream removes the durable checkpoint.
6. A mesh tombstone is present in PostgreSQL.
7. A second restart does not resurrect the deleted stream.

## Clean up

```bash
docker compose -f demo/postgres/compose.yml down --volumes --remove-orphans
```

Use this proof when recording launch material or checking PostgreSQL storage
behavior before a release.
