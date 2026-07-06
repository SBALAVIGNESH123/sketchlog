"""Production database hardening utilities for SketchLog durable storage.

SQLAlchemy-dependent operations (health check, schema version) require the
``server`` optional dependency group::

    pip install 'sketchlog[server]'

Configuration validation and pool advisory functions work with the standard
library only and are safe to import in any environment.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence
from urllib.parse import urlsplit, urlunsplit

# ---------------------------------------------------------------------------
# Public constants
# ---------------------------------------------------------------------------

SCHEMA_VERSION_TABLE: str = "sketchlog_schema_version"
CURRENT_SCHEMA_VERSION: int = 1

_MIN_POOL_SIZE = 1
_MAX_POOL_SIZE = 100
_MIN_MAX_OVERFLOW = 0
_MAX_MAX_OVERFLOW = 200
_MIN_POOL_TIMEOUT = 1.0
_MAX_POOL_TIMEOUT = 300.0
_MIN_POOL_RECYCLE = 60
_MAX_POOL_RECYCLE = 86_400
_MIN_CONNECT_TIMEOUT = 1
_MAX_CONNECT_TIMEOUT = 120

_SUPPORTED_SCHEMES = (
    "postgresql+asyncpg",
    "postgresql+psycopg",
    "postgresql",
    "mysql+aiomysql",
    "mysql",
    "sqlite+aiosqlite",
    "sqlite",
)

_ASYNC_TO_SYNC: Dict[str, str] = {
    "postgresql+asyncpg://": "postgresql+psycopg2://",
    "postgresql+psycopg://":  "postgresql+psycopg2://",
    "mysql+aiomysql://":      "mysql+mysqlconnector://",
    "sqlite+aiosqlite://":    "sqlite://",
}

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class DbCheckStatus(str, Enum):
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DbConfig:
    """Validated production database configuration.

    Parameters
    ----------
    url:
        SQLAlchemy database URL. Supported schemes:
        ``postgresql+asyncpg://``, ``postgresql+psycopg://``,
        ``postgresql://``, ``mysql+aiomysql://``, ``mysql://``,
        ``sqlite+aiosqlite://``, ``sqlite://``.
    pool_size:
        Persistent connections in the pool (default 5, range 1–100).
    max_overflow:
        Extra connections allowed above *pool_size* under burst load
        (default 10, range 0–200).
    pool_timeout:
        Seconds to wait for a free connection before raising ``TimeoutError``
        (default 30.0, range 1–300).
    pool_recycle:
        Seconds after which a connection is closed and replaced to prevent
        server-side timeout errors (default 1800, range 60–86400).
        Use ≤ 3600 for MySQL / MariaDB managed services.
    schema_version_table:
        Table name holding the current schema version. Must be alphanumeric +
        underscore only (default ``sketchlog_schema_version``).
    expected_version:
        Application schema version to compare against (default 1, minimum 1).
    connect_timeout:
        Per-connection driver timeout in seconds (default 10, range 1–120).
    """

    url: str
    pool_size: int = 5
    max_overflow: int = 10
    pool_timeout: float = 30.0
    pool_recycle: int = 1_800
    schema_version_table: str = SCHEMA_VERSION_TABLE
    expected_version: int = CURRENT_SCHEMA_VERSION
    connect_timeout: int = 10

    def __post_init__(self) -> None:  # noqa: C901 – validation method is intentionally long
        errors: List[str] = []

        # url ----------------------------------------------------------------
        if not isinstance(self.url, str) or not self.url.strip():
            errors.append("url must be a non-empty string")
        else:
            scheme = self.url.split("://")[0]
            if scheme not in _SUPPORTED_SCHEMES:
                errors.append(
                    f"unsupported url scheme {scheme!r}; supported: "
                    + ", ".join(_SUPPORTED_SCHEMES)
                )

        # pool_size ----------------------------------------------------------
        if isinstance(self.pool_size, bool) or not isinstance(self.pool_size, int):
            errors.append("pool_size must be an int")
        elif not (_MIN_POOL_SIZE <= self.pool_size <= _MAX_POOL_SIZE):
            errors.append(
                f"pool_size must be in [{_MIN_POOL_SIZE}, {_MAX_POOL_SIZE}]; "
                f"got {self.pool_size}"
            )

        # max_overflow -------------------------------------------------------
        if isinstance(self.max_overflow, bool) or not isinstance(self.max_overflow, int):
            errors.append("max_overflow must be an int")
        elif not (_MIN_MAX_OVERFLOW <= self.max_overflow <= _MAX_MAX_OVERFLOW):
            errors.append(
                f"max_overflow must be in [{_MIN_MAX_OVERFLOW}, {_MAX_MAX_OVERFLOW}]; "
                f"got {self.max_overflow}"
            )

        # pool_timeout -------------------------------------------------------
        if isinstance(self.pool_timeout, bool):
            errors.append("pool_timeout must be a float, not bool")
        else:
            try:
                _pt = float(self.pool_timeout)
            except (TypeError, ValueError):
                _pt = float("nan")
            if not math.isfinite(_pt) or not (_MIN_POOL_TIMEOUT <= _pt <= _MAX_POOL_TIMEOUT):
                errors.append(
                    f"pool_timeout must be a finite float in "
                    f"[{_MIN_POOL_TIMEOUT}, {_MAX_POOL_TIMEOUT}]; got {self.pool_timeout!r}"
                )

        # pool_recycle -------------------------------------------------------
        if isinstance(self.pool_recycle, bool) or not isinstance(self.pool_recycle, int):
            errors.append("pool_recycle must be an int")
        elif not (_MIN_POOL_RECYCLE <= self.pool_recycle <= _MAX_POOL_RECYCLE):
            errors.append(
                f"pool_recycle must be in [{_MIN_POOL_RECYCLE}, {_MAX_POOL_RECYCLE}]; "
                f"got {self.pool_recycle}"
            )

        # schema_version_table -----------------------------------------------
        if not isinstance(self.schema_version_table, str) or not self.schema_version_table.strip():
            errors.append("schema_version_table must be a non-empty string")
        elif not all(c.isalnum() or c == "_" for c in self.schema_version_table):
            errors.append(
                f"schema_version_table must contain only alphanumeric characters and "
                f"underscores; got {self.schema_version_table!r}"
            )

        # expected_version ---------------------------------------------------
        if isinstance(self.expected_version, bool) or not isinstance(self.expected_version, int):
            errors.append("expected_version must be an int")
        elif self.expected_version < 1:
            errors.append(f"expected_version must be >= 1; got {self.expected_version}")

        # connect_timeout ----------------------------------------------------
        if isinstance(self.connect_timeout, bool) or not isinstance(self.connect_timeout, int):
            errors.append("connect_timeout must be an int")
        elif not (_MIN_CONNECT_TIMEOUT <= self.connect_timeout <= _MAX_CONNECT_TIMEOUT):
            errors.append(
                f"connect_timeout must be in [{_MIN_CONNECT_TIMEOUT}, {_MAX_CONNECT_TIMEOUT}]; "
                f"got {self.connect_timeout}"
            )

        if errors:
            raise ValueError("; ".join(errors))


@dataclass(frozen=True)
class SchemaVersionResult:
    """Result of a schema version check."""

    status: DbCheckStatus
    found_version: Optional[int]
    expected_version: int
    message: str
    detail: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status.value,
            "found_version": self.found_version,
            "expected_version": self.expected_version,
            "message": self.message,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class DbHealthResult:
    """Comprehensive database health check result."""

    status: DbCheckStatus
    reachable: bool
    schema_version: Optional[SchemaVersionResult]
    pool_config_ok: bool
    latency_ms: Optional[float]
    message: str
    checks: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status.value,
            "reachable": self.reachable,
            "schema_version": self.schema_version.to_dict() if self.schema_version else None,
            "pool_config_ok": self.pool_config_ok,
            "latency_ms": self.latency_ms,
            "message": self.message,
            "checks": self.checks,
        }


# ---------------------------------------------------------------------------
# Pool configuration advisor  (no SQLAlchemy required)
# ---------------------------------------------------------------------------


def advise_pool_config(config: DbConfig) -> List[str]:
    """Return advisory warnings for the pool configuration.

    An empty list means the configuration looks production-ready.  These
    warnings are informational and do not represent connection failures.
    """
    warnings: List[str] = []

    if config.pool_size < 2:
        warnings.append(
            "pool_size=1 provides no connection redundancy; "
            "use pool_size >= 2 for production"
        )

    total_max = config.pool_size + config.max_overflow
    if total_max > 50:
        warnings.append(
            f"Total max connections ({total_max}) may exceed managed database "
            f"server limits (25–100 typical). Verify your database "
            f"server's max_connections setting."
        )

    if "mysql" in config.url and config.pool_recycle > 3_600:
        warnings.append(
            "MySQL/MariaDB: pool_recycle > 3600 s risks 'MySQL server has gone "
            "away' on managed services with a short wait_timeout. "
            "Recommended: pool_recycle=1800."
        )

    if config.pool_timeout > 60:
        warnings.append(
            f"pool_timeout={config.pool_timeout!r} s is unusually high; "
            "consider ≤ 30 s to surface connection exhaustion quickly"
        )

    if config.pool_recycle < 300 and "sqlite" not in config.url:
        warnings.append(
            f"pool_recycle={config.pool_recycle} s is very short and will cause "
            "excessive connection churn; consider >= 300 s"
        )

    return warnings


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _sync_url(url: str) -> str:
    """Map an async-driver URL to its synchronous equivalent."""
    for async_prefix, sync_prefix in _ASYNC_TO_SYNC.items():
        if url.startswith(async_prefix):
            return sync_prefix + url[len(async_prefix):]
    return url


def _is_postgres(url: str) -> bool:
    return url.startswith("postgresql") or url.startswith("postgres")


def _is_sqlite(url: str) -> bool:
    return url.startswith("sqlite")


def _build_engine(sa: Any, url: str, config: DbConfig) -> Any:
    """Create a synchronous SQLAlchemy engine for the given URL."""
    sync = _sync_url(url)
    if _is_sqlite(url):
        # SQLite: pool_size / max_overflow / connect_args not applicable
        return sa.create_engine(sync)
    connect_args: Dict[str, Any] = {}
    if _is_postgres(url):
        connect_args["connect_timeout"] = config.connect_timeout
    elif "mysql" in url:
        # mysqlconnector uses connection_timeout
        connect_args["connection_timeout"] = config.connect_timeout
    return sa.create_engine(
        sync,
        pool_size=config.pool_size,
        max_overflow=config.max_overflow,
        pool_timeout=config.pool_timeout,
        pool_recycle=config.pool_recycle,
        pool_pre_ping=True,
        connect_args=connect_args,
    )


def _require_sqlalchemy() -> Any:
    try:
        import sqlalchemy
        return sqlalchemy
    except ImportError as exc:
        raise ImportError(
            "SQLAlchemy is required for database health checks. "
            "Install it with: pip install 'sketchlog[server]'"
        ) from exc


def _redact_db_url(url: str) -> str:
    """Return a URL safe for logs (password replaced with ``<redacted>``).

    If the URL contains no password (no ``@`` in the authority component),
    the original string is returned unchanged to avoid urlsplit/urlunsplit
    round-trip issues with opaque URLs such as ``sqlite:///path``.
    """
    try:
        parts = urlsplit(url)
        netloc = parts.netloc
        if "@" not in netloc:
            # No credentials present — return original string unchanged.
            return url
        userinfo, host = netloc.rsplit("@", 1)
        user = userinfo.split(":", 1)[0]
        redacted_netloc = f"{user}:<redacted>@{host}"
        return urlunsplit(
            (parts.scheme, redacted_netloc, parts.path, parts.query, parts.fragment)
        )
    except Exception:
        return "<url-redacted>"


# ---------------------------------------------------------------------------
# Schema version check
# ---------------------------------------------------------------------------

_SCHEMA_VERSION_SELECT = (
    "SELECT version FROM {table} ORDER BY id DESC LIMIT 1"
)


def check_schema_version(config: DbConfig) -> SchemaVersionResult:
    """Read and validate the schema version from the database.

    This function is **read-only** — it never creates or modifies the version
    table. If the table does not exist the result is ``WARN`` with guidance to
    run migrations.

    Requires SQLAlchemy (``pip install 'sketchlog[server]'``).
    """
    sa = _require_sqlalchemy()
    table = config.schema_version_table

    try:
        engine = _build_engine(sa, config.url, config)
    except Exception as exc:
        return SchemaVersionResult(
            status=DbCheckStatus.FAIL,
            found_version=None,
            expected_version=config.expected_version,
            message="Failed to create database engine",
            detail=str(exc),
        )

    try:
        insp = sa.inspect(engine)
        if not insp.has_table(table):
            return SchemaVersionResult(
                status=DbCheckStatus.WARN,
                found_version=None,
                expected_version=config.expected_version,
                message=(
                    f"Schema version table '{table}' does not exist. "
                    "Run migrations before starting the server. "
                    "See docs/db-hardening.md for the DDL reference."
                ),
            )

        with engine.connect() as conn:
            row = conn.execute(
                sa.text(_SCHEMA_VERSION_SELECT.format(table=table))
            ).fetchone()

        if row is None:
            return SchemaVersionResult(
                status=DbCheckStatus.WARN,
                found_version=None,
                expected_version=config.expected_version,
                message=(
                    f"Schema version table '{table}' exists but is empty. "
                    "Run migrations to populate it."
                ),
            )

        found = int(row[0])
        if found == config.expected_version:
            return SchemaVersionResult(
                status=DbCheckStatus.PASS,
                found_version=found,
                expected_version=config.expected_version,
                message=(
                    f"Schema version {found} matches expected {config.expected_version}"
                ),
            )
        if found < config.expected_version:
            return SchemaVersionResult(
                status=DbCheckStatus.FAIL,
                found_version=found,
                expected_version=config.expected_version,
                message=(
                    f"Schema version {found} is behind expected "
                    f"{config.expected_version}. "
                    "Run pending migrations before starting the server."
                ),
            )
        # found > expected
        return SchemaVersionResult(
            status=DbCheckStatus.WARN,
            found_version=found,
            expected_version=config.expected_version,
            message=(
                f"Schema version {found} is ahead of expected "
                f"{config.expected_version}. "
                "Downgrading is not supported; update the application to match."
            ),
        )
    except Exception as exc:
        return SchemaVersionResult(
            status=DbCheckStatus.FAIL,
            found_version=None,
            expected_version=config.expected_version,
            message="Schema version query failed",
            detail=str(exc),
        )
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# Full database health check
# ---------------------------------------------------------------------------


def check_db_health(config: DbConfig) -> DbHealthResult:
    """Run a full production database health check.

    Checks (in order):

    1. **Pool config advisory** — non-fatal configuration recommendations.
    2. **Reachability** — ``SELECT 1`` with round-trip latency measurement.
    3. **Schema version** — compares found version against expected version.

    Returns a :class:`DbHealthResult` with an overall ``PASS``/``WARN``/``FAIL``
    status and per-check detail suitable for inclusion in ``sketchlog-doctor``
    output.

    Requires SQLAlchemy (``pip install 'sketchlog[server]'``).
    """
    import time

    sa = _require_sqlalchemy()
    checks: List[Dict[str, Any]] = []

    # 1. Pool config advisory ------------------------------------------------
    pool_warnings = advise_pool_config(config)
    pool_config_ok = not pool_warnings
    for w in pool_warnings:
        checks.append({"name": "pool-config", "status": "warn", "message": w})
    if pool_config_ok:
        checks.append({
            "name": "pool-config",
            "status": "pass",
            "message": (
                f"pool_size={config.pool_size}, "
                f"max_overflow={config.max_overflow}, "
                f"pool_recycle={config.pool_recycle} s — looks production-ready"
            ),
        })

    # 2. Reachability --------------------------------------------------------
    latency_ms: Optional[float] = None
    engine: Any = None
    try:
        engine = _build_engine(sa, config.url, config)
        t0 = time.perf_counter()
        with engine.connect() as conn:
            conn.execute(sa.text("SELECT 1"))
        latency_ms = round((time.perf_counter() - t0) * 1_000, 2)

        checks.append({
            "name": "reachability",
            "status": "pass",
            "message": f"Database reachable; round-trip latency {latency_ms} ms",
        })
        if latency_ms > 100:
            checks.append({
                "name": "latency",
                "status": "warn",
                "message": (
                    f"Round-trip latency {latency_ms} ms exceeds 100 ms; "
                    "check network path and server load"
                ),
            })
        else:
            checks.append({
                "name": "latency",
                "status": "pass",
                "message": (
                    f"Round-trip latency {latency_ms} ms is within the 100 ms threshold"
                ),
            })

    except Exception as exc:
        checks.append({
            "name": "reachability",
            "status": "fail",
            "message": f"Database unreachable: {exc}",
        })
        return DbHealthResult(
            status=DbCheckStatus.FAIL,
            reachable=False,
            schema_version=None,
            pool_config_ok=pool_config_ok,
            latency_ms=None,
            message="Database is not reachable — cannot continue health check",
            checks=checks,
        )
    finally:
        if engine is not None:
            engine.dispose()

    # 3. Schema version ------------------------------------------------------
    sv = check_schema_version(config)
    checks.append({
        "name": "schema-version",
        "status": sv.status.value,
        "message": sv.message,
        "detail": sv.detail,
    })

    # Overall status ---------------------------------------------------------
    has_fail = any(c["status"] == "fail" for c in checks)
    has_warn = any(c["status"] == "warn" for c in checks)

    if has_fail:
        overall = DbCheckStatus.FAIL
        msg = "Database health check FAILED — see individual checks for details"
    elif has_warn:
        overall = DbCheckStatus.WARN
        msg = "Database health check passed with warnings — see individual checks"
    else:
        overall = DbCheckStatus.PASS
        msg = "Database health check PASSED"

    return DbHealthResult(
        status=overall,
        reachable=True,
        schema_version=sv,
        pool_config_ok=pool_config_ok,
        latency_ms=latency_ms,
        message=msg,
        checks=checks,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args(argv: Optional[Sequence[str]]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="sketchlog-db-check",
        description=(
            "Check SketchLog production database health: "
            "reachability, schema version, and connection pool configuration."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--db-url",
        required=True,
        metavar="URL",
        help=(
            "SQLAlchemy database URL, e.g. "
            "postgresql+asyncpg://user:pass@localhost/sketchlog"
        ),
    )
    parser.add_argument(
        "--pool-size", type=int, default=5, metavar="N",
        help="Connection pool size",
    )
    parser.add_argument(
        "--max-overflow", type=int, default=10, metavar="N",
        help="Max overflow connections above pool-size",
    )
    parser.add_argument(
        "--pool-timeout", type=float, default=30.0, metavar="SEC",
        help="Seconds to wait for a free pool connection",
    )
    parser.add_argument(
        "--pool-recycle", type=int, default=1_800, metavar="SEC",
        help="Connection recycle interval in seconds",
    )
    parser.add_argument(
        "--expected-version",
        type=int,
        default=CURRENT_SCHEMA_VERSION,
        metavar="N",
        help="Expected schema version",
    )
    parser.add_argument(
        "--schema-version-table",
        default=SCHEMA_VERSION_TABLE,
        metavar="TABLE",
        help="Schema version table name",
    )
    parser.add_argument(
        "--connect-timeout", type=int, default=10, metavar="SEC",
        help="Per-connection driver timeout",
    )
    parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="Output format",
    )
    return parser.parse_args(argv)


def _render_text(result: DbHealthResult, config: DbConfig) -> str:
    lines = [
        "SketchLog database health check",
        f"URL:  {_redact_db_url(config.url)}",
        "",
    ]
    width = max((len(c["name"]) for c in result.checks), default=20)
    for check in result.checks:
        status_label = check["status"].upper()
        label = check["name"]
        msg = check["message"]
        detail = check.get("detail")
        lines.append(f"{status_label:<5}  {label:<{width}}  {msg}")
        if detail:
            lines.append(f"       detail: {detail}")
    lines += [
        "",
        f"Result:  {result.status.value.upper()}  —  {result.message}",
    ]
    if result.latency_ms is not None:
        lines.append(f"Latency: {result.latency_ms} ms")
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parse_args(argv)

    try:
        config = DbConfig(
            url=args.db_url,
            pool_size=args.pool_size,
            max_overflow=args.max_overflow,
            pool_timeout=args.pool_timeout,
            pool_recycle=args.pool_recycle,
            expected_version=args.expected_version,
            schema_version_table=args.schema_version_table,
            connect_timeout=args.connect_timeout,
        )
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    try:
        result = check_db_health(config)
    except ImportError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"ERROR: unexpected error during health check: {exc}", file=sys.stderr)
        return 1

    if args.format == "json":
        print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
    else:
        print(_render_text(result, config))

    return 0 if result.status is DbCheckStatus.PASS else 1


if __name__ == "__main__":
    raise SystemExit(main())
