"""Tests for sketchlog.db_hardening — configuration, advisories, schema checks, CLI.

SQLAlchemy-dependent paths (check_db_health, check_schema_version) are tested
with an in-memory SQLite engine so the test suite runs without a real Postgres
instance and without any extra test dependencies beyond the ``server`` extras.
"""

from __future__ import annotations

import json
import sys
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from sketchlog.db_hardening import (
    CURRENT_SCHEMA_VERSION,
    SCHEMA_VERSION_TABLE,
    DbCheckStatus,
    DbConfig,
    DbHealthResult,
    SchemaVersionResult,
    _redact_db_url,
    _sync_url,
    advise_pool_config,
    check_db_health,
    check_schema_version,
    main,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sqlite_cfg(**kwargs: Any) -> DbConfig:
    defaults: dict[str, Any] = {
        "url": "sqlite+aiosqlite:///test.db",
        "pool_size": 5,
        "max_overflow": 10,
        "pool_timeout": 30.0,
        "pool_recycle": 1_800,
    }
    defaults.update(kwargs)
    return DbConfig(**defaults)


def _pg_cfg(**kwargs: Any) -> DbConfig:
    defaults: dict[str, Any] = {
        "url": "postgresql+asyncpg://user:pass@localhost/sketchlog",
        "pool_size": 5,
        "max_overflow": 10,
        "pool_timeout": 30.0,
        "pool_recycle": 1_800,
    }
    defaults.update(kwargs)
    return DbConfig(**defaults)


# ---------------------------------------------------------------------------
# DbConfig validation
# ---------------------------------------------------------------------------


class TestDbConfigValidation:
    def test_valid_sqlite(self) -> None:
        cfg = _sqlite_cfg()
        assert cfg.url.startswith("sqlite")

    def test_valid_postgres(self) -> None:
        cfg = _pg_cfg()
        assert cfg.pool_size == 5

    def test_valid_mysql(self) -> None:
        cfg = DbConfig(url="mysql+aiomysql://user:pass@localhost/db")
        assert "mysql" in cfg.url

    def test_empty_url_raises(self) -> None:
        with pytest.raises(ValueError, match="url must be a non-empty string"):
            DbConfig(url="")

    def test_blank_url_raises(self) -> None:
        with pytest.raises(ValueError, match="url must be a non-empty string"):
            DbConfig(url="   ")

    def test_unsupported_scheme_raises(self) -> None:
        with pytest.raises(ValueError, match="unsupported url scheme"):
            DbConfig(url="oracle://user:pass@host/db")

    def test_pool_size_bool_raises(self) -> None:
        with pytest.raises(ValueError, match="pool_size must be an int"):
            DbConfig(url="sqlite:///x.db", pool_size=True)  # type: ignore[arg-type]

    def test_pool_size_zero_raises(self) -> None:
        with pytest.raises(ValueError, match="pool_size must be in"):
            DbConfig(url="sqlite:///x.db", pool_size=0)

    def test_pool_size_too_large_raises(self) -> None:
        with pytest.raises(ValueError, match="pool_size must be in"):
            DbConfig(url="sqlite:///x.db", pool_size=101)

    def test_max_overflow_negative_raises(self) -> None:
        with pytest.raises(ValueError, match="max_overflow must be in"):
            DbConfig(url="sqlite:///x.db", max_overflow=-1)

    def test_max_overflow_bool_raises(self) -> None:
        with pytest.raises(ValueError, match="max_overflow must be an int"):
            DbConfig(url="sqlite:///x.db", max_overflow=True)  # type: ignore[arg-type]

    def test_pool_timeout_nan_raises(self) -> None:
        with pytest.raises(ValueError, match="pool_timeout must be a finite float"):
            DbConfig(url="sqlite:///x.db", pool_timeout=float("nan"))

    def test_pool_timeout_inf_raises(self) -> None:
        with pytest.raises(ValueError, match="pool_timeout must be a finite float"):
            DbConfig(url="sqlite:///x.db", pool_timeout=float("inf"))

    def test_pool_timeout_bool_raises(self) -> None:
        with pytest.raises(ValueError, match="pool_timeout must be a float, not bool"):
            DbConfig(url="sqlite:///x.db", pool_timeout=True)  # type: ignore[arg-type]

    def test_pool_timeout_zero_raises(self) -> None:
        with pytest.raises(ValueError, match="pool_timeout must be a finite float"):
            DbConfig(url="sqlite:///x.db", pool_timeout=0.0)

    def test_pool_recycle_bool_raises(self) -> None:
        with pytest.raises(ValueError, match="pool_recycle must be an int"):
            DbConfig(url="sqlite:///x.db", pool_recycle=True)  # type: ignore[arg-type]

    def test_pool_recycle_too_small_raises(self) -> None:
        with pytest.raises(ValueError, match="pool_recycle must be in"):
            DbConfig(url="sqlite:///x.db", pool_recycle=59)

    def test_pool_recycle_too_large_raises(self) -> None:
        with pytest.raises(ValueError, match="pool_recycle must be in"):
            DbConfig(url="sqlite:///x.db", pool_recycle=86_401)

    def test_schema_version_table_empty_raises(self) -> None:
        with pytest.raises(ValueError, match="schema_version_table must be a non-empty string"):
            DbConfig(url="sqlite:///x.db", schema_version_table="")

    def test_schema_version_table_bad_chars_raises(self) -> None:
        with pytest.raises(ValueError, match="alphanumeric"):
            DbConfig(url="sqlite:///x.db", schema_version_table="bad-name!")

    def test_expected_version_zero_raises(self) -> None:
        with pytest.raises(ValueError, match="expected_version must be >= 1"):
            DbConfig(url="sqlite:///x.db", expected_version=0)

    def test_expected_version_bool_raises(self) -> None:
        with pytest.raises(ValueError, match="expected_version must be an int"):
            DbConfig(url="sqlite:///x.db", expected_version=True)  # type: ignore[arg-type]

    def test_connect_timeout_zero_raises(self) -> None:
        with pytest.raises(ValueError, match="connect_timeout must be in"):
            DbConfig(url="sqlite:///x.db", connect_timeout=0)

    def test_connect_timeout_too_large_raises(self) -> None:
        with pytest.raises(ValueError, match="connect_timeout must be in"):
            DbConfig(url="sqlite:///x.db", connect_timeout=121)

    def test_connect_timeout_bool_raises(self) -> None:
        with pytest.raises(ValueError, match="connect_timeout must be an int"):
            DbConfig(url="sqlite:///x.db", connect_timeout=True)  # type: ignore[arg-type]

    def test_multiple_errors_aggregated(self) -> None:
        with pytest.raises(ValueError) as exc_info:
            DbConfig(url="oracle://x", pool_size=0, max_overflow=-1)
        msg = str(exc_info.value)
        assert "pool_size" in msg
        assert "max_overflow" in msg

    def test_boundary_pool_size_min(self) -> None:
        cfg = DbConfig(url="sqlite:///x.db", pool_size=1)
        assert cfg.pool_size == 1

    def test_boundary_pool_size_max(self) -> None:
        cfg = DbConfig(url="sqlite:///x.db", pool_size=100)
        assert cfg.pool_size == 100

    def test_boundary_pool_timeout_min(self) -> None:
        cfg = DbConfig(url="sqlite:///x.db", pool_timeout=1.0)
        assert cfg.pool_timeout == 1.0

    def test_boundary_pool_timeout_max(self) -> None:
        cfg = DbConfig(url="sqlite:///x.db", pool_timeout=300.0)
        assert cfg.pool_timeout == 300.0


# ---------------------------------------------------------------------------
# Pool config advisor
# ---------------------------------------------------------------------------


class TestAdvisePoolConfig:
    def test_good_config_no_warnings(self) -> None:
        cfg = _pg_cfg()
        assert advise_pool_config(cfg) == []

    def test_pool_size_1_warns(self) -> None:
        cfg = _pg_cfg(pool_size=1)
        warnings = advise_pool_config(cfg)
        assert any("redundancy" in w for w in warnings)

    def test_high_total_connections_warns(self) -> None:
        cfg = _pg_cfg(pool_size=40, max_overflow=20)
        warnings = advise_pool_config(cfg)
        assert any("max_connections" in w for w in warnings)

    def test_mysql_high_recycle_warns(self) -> None:
        cfg = DbConfig(
            url="mysql+aiomysql://user:pass@localhost/db",
            pool_recycle=7_200,
        )
        warnings = advise_pool_config(cfg)
        assert any("MySQL" in w or "MariaDB" in w for w in warnings)

    def test_mysql_low_recycle_no_warn(self) -> None:
        cfg = DbConfig(
            url="mysql+aiomysql://user:pass@localhost/db",
            pool_recycle=1_800,
        )
        warnings = advise_pool_config(cfg)
        assert not any("MySQL" in w for w in warnings)

    def test_high_pool_timeout_warns(self) -> None:
        cfg = _pg_cfg(pool_timeout=120.0)
        warnings = advise_pool_config(cfg)
        assert any("pool_timeout" in w for w in warnings)

    def test_very_short_recycle_warns(self) -> None:
        cfg = _pg_cfg(pool_recycle=60)
        warnings = advise_pool_config(cfg)
        assert any("churn" in w for w in warnings)

    def test_sqlite_short_recycle_no_churn_warn(self) -> None:
        # SQLite guard should not fire for sqlite urls
        cfg = DbConfig(url="sqlite:///x.db", pool_recycle=60)
        warnings = advise_pool_config(cfg)
        # sqlite should not emit the churn warning
        assert not any("churn" in w for w in warnings)


# ---------------------------------------------------------------------------
# _sync_url
# ---------------------------------------------------------------------------


class TestSyncUrl:
    def test_asyncpg_to_psycopg2(self) -> None:
        result = _sync_url("postgresql+asyncpg://user:pass@localhost/db")
        assert result.startswith("postgresql+psycopg2://")

    def test_aiosqlite_to_sqlite(self) -> None:
        result = _sync_url("sqlite+aiosqlite:///test.db")
        assert result == "sqlite:///test.db"

    def test_aiomysql_to_mysqlconnector(self) -> None:
        result = _sync_url("mysql+aiomysql://user:pass@localhost/db")
        assert result.startswith("mysql+mysqlconnector://")

    def test_plain_sqlite_unchanged(self) -> None:
        url = "sqlite:///test.db"
        assert _sync_url(url) == url

    def test_psycopg_to_psycopg2(self) -> None:
        result = _sync_url("postgresql+psycopg://user:pass@localhost/db")
        assert result.startswith("postgresql+psycopg2://")


# ---------------------------------------------------------------------------
# _redact_db_url
# ---------------------------------------------------------------------------


class TestRedactDbUrl:
    def test_password_redacted(self) -> None:
        url = "postgresql+asyncpg://admin:s3cr3t@localhost/sketchlog"
        result = _redact_db_url(url)
        assert "s3cr3t" not in result
        assert "<redacted>" in result
        assert "admin" in result
        assert "localhost" in result

    def test_no_password_unchanged(self) -> None:
        url = "sqlite:///test.db"
        result = _redact_db_url(url)
        assert result == url

    def test_empty_string_safe(self) -> None:
        result = _redact_db_url("")
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# SchemaVersionResult.to_dict
# ---------------------------------------------------------------------------


class TestSchemaVersionResultToDict:
    def test_to_dict_json_serializable(self) -> None:
        r = SchemaVersionResult(
            status=DbCheckStatus.PASS,
            found_version=1,
            expected_version=1,
            message="ok",
        )
        d = r.to_dict()
        json.dumps(d)  # must not raise

    def test_to_dict_fields(self) -> None:
        r = SchemaVersionResult(
            status=DbCheckStatus.FAIL,
            found_version=None,
            expected_version=2,
            message="missing",
            detail="table not found",
        )
        d = r.to_dict()
        assert d["status"] == "fail"
        assert d["found_version"] is None
        assert d["expected_version"] == 2
        assert d["detail"] == "table not found"


# ---------------------------------------------------------------------------
# DbHealthResult.to_dict
# ---------------------------------------------------------------------------


class TestDbHealthResultToDict:
    def test_to_dict_json_serializable(self) -> None:
        sv = SchemaVersionResult(
            status=DbCheckStatus.PASS,
            found_version=1,
            expected_version=1,
            message="ok",
        )
        r = DbHealthResult(
            status=DbCheckStatus.PASS,
            reachable=True,
            schema_version=sv,
            pool_config_ok=True,
            latency_ms=5.2,
            message="all good",
            checks=[{"name": "reachability", "status": "pass", "message": "ok"}],
        )
        d = r.to_dict()
        json.dumps(d)  # must not raise
        assert d["status"] == "pass"
        assert d["reachable"] is True
        assert d["latency_ms"] == 5.2
        assert d["schema_version"]["status"] == "pass"


# ---------------------------------------------------------------------------
# check_schema_version — SQLite integration
# ---------------------------------------------------------------------------


class TestCheckSchemaVersionSQLite:
    @pytest.fixture()
    def cfg(self) -> DbConfig:
        return DbConfig(url="sqlite:///file::memory:?cache=shared&uri=true")

    def test_missing_table_returns_warn(self, cfg: DbConfig) -> None:
        result = check_schema_version(cfg)
        assert result.status is DbCheckStatus.WARN
        assert "does not exist" in result.message

    def test_pass_when_version_matches(self, tmp_path: Any) -> None:
        import sqlalchemy as sa

        db_url = f"sqlite:///{tmp_path}/test.db"
        engine = sa.create_engine(db_url)
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "CREATE TABLE sketchlog_schema_version "
                    "(id INTEGER PRIMARY KEY AUTOINCREMENT, version INTEGER NOT NULL)"
                )
            )
            conn.execute(
                sa.text(
                    "INSERT INTO sketchlog_schema_version (version) VALUES (:v)"
                ),
                {"v": CURRENT_SCHEMA_VERSION},
            )
        engine.dispose()

        cfg = DbConfig(url=db_url)
        result = check_schema_version(cfg)
        assert result.status is DbCheckStatus.PASS
        assert result.found_version == CURRENT_SCHEMA_VERSION

    def test_fail_when_version_behind(self, tmp_path: Any) -> None:
        import sqlalchemy as sa

        db_url = f"sqlite:///{tmp_path}/behind.db"
        engine = sa.create_engine(db_url)
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "CREATE TABLE sketchlog_schema_version "
                    "(id INTEGER PRIMARY KEY AUTOINCREMENT, version INTEGER NOT NULL)"
                )
            )
            conn.execute(
                sa.text(
                    "INSERT INTO sketchlog_schema_version (version) VALUES (:v)"
                ),
                {"v": 0},
            )
        engine.dispose()

        cfg = DbConfig(url=db_url, expected_version=2)
        result = check_schema_version(cfg)
        assert result.status is DbCheckStatus.FAIL
        assert result.found_version == 0

    def test_warn_when_version_ahead(self, tmp_path: Any) -> None:
        import sqlalchemy as sa

        db_url = f"sqlite:///{tmp_path}/ahead.db"
        engine = sa.create_engine(db_url)
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "CREATE TABLE sketchlog_schema_version "
                    "(id INTEGER PRIMARY KEY AUTOINCREMENT, version INTEGER NOT NULL)"
                )
            )
            conn.execute(
                sa.text(
                    "INSERT INTO sketchlog_schema_version (version) VALUES (:v)"
                ),
                {"v": 99},
            )
        engine.dispose()

        cfg = DbConfig(url=db_url, expected_version=1)
        result = check_schema_version(cfg)
        assert result.status is DbCheckStatus.WARN
        assert result.found_version == 99

    def test_warn_when_table_empty(self, tmp_path: Any) -> None:
        import sqlalchemy as sa

        db_url = f"sqlite:///{tmp_path}/empty.db"
        engine = sa.create_engine(db_url)
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "CREATE TABLE sketchlog_schema_version "
                    "(id INTEGER PRIMARY KEY AUTOINCREMENT, version INTEGER NOT NULL)"
                )
            )
        engine.dispose()

        cfg = DbConfig(url=db_url)
        result = check_schema_version(cfg)
        assert result.status is DbCheckStatus.WARN
        assert "empty" in result.message


