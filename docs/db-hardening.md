# Production Database Hardening

SketchLog's durable storage layer is built on SQLAlchemy and supports
**PostgreSQL** (recommended), **MySQL / MariaDB**, and **SQLite** (development
only). This guide covers everything you need for a safe, long-running
production deployment: setup, connection pool tuning, schema migrations,
backup / restore, health checks, and a Docker Compose reference.

---

## Quick start

Install the server extras, then add the sync driver for your backend:

```bash
# Required for all backends
pip install 'sketchlog[server]'

# PostgreSQL sync driver (used by the health check only)
pip install psycopg2-binary

# MySQL / MariaDB sync driver (used by the health check only)
pip install mysql-connector-python
```

Then run:

```bash
sketchlog-db-check \
  --db-url postgresql+asyncpg://user:pass@localhost/sketchlog \
  --format text
```

A passing run prints:

```text
SketchLog database health check
URL:  postgresql+asyncpg://user:<redacted>@localhost/sketchlog

PASS   pool-config        pool_size=5, max_overflow=10, pool_recycle=1800 s — looks production-ready
PASS   reachability       Database reachable; round-trip latency 3.21 ms
PASS   latency            Round-trip latency 3.21 ms is within the 100 ms threshold
PASS   schema-version     Schema version 1 matches expected 1

Result:  PASS  —  Database health check PASSED
Latency: 3.21 ms
```

---

## Supported databases

| Backend | Async driver | Sync driver (health check) | Extra install | Notes |
|---|---|---|---|---|
| PostgreSQL 14+ | `asyncpg` | `psycopg2` | `pip install psycopg2-binary` | Recommended for production |
| PostgreSQL 14+ | `psycopg` (v3) | `psycopg2` | `pip install psycopg2-binary` | Alternative async driver |
| MySQL 8 / MariaDB 10.6+ | `aiomysql` | `mysqlconnector` | `pip install mysql-connector-python` | Set `pool_recycle ≤ 3600` |
| SQLite | `aiosqlite` | built-in | included in `sketchlog[server]` | Development / CI only |

---

## PostgreSQL setup

### 1. Create the database and user

```sql
CREATE USER sketchlog WITH PASSWORD 'changeme';
CREATE DATABASE sketchlog OWNER sketchlog;
GRANT ALL PRIVILEGES ON DATABASE sketchlog TO sketchlog;
```

### 2. Schema version table (DDL)

Run this once before starting the server:

```sql
CREATE TABLE IF NOT EXISTS sketchlog_schema_version (
    id      SERIAL PRIMARY KEY,
    version INTEGER NOT NULL,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO sketchlog_schema_version (version) VALUES (1);
```

### 3. Server configuration (`sketchlog.toml`)

```toml
[storage]
url            = "postgresql+asyncpg://sketchlog:changeme@localhost/sketchlog"
pool_size      = 10
max_overflow   = 20
pool_timeout   = 30.0
pool_recycle   = 1800
connect_timeout = 10
```

### 4. Verify

```bash
sketchlog-db-check \
  --db-url postgresql+asyncpg://sketchlog:changeme@localhost/sketchlog \
  --pool-size 10 \
  --max-overflow 20
```

---

## MySQL / MariaDB setup

```sql
CREATE DATABASE sketchlog CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'sketchlog'@'%' IDENTIFIED BY 'changeme';
GRANT ALL PRIVILEGES ON sketchlog.* TO 'sketchlog'@'%';
FLUSH PRIVILEGES;
```

Schema version table (MySQL syntax):

```sql
CREATE TABLE IF NOT EXISTS sketchlog_schema_version (
    id      INT AUTO_INCREMENT PRIMARY KEY,
    version INT NOT NULL,
    applied_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

INSERT INTO sketchlog_schema_version (version) VALUES (1);
```

> **Important:** Set `pool_recycle ≤ 3600` on managed MySQL services
> (RDS, Cloud SQL, PlanetScale) to avoid *"MySQL server has gone away"*
> errors caused by their default 3600 s `wait_timeout`.

```toml
[storage]
url          = "mysql+aiomysql://sketchlog:changeme@localhost/sketchlog"
pool_recycle = 1800
```

---

## Connection pool tuning

