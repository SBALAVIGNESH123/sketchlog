"""Tests for sketchlog.query_builder."""
from __future__ import annotations

import json
import os
import time
import tempfile
import pytest

from sketchlog.query_builder import (
    QueryBuilderConfig,
    ValidationError,
    ValidationResult,
    CompletionItem,
    ExplainPlan,
    ApiRequest,
    SavedQuery,
    HistoryEntry,
    QueryStore,
    autocomplete,
    validate_query,
    explain_query,
    build_api_request,
    _EXAMPLE_TEMPLATES,
    main,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_GOOD_SQL = (
    "SELECT service, APPROX_QUANTILE(latency_ms, 0.99) AS p99 "
    "FROM request_latency "
    "GROUP BY service, TUMBLE(ts, INTERVAL '5' MINUTE) "
    "EMIT FINAL;"
)

_SIMPLE_SQL = "SELECT id, value FROM my_stream;"


# ---------------------------------------------------------------------------
# autocomplete
# ---------------------------------------------------------------------------

class TestAutocomplete:
    def test_keyword_prefix(self):
        items = autocomplete("SEL")
        labels = [i.label for i in items]
        assert "SELECT" in labels

    def test_function_prefix(self):
        items = autocomplete("APPROX")
        labels = [i.label for i in items]
        assert any("APPROX_QUANTILE" in l for l in labels)

    def test_stream_prefix(self):
        items = autocomplete("req", streams=["request_latency", "request_errors"])
        labels = [i.label for i in items]
        assert "request_latency" in labels
        assert "request_errors" in labels

    def test_stream_prefix_no_match(self):
        items = autocomplete("xyz", streams=["request_latency"])
        assert items == []

    def test_case_insensitive(self):
        items = autocomplete("sel")
        labels = [i.label for i in items]
        assert "SELECT" in labels

    def test_column_prefix(self):
        items = autocomplete("lat", columns={"metrics": ["latency_ms", "timestamp"]})
        labels = [i.label for i in items]
        assert any("latency_ms" in l for l in labels)

    def test_empty_prefix_returns_all(self):
        items = autocomplete("")
        assert len(items) > 10

    def test_item_kinds(self):
        items = autocomplete("COUNT")
        kinds = {i.kind for i in items}
        assert "aggregate_function" in kinds

    def test_scalar_function_kind(self):
        items = autocomplete("ABS")
        kinds = {i.kind for i in items}
        assert "scalar_function" in kinds

    def test_returns_completion_items(self):
        items = autocomplete("SEL")
        assert all(isinstance(i, CompletionItem) for i in items)


# ---------------------------------------------------------------------------
# validate_query
# ---------------------------------------------------------------------------

class TestValidateQuery:
    def test_good_query_is_valid(self):
        result = validate_query(_GOOD_SQL)
        assert result.valid

    def test_simple_select_valid(self):
        result = validate_query(_SIMPLE_SQL)
        assert result.valid

    def test_empty_query_invalid(self):
        result = validate_query("")
        assert not result.valid
        assert any(e.code == "EMPTY_QUERY" for e in result.errors)

    def test_missing_select(self):
        result = validate_query("FROM my_stream;")
        assert not result.valid
        assert any(e.code == "MISSING_SELECT" for e in result.errors)

    def test_missing_from(self):
        result = validate_query("SELECT id;")
        assert not result.valid
        assert any(e.code == "MISSING_FROM" for e in result.errors)

    def test_unbalanced_open_paren(self):
        result = validate_query("SELECT COUNT(id FROM my_stream;")
        assert not result.valid
        assert any(e.code == "UNBALANCED_PAREN" for e in result.errors)

    def test_unbalanced_close_paren(self):
        result = validate_query("SELECT COUNT(id)) FROM my_stream;")
        assert not result.valid
        assert any(e.code == "UNBALANCED_PAREN" for e in result.errors)

    def test_unknown_function_warning(self):
        result = validate_query("SELECT FOOBAR(id) FROM my_stream;")
        assert any(w.code == "UNKNOWN_FUNCTION" for w in result.warnings)

    def test_missing_emit_warning(self):
        result = validate_query(
            "SELECT COUNT(*) FROM s GROUP BY TUMBLE(ts, INTERVAL '1' MINUTE);"
        )
        assert any(w.code == "MISSING_EMIT" for w in result.warnings)

    def test_no_missing_emit_when_present(self):
        result = validate_query(_GOOD_SQL)
        assert not any(w.code == "MISSING_EMIT" for w in result.warnings)

    def test_select_star_warning(self):
        result = validate_query("SELECT * FROM my_stream;")
        assert any(w.code == "SELECT_STAR" for w in result.warnings)

    def test_missing_semicolon_warning(self):
        result = validate_query("SELECT id FROM my_stream")
        assert any(w.code == "MISSING_SEMICOLON" for w in result.warnings)

    def test_comment_stripped(self):
        sql = "-- this is a comment\nSELECT id FROM s;"
        result = validate_query(sql)
        assert result.valid

    def test_to_dict_schema(self):
        result = validate_query(_SIMPLE_SQL)
        d = result.to_dict()
        assert "valid" in d
        assert "errors" in d
        assert "warnings" in d

    def test_to_dict_json_serializable(self):
        result = validate_query(_GOOD_SQL)
        json.dumps(result.to_dict())  # must not raise

    def test_whitespace_only_invalid(self):
        result = validate_query("   \n\t  ")
        assert not result.valid


# ---------------------------------------------------------------------------
# explain_query
# ---------------------------------------------------------------------------

class TestExplainQuery:
    def test_returns_explain_plan(self):
        plan = explain_query(_GOOD_SQL)
        assert isinstance(plan, ExplainPlan)

    def test_identifies_stream(self):
        plan = explain_query(_GOOD_SQL)
        assert "request_latency" in plan.estimated_streams

    def test_uses_sketch_true(self):
        plan = explain_query(_GOOD_SQL)
        assert plan.uses_sketch

    def test_uses_sketch_false(self):
        plan = explain_query(_SIMPLE_SQL)
        assert not plan.uses_sketch

    def test_uses_window_true(self):
        plan = explain_query(_GOOD_SQL)
        assert plan.uses_window

    def test_uses_window_false(self):
        plan = explain_query(_SIMPLE_SQL)
        assert not plan.uses_window

    def test_steps_non_empty(self):
        plan = explain_query(_GOOD_SQL)
        assert len(plan.steps) > 0

    def test_render_text_contains_stream(self):
        plan = explain_query(_GOOD_SQL)
        text = plan.render_text()
        assert "request_latency" in text

    def test_to_dict_json_serializable(self):
        plan = explain_query(_GOOD_SQL)
        json.dumps(plan.to_dict())  # must not raise

    def test_note_for_sketch(self):
        plan = explain_query(_GOOD_SQL)
        assert "approximate" in plan.note.lower() or plan.note == ""


# ---------------------------------------------------------------------------
# build_api_request
# ---------------------------------------------------------------------------

class TestBuildApiRequest:
    def test_returns_api_request(self):
        req = build_api_request(_SIMPLE_SQL, "https://localhost:8080")
        assert isinstance(req, ApiRequest)

    def test_url_contains_namespace(self):
        req = build_api_request(_SIMPLE_SQL, "https://localhost:8080", namespace="prod")
        assert "prod" in req.url

    def test_method_is_post(self):
        req = build_api_request(_SIMPLE_SQL, "https://localhost:8080")
        assert req.method == "POST"

    def test_body_contains_sql(self):
        req = build_api_request(_SIMPLE_SQL, "https://localhost:8080")
        assert req.body["sql"] == _SIMPLE_SQL

    def test_token_in_header(self):
        req = build_api_request(_SIMPLE_SQL, "https://localhost:8080", token="tok123")
        assert req.headers.get("Authorization") == "Bearer tok123"

    def test_env_token_preferred(self, monkeypatch):
        monkeypatch.setenv("SKETCHLOG_TOKEN", "envtok")
        req = build_api_request(_SIMPLE_SQL, "https://localhost:8080", token="inline")
        assert "envtok" in req.headers.get("Authorization", "")

    def test_to_curl_output(self):
        req = build_api_request(_SIMPLE_SQL, "https://localhost:8080")
        curl = req.to_curl()
        assert "curl" in curl
        assert "https://localhost:8080" in curl

    def test_to_python_output(self):
        req = build_api_request(_SIMPLE_SQL, "https://localhost:8080")
        py = req.to_python()
        assert "urllib.request" in py

    def test_to_dict_json_serializable(self):
        req = build_api_request(_SIMPLE_SQL, "https://localhost:8080")
        json.dumps(req.to_dict())  # must not raise

    def test_namespace_url_encoded(self):
        req = build_api_request(_SIMPLE_SQL, "https://host", namespace="my namespace")
        assert "my%20namespace" in req.url or "my+namespace" in req.url


# ---------------------------------------------------------------------------
# QueryBuilderConfig
# ---------------------------------------------------------------------------

class TestQueryBuilderConfig:
    def test_valid_config(self):
        cfg = QueryBuilderConfig(server_url="https://host", namespace="prod")
        assert cfg.namespace == "prod"

    def test_http_url_rejected(self):
        with pytest.raises(ValueError, match="https://"):
            QueryBuilderConfig(server_url="http://host")

    def test_empty_namespace_rejected(self):
        with pytest.raises(ValueError, match="namespace"):
            QueryBuilderConfig(namespace="  ")

    def test_bool_namespace_rejected(self):
        with pytest.raises(ValueError, match="namespace"):
            QueryBuilderConfig(namespace=True)  # type: ignore[arg-type]

    def test_timeout_too_low(self):
        with pytest.raises(ValueError, match="timeout_ms"):
            QueryBuilderConfig(timeout_ms=50)

    def test_timeout_too_high(self):
        with pytest.raises(ValueError, match="timeout_ms"):
            QueryBuilderConfig(timeout_ms=999_999)

    def test_bool_timeout_rejected(self):
        with pytest.raises(ValueError, match="timeout_ms"):
            QueryBuilderConfig(timeout_ms=True)  # type: ignore[arg-type]

    def test_env_token_preference(self, monkeypatch):
        monkeypatch.setenv("SKETCHLOG_TOKEN", "envtok")
        cfg = QueryBuilderConfig(token="inline")
        assert cfg.resolved_token() == "envtok"

    def test_inline_token_fallback(self, monkeypatch):
        monkeypatch.delenv("SKETCHLOG_TOKEN", raising=False)
        cfg = QueryBuilderConfig(token="inline")
        assert cfg.resolved_token() == "inline"


# ---------------------------------------------------------------------------
# QueryStore
# ---------------------------------------------------------------------------

class TestQueryStore:
    def test_save_and_get(self, tmp_path):
        store = QueryStore(persist_path=str(tmp_path / "qs.json"))
        store.save(SavedQuery(name="q1", sql=_SIMPLE_SQL))
        q = store.get("q1")
        assert q is not None
        assert q.sql == _SIMPLE_SQL

    def test_save_empty_name_raises(self):
        store = QueryStore()
        with pytest.raises(ValueError, match="name"):
            store.save(SavedQuery(name="", sql=_SIMPLE_SQL))

    def test_save_empty_sql_raises(self):
        store = QueryStore()
        with pytest.raises(ValueError, match="SQL"):
            store.save(SavedQuery(name="q1", sql="   "))

    def test_delete_existing(self, tmp_path):
        store = QueryStore(persist_path=str(tmp_path / "qs.json"))
        store.save(SavedQuery(name="q1", sql=_SIMPLE_SQL))
        assert store.delete("q1") is True
        assert store.get("q1") is None

    def test_delete_missing_returns_false(self):
        store = QueryStore()
        assert store.delete("nonexistent") is False

    def test_list_saved_order(self, tmp_path):
        store = QueryStore(persist_path=str(tmp_path / "qs.json"))
        store.save(SavedQuery(name="a", sql=_SIMPLE_SQL))
        time.sleep(0.01)
        store.save(SavedQuery(name="b", sql=_SIMPLE_SQL))
        names = [q.name for q in store.list_saved()]
        assert names[0] == "b"  # most recently updated first

    def test_list_saved_tag_filter(self, tmp_path):
        store = QueryStore(persist_path=str(tmp_path / "qs.json"))
        store.save(SavedQuery(name="a", sql=_SIMPLE_SQL, tags=["prod"]))
        store.save(SavedQuery(name="b", sql=_SIMPLE_SQL, tags=["dev"]))
        names = [q.name for q in store.list_saved(tag="prod")]
        assert names == ["a"]

    def test_history_push_and_list(self, tmp_path):
        store = QueryStore(persist_path=str(tmp_path / "qs.json"))
        store.push_history(HistoryEntry(sql=_SIMPLE_SQL))
        entries = store.list_history()
        assert len(entries) == 1
        assert entries[0].sql == _SIMPLE_SQL

    def test_history_limit(self, tmp_path):
        store = QueryStore(persist_path=str(tmp_path / "qs.json"))
        for i in range(5):
            store.push_history(HistoryEntry(sql=f"SELECT {i} FROM s;"))
        entries = store.list_history(limit=3)
        assert len(entries) == 3

    def test_history_max_cap(self, tmp_path):
        store = QueryStore(persist_path=str(tmp_path / "qs.json"))
        for i in range(250):
            store.push_history(HistoryEntry(sql=f"SELECT {i} FROM s;"))
        assert len(store._history) == 200

    def test_clear_history(self, tmp_path):
        store = QueryStore(persist_path=str(tmp_path / "qs.json"))
        store.push_history(HistoryEntry(sql=_SIMPLE_SQL))
        store.clear_history()
        assert store.list_history() == []

    def test_persist_roundtrip(self, tmp_path):
        path = str(tmp_path / "qs.json")
        store = QueryStore(persist_path=path)
        store.save(SavedQuery(name="q1", sql=_SIMPLE_SQL, description="test"))
        store.push_history(HistoryEntry(sql=_SIMPLE_SQL))
        store2 = QueryStore(persist_path=path)
        assert store2.get("q1") is not None
        assert len(store2.list_history()) == 1

    def test_corrupt_file_ignored(self, tmp_path):
        path = str(tmp_path / "qs.json")
        with open(path, "w") as f:
            f.write("NOT JSON {{{")
        store = QueryStore(persist_path=path)  # must not raise
        assert store.list_saved() == []

    def test_empty_file_ignored(self, tmp_path):
        path = str(tmp_path / "qs.json")
        with open(path, "w") as f:
            f.write("")
        store = QueryStore(persist_path=path)  # must not raise
        assert store.list_saved() == []


# ---------------------------------------------------------------------------
# Example templates
# ---------------------------------------------------------------------------

class TestExampleTemplates:
    def test_templates_non_empty(self):
        assert len(_EXAMPLE_TEMPLATES) >= 4

    def test_templates_have_required_keys(self):
        for t in _EXAMPLE_TEMPLATES:
            assert "name" in t
            assert "sql" in t
            assert "description" in t

    def test_templates_sql_valid(self):
        for t in _EXAMPLE_TEMPLATES:
            result = validate_query(t["sql"])
            assert result.valid, f"Template '{t['name']}' failed validation: {result.errors}"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

class TestCLI:
    def test_complete_command(self, capsys):
        main(["complete", "SEL"])
        out = capsys.readouterr().out
        assert "SELECT" in out

    def test_complete_json(self, capsys):
        main(["--format", "json", "complete", "COUNT"])
        out = capsys.readouterr().out
        data = json.loads(out)
        assert isinstance(data, list)
        assert any("COUNT" in item["label"] for item in data)

    def test_validate_valid(self, capsys):
        main(["validate", _SIMPLE_SQL])
        out = capsys.readouterr().out
        assert "VALID" in out

    def test_validate_invalid_exits_1(self):
        with pytest.raises(SystemExit) as exc:
            main(["validate", "FROM my_stream;"])
        assert exc.value.code == 1

    def test_validate_json(self, capsys):
        main(["--format", "json", "validate", _SIMPLE_SQL])
        out = capsys.readouterr().out
        data = json.loads(out)
        assert "valid" in data

    def test_explain_command(self, capsys):
        main(["explain", _GOOD_SQL])
        out = capsys.readouterr().out
        assert "request_latency" in out

    def test_explain_json(self, capsys):
        main(["--format", "json", "explain", _GOOD_SQL])
        out = capsys.readouterr().out
        data = json.loads(out)
        assert "steps" in data

    def test_explain_invalid_exits_1(self):
        with pytest.raises(SystemExit) as exc:
            main(["explain", "FROM only;"])
        assert exc.value.code == 1

    def test_api_request_curl(self, capsys):
        main(["api-request", _SIMPLE_SQL, "--server-url", "https://localhost:8080"])
        out = capsys.readouterr().out
        assert "curl" in out

    def test_api_request_json(self, capsys):
        main(["api-request", _SIMPLE_SQL, "--server-url", "https://localhost:8080", "--output", "json"])
        out = capsys.readouterr().out
        data = json.loads(out)
        assert data["method"] == "POST"

    def test_api_request_python(self, capsys):
        main(["api-request", _SIMPLE_SQL,
              "--server-url", "https://localhost:8080", "--output", "python"])
        out = capsys.readouterr().out
        assert "urllib.request" in out

    def test_api_request_http_url_exits_2(self):
        with pytest.raises(SystemExit) as exc:
            main(["api-request", _SIMPLE_SQL, "--server-url", "http://bad"])
        assert exc.value.code == 2

    def test_templates_command(self, capsys):
        main(["templates"])
        out = capsys.readouterr().out
        assert "latency" in out.lower() or "SELECT" in out

    def test_templates_json(self, capsys):
        main(["--format", "json", "templates"])
        out = capsys.readouterr().out
        data = json.loads(out)
        assert isinstance(data, list)
        assert len(data) >= 4

    def test_save_and_list(self, tmp_path, capsys):
        store_path = str(tmp_path / "qs.json")
        main(["save", "myq", _SIMPLE_SQL, "--store", store_path])
        main(["list-saved", "--store", store_path])
        out = capsys.readouterr().out
        assert "myq" in out

    def test_history_command(self, tmp_path, capsys):
        store_path = str(tmp_path / "qs.json")
        store = QueryStore(persist_path=store_path)
        store.push_history(HistoryEntry(sql=_SIMPLE_SQL))
        main(["history", "--store", store_path])
        out = capsys.readouterr().out
        assert "SELECT" in out

    def test_read_sql_from_file(self, tmp_path, capsys):
        f = tmp_path / "q.sql"
        f.write_text(_SIMPLE_SQL)
        main(["validate", f"@{f}"])
        out = capsys.readouterr().out
        assert "VALID" in out

    def test_read_sql_file_missing_exits_2(self):
        with pytest.raises(SystemExit) as exc:
            main(["validate", "@/nonexistent/path/q.sql"])
        assert exc.value.code == 2

    def test_no_command_exits_0(self):
        with pytest.raises(SystemExit) as exc:
            main([])
        assert exc.value.code == 0