# ---------------------------------------------------------------------------
# check_db_health — SQLite integration
# ---------------------------------------------------------------------------


class TestCheckDbHealthSQLite:
    def test_pass_with_in_memory_sqlite(self, tmp_path: Any) -> None:
        import sqlalchemy as sa

        db_url = f"sqlite:///{tmp_path}/health.db"
        engine = sa.create_engine(db_url)
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "CREATE TABLE sketchlog_schema_version "
                    "(id INTEGER PRIMARY KEY AUTOINCREMENT, version INTEGER NOT NULL)"
                )
            )
            conn.execute(
                sa.text(
                    "INSERT INTO sketchlog_schema_version (version) VALUES (:v)"
                ),
                {"v": CURRENT_SCHEMA_VERSION},
            )
        engine.dispose()

        cfg = DbConfig(url=db_url)
        result = check_db_health(cfg)
        assert result.reachable is True
        assert result.latency_ms is not None
        assert result.latency_ms >= 0

    def test_fail_on_unreachable_db(self) -> None:
        cfg = DbConfig(
            url="postgresql+asyncpg://user:pass@127.0.0.1:19999/nonexistent"
        )
        result = check_db_health(cfg)
        assert result.reachable is False
        assert result.status is DbCheckStatus.FAIL

    def test_to_dict_json_serializable(self, tmp_path: Any) -> None:
        import sqlalchemy as sa

        db_url = f"sqlite:///{tmp_path}/dict.db"
        engine = sa.create_engine(db_url)
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "CREATE TABLE sketchlog_schema_version "
                    "(id INTEGER PRIMARY KEY AUTOINCREMENT, version INTEGER NOT NULL)"
                )
            )
            conn.execute(
                sa.text(
                    "INSERT INTO sketchlog_schema_version (version) VALUES (:v)"
                ),
                {"v": CURRENT_SCHEMA_VERSION},
            )
        engine.dispose()

        cfg = DbConfig(url=db_url)
        result = check_db_health(cfg)
        d = result.to_dict()
        json.dumps(d)  # must not raise

    def test_checks_list_non_empty(self, tmp_path: Any) -> None:
        import sqlalchemy as sa

        db_url = f"sqlite:///{tmp_path}/checks.db"
        engine = sa.create_engine(db_url)
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "CREATE TABLE sketchlog_schema_version "
                    "(id INTEGER PRIMARY KEY AUTOINCREMENT, version INTEGER NOT NULL)"
                )
            )
            conn.execute(
                sa.text(
                    "INSERT INTO sketchlog_schema_version (version) VALUES (:v)"
                ),
                {"v": CURRENT_SCHEMA_VERSION},
            )
        engine.dispose()

        cfg = DbConfig(url=db_url)
        result = check_db_health(cfg)
        assert len(result.checks) >= 3