| Parameter | Default | Range | Guidance |
|---|---|---|---|
| `pool_size` | 5 | 1–100 | Set to `(vCPUs × 2) + 1` as a starting point |
| `max_overflow` | 10 | 0–200 | Burst headroom; keep `pool_size + max_overflow ≤ 50` on managed DBs |
| `pool_timeout` | 30.0 s | 1–300 | Lower (≤ 10 s) surfaces exhaustion faster in high-traffic services |
| `pool_recycle` | 1800 s | 60–86400 | Use ≤ 3600 for MySQL; 1800 is safe for all backends |
| `connect_timeout` | 10 s | 1–120 | Per-connection driver timeout; 10 s is suitable for LAN deployments |

`pool_pre_ping=True` is always enabled — stale connections are silently
replaced before use.

### Advisory warnings

`sketchlog-db-check` emits `WARN` (not `FAIL`) for advisory issues:

- `pool_size=1` — no redundancy
- `pool_size + max_overflow > 50` — may exceed managed DB limits
- `pool_recycle > 3600` on MySQL — risk of *server has gone away*
- `pool_timeout > 60 s` — slow exhaustion detection
- `pool_recycle < 300 s` on non-SQLite — excessive churn

---

## Schema migrations

SketchLog uses a lightweight `sketchlog_schema_version` table instead of a
full migration framework. The server refuses to start if the found version is
behind the expected version.

### Upgrading

1. Apply your DDL changes manually or with your preferred migration tool
   (Flyway, Liquibase, or a plain SQL script).
2. Insert the new version row:
   ```sql
   INSERT INTO sketchlog_schema_version (version) VALUES (2);
   ```
3. Run `sketchlog-db-check --expected-version 2` to confirm.
4. Start the new server version.

### Downgrading

Downgrading is **not supported** — the server will start with a `WARN` if the
found version is ahead of the application's expected version. Roll forward
instead.

---

## Backup and restore

### PostgreSQL — `pg_dump` / `pg_restore`

```bash
# Backup (custom format, compressed)
pg_dump \
  --format=custom \
  --compress=9 \
  --dbname=postgresql://sketchlog:changeme@localhost/sketchlog \
  --file=sketchlog-$(date +%Y%m%d).dump

# Restore
pg_restore \
  --clean \
  --if-exists \
  --dbname=postgresql://sketchlog:changeme@localhost/sketchlog \
  sketchlog-20260101.dump
```

**Automate with cron:**

```cron
0 2 * * * pg_dump --format=custom --compress=9 \
  --dbname=postgresql://sketchlog:changeme@localhost/sketchlog \
  --file=/backups/sketchlog-$(date +\%Y\%m\%d).dump
```

### MySQL — `mysqldump`

```bash
mysqldump \
  --single-transaction \
  --routines \
  --triggers \
  -u sketchlog -p sketchlog \
  > sketchlog-$(date +%Y%m%d).sql
```

### Point-in-time recovery

For managed services (RDS, Cloud SQL, Azure Database) enable automated backups
and point-in-time recovery in your cloud console. SketchLog does not ship
backup management itself — delegate to your cloud provider or DBA tooling.

---

## Docker Compose example (PostgreSQL)

For a reproducible end-to-end proof, use the checked-in PostgreSQL durability
stack:

```bash
docker compose -f demo/postgres/compose.yml up --build -d --wait
python scripts/postgres_durability_proof.py
```

The proof writes telemetry, restarts SketchLog, verifies restored metrics,
deletes the stream, checks the durable mesh tombstone in PostgreSQL, restarts
again, and confirms the deleted stream is not resurrected.

The same scenario is also available through the unified storage proof runner:

```bash
python scripts/storage_proof.py --backend memory
python scripts/storage_proof.py --backend postgres --postgres-start --postgres-stop
python scripts/storage_proof.py --backend omnikv
```

The unified runner emits both a readable summary and JSON evidence with timings
and environment metadata, making it suitable for reproducible screenshots and
launch-video proof clips.

```yaml
version: "3.9"

services:
  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_DB: sketchlog
      POSTGRES_USER: sketchlog
      POSTGRES_PASSWORD: changeme
    volumes:
      - pg_data:/var/lib/postgresql/data
      - ./docker/initdb:/docker-entrypoint-initdb.d:ro
    ports:
      - "5432:5432"
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U sketchlog"]
      interval: 10s
      timeout: 5s
      retries: 5

  sketchlog:
    image: sketchlog:latest
    depends_on:
      postgres:
        condition: service_healthy
    environment:
      SKETCHLOG_DB_URL: "postgresql+asyncpg://sketchlog:changeme@postgres/sketchlog"
      SKETCHLOG_POOL_SIZE: "10"
      SKETCHLOG_MAX_OVERFLOW: "20"
    ports:
      - "8000:8000"

volumes:
  pg_data:
```