# ---------------------------------------------------------------------------
# CLI — main()
# ---------------------------------------------------------------------------


class TestMain:
    def test_missing_db_url_exits_2(self) -> None:
        with pytest.raises(SystemExit) as exc_info:
            main(["--format", "json"])
        assert exc_info.value.code == 2

    def test_bad_url_scheme_exits_2(self, capsys: Any) -> None:
        result = main(["--db-url", "oracle://x/y"])
        assert result == 2
        captured = capsys.readouterr()
        assert "ERROR" in captured.err

    def test_json_output_unreachable(self, capsys: Any) -> None:
        result = main([
            "--db-url", "postgresql+asyncpg://user:pass@127.0.0.1:19999/nodb",
            "--format", "json",
        ])
        assert result != 0
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["status"] == "fail"
        assert data["reachable"] is False

    def test_text_output_unreachable(self, capsys: Any) -> None:
        result = main([
            "--db-url", "postgresql+asyncpg://user:pass@127.0.0.1:19999/nodb",
            "--format", "text",
        ])
        assert result != 0
        captured = capsys.readouterr()
        assert "FAIL" in captured.out

    def test_sqlite_pass_exit_zero(self, tmp_path: Any, capsys: Any) -> None:
        import sqlalchemy as sa

        db_url = f"sqlite:///{tmp_path}/cli.db"
        engine = sa.create_engine(db_url)
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "CREATE TABLE sketchlog_schema_version "
                    "(id INTEGER PRIMARY KEY AUTOINCREMENT, version INTEGER NOT NULL)"
                )
            )
            conn.execute(
                sa.text(
                    "INSERT INTO sketchlog_schema_version (version) VALUES (:v)"
                ),
                {"v": CURRENT_SCHEMA_VERSION},
            )
        engine.dispose()

        result = main(["--db-url", db_url, "--format", "json"])
        assert result == 0
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["status"] == "pass"

    def test_pool_size_advisory_in_output(self, tmp_path: Any, capsys: Any) -> None:
        import sqlalchemy as sa

        db_url = f"sqlite:///{tmp_path}/adv.db"
        engine = sa.create_engine(db_url)
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "CREATE TABLE sketchlog_schema_version "
                    "(id INTEGER PRIMARY KEY AUTOINCREMENT, version INTEGER NOT NULL)"
                )
            )
            conn.execute(
                sa.text(
                    "INSERT INTO sketchlog_schema_version (version) VALUES (:v)"
                ),
                {"v": CURRENT_SCHEMA_VERSION},
            )
        engine.dispose()

        result = main([
            "--db-url", db_url,
            "--pool-size", "1",
            "--format", "json",
        ])
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        pool_checks = [c for c in data["checks"] if c["name"] == "pool-config"]
        messages = " ".join(c["message"] for c in pool_checks)
        assert "redundancy" in messages

    def test_pool_timeout_zero_exits_2(self, capsys: Any) -> None:
        result = main([
            "--db-url", "sqlite:///x.db",
            "--pool-timeout", "0.0",
        ])
        assert result == 2

    def test_expected_version_zero_exits_2(self, capsys: Any) -> None:
        result = main([
            "--db-url", "sqlite:///x.db",
            "--expected-version", "0",
        ])
        assert result == 2