Place your schema DDL in `docker/initdb/01_schema_version.sql` — Docker
will run it on first startup.

---

## Health check CLI reference

```bash
sketchlog-db-check [OPTIONS]
```

| Option | Default | Description |
|---|---|---|
| `--db-url URL` | required | SQLAlchemy database URL |
| `--pool-size N` | 5 | Connection pool size |
| `--max-overflow N` | 10 | Max overflow above pool-size |
| `--pool-timeout SEC` | 30.0 | Wait timeout for a free connection |
| `--pool-recycle SEC` | 1800 | Connection recycle interval |
| `--expected-version N` | 1 | Expected schema version |
| `--schema-version-table TABLE` | `sketchlog_schema_version` | Version table name |
| `--connect-timeout SEC` | 10 | Per-connection driver timeout |
| `--format text\|json` | `text` | Output format |

**Exit codes:**

| Code | Meaning |
|---|---|
| 0 | All checks passed |
| 1 | One or more checks failed or warned |
| 2 | Invalid configuration (bad URL, out-of-range value) |

### JSON output example

```bash
sketchlog-db-check --db-url postgresql+asyncpg://... --format json
```

```json
{
  "checks": [
    {"name": "pool-config",    "status": "pass", "message": "pool_size=5 ..."},
    {"name": "reachability",   "status": "pass", "message": "Database reachable; round-trip latency 3.21 ms"},
    {"name": "latency",        "status": "pass", "message": "Round-trip latency 3.21 ms is within the 100 ms threshold"},
    {"name": "schema-version", "status": "pass", "message": "Schema version 1 matches expected 1"}
  ],
  "latency_ms": 3.21,
  "message": "Database health check PASSED",
  "pool_config_ok": true,
  "reachable": true,
  "schema_version": {
    "detail": null,
    "expected_version": 1,
    "found_version": 1,
    "message": "Schema version 1 matches expected 1",
    "status": "pass"
  },
  "status": "pass"
}
```

---

## Integrating with `sketchlog-doctor`

`sketchlog-doctor` calls `check_db_health()` internally when a storage URL is
configured. The database section appears in its output automatically:

```text
[database]  PASS  reachable=true  latency=3.21 ms  schema=1/1
```

You can also call the Python API directly in your own readiness probes:

```python
from sketchlog.db_hardening import DbConfig, check_db_health, DbCheckStatus

config = DbConfig(
    url="postgresql+asyncpg://sketchlog:changeme@localhost/sketchlog",
    pool_size=10,
    max_overflow=20,
)
result = check_db_health(config)

if result.status is not DbCheckStatus.PASS:
    for check in result.checks:
        if check["status"] != "pass":
            print(f"[{check['status'].upper()}] {check['name']}: {check['message']}")
    raise SystemExit(1)
```

---

## Security notes

- Never log the raw database URL — `_redact_db_url()` replaces passwords
  with `<redacted>` before any log output.
- Use environment variables or a secrets manager for credentials; avoid
  hard-coding passwords in configuration files.
- Prefer SSL/TLS connections: append `?sslmode=require` (PostgreSQL) or
  `?ssl=true` (MySQL) to your URL.
- Rotate credentials regularly; `pool_recycle` ensures old connections are
  replaced without a server restart.

---

## Caveats

1. **Health checks require `sketchlog[server]`** — `check_db_health()` and
   `check_schema_version()` import SQLAlchemy at call time. Config validation
   and pool advisory functions work with the standard library only.
2. **SQLite is not supported in production** — SQLite lacks the concurrency
   primitives needed for multi-worker SketchLog deployments.
3. **`check_schema_version` is read-only** — it never creates or modifies the
   version table.
4. **Latency threshold (100 ms)** is advisory, not a hard failure.
5. **Backup guidance is illustrative** — test your restore procedure in a
   staging environment before relying on it.
6. **`max_overflow` connections are temporary** — they are created on demand
   and closed after use; only `pool_size` connections persist.
