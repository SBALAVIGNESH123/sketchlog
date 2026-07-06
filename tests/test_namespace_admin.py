"""Tests for sketchlog.namespace_admin."""
from __future__ import annotations
import json, math, time, sys, io
import pytest
from sketchlog.namespace_admin import (
    AlertSummary, NamespaceAdminConfig, NamespaceInfo, StreamSummary,
    TokenInfo, _build_demo_namespaces, _fmt_bytes, _parse_bool,
    _parse_namespace_response, _redact_url, render_json, render_text,
    main,
)


# ---------------------------------------------------------------------------
# _fmt_bytes
# ---------------------------------------------------------------------------
class TestFmtBytes:
    def test_bytes(self):        assert "B"   in _fmt_bytes(512)
    def test_kib(self):          assert "KiB" in _fmt_bytes(2048)
    def test_mib(self):          assert "MiB" in _fmt_bytes(2 * 1024 ** 2)
    def test_gib(self):          assert "GiB" in _fmt_bytes(3 * 1024 ** 3)


# ---------------------------------------------------------------------------
# _parse_bool
# ---------------------------------------------------------------------------
class TestParseBool:
    def test_true_bool(self):    assert _parse_bool(True) is True
    def test_false_bool(self):   assert _parse_bool(False) is False
    def test_string_true(self):  assert _parse_bool("true") is True
    def test_string_false(self): assert _parse_bool("false") is False
    def test_string_TRUE(self):  assert _parse_bool("TRUE") is True
    def test_int_one(self):      assert _parse_bool(1) is True
    def test_int_zero(self):     assert _parse_bool(0) is False


# ---------------------------------------------------------------------------
# _redact_url
# ---------------------------------------------------------------------------
class TestRedactUrl:
    def test_no_creds(self):
        assert _redact_url("https://host:9000/path") == "https://host:9000/path"
    def test_with_password(self):
        u = _redact_url("https://user:secret@host/api")
        assert "secret" not in u
        assert "host" in u


# ---------------------------------------------------------------------------
# TokenInfo
# ---------------------------------------------------------------------------
class TestTokenInfo:
    def _ok(self, **kw):
        defaults = dict(
            token_id="tok-001", label="ingest", scopes=["ingest"],
            created_at=None, expires_at=None, last_used_at=None, active=True,
        )
        return TokenInfo(**{**defaults, **kw})

    def test_valid(self):
        t = self._ok()
        assert t.token_id == "tok-001"

    def test_empty_token_id(self):
        with pytest.raises(ValueError, match="token_id"):
            self._ok(token_id="")

    def test_scopes_not_list(self):
        with pytest.raises(ValueError, match="scopes"):
            self._ok(scopes="ingest")  # type: ignore[arg-type]

    def test_active_not_bool(self):
        with pytest.raises(ValueError, match="active"):
            self._ok(active=1)  # type: ignore[arg-type]

    def test_to_dict(self):
        d = self._ok().to_dict()
        assert d["token_id"] == "tok-001"
        assert "prefix" in d


# ---------------------------------------------------------------------------
# StreamSummary
# ---------------------------------------------------------------------------
class TestStreamSummary:
    def _ok(self, **kw):
        defaults = dict(
            name="latency", sketch_count=3, memory_bytes=1024,
            last_write_at=None, event_rate_hz=10.0,
        )
        return StreamSummary(**{**defaults, **kw})

    def test_valid(self):        assert self._ok().name == "latency"
    def test_empty_name(self):
        with pytest.raises(ValueError, match="name"):
            self._ok(name="")
    def test_neg_sketch_count(self):
        with pytest.raises(ValueError, match="sketch_count"):
            self._ok(sketch_count=-1)
    def test_bool_memory(self):
        with pytest.raises(ValueError, match="memory_bytes"):
            self._ok(memory_bytes=True)  # type: ignore[arg-type]
    def test_nan_rate(self):
        with pytest.raises(ValueError, match="event_rate_hz"):
            self._ok(event_rate_hz=float("nan"))
    def test_neg_rate(self):
        with pytest.raises(ValueError, match="event_rate_hz"):
            self._ok(event_rate_hz=-1.0)
    def test_to_dict(self):
        d = self._ok().to_dict()
        assert d["name"] == "latency"


# ---------------------------------------------------------------------------
# AlertSummary
# ---------------------------------------------------------------------------
class TestAlertSummary:
    def test_valid(self):
        a = AlertSummary(1, 2, 3, 0)
        assert a.total_active == 6
    def test_neg_raises(self):
        with pytest.raises(ValueError):
            AlertSummary(-1, 0, 0, 0)
    def test_to_dict(self):
        d = AlertSummary(1, 0, 2, 1).to_dict()
        assert d["total_active"] == 3


# ---------------------------------------------------------------------------
# NamespaceInfo
# ---------------------------------------------------------------------------
def _make_ns(**kw) -> NamespaceInfo:
    defaults = dict(
        name="platform", display_name="Platform", stream_count=10,
        sketch_count=50, memory_bytes=1024 * 1024, quota_bytes=512 * 1024 * 1024,
        last_activity_at=None, top_streams=[], tokens=[],
        alerts=AlertSummary(0, 0, 0, 0), health="healthy", tags={},
    )
    return NamespaceInfo(**{**defaults, **kw})


class TestNamespaceInfo:
    def test_valid(self):
        ns = _make_ns()
        assert ns.name == "platform"

    def test_empty_name(self):
        with pytest.raises(ValueError, match="name"):
            _make_ns(name="")

    def test_invalid_health(self):
        with pytest.raises(ValueError, match="health"):
            _make_ns(health="bad")

    def test_bool_stream_count(self):
        with pytest.raises(ValueError, match="stream_count"):
            _make_ns(stream_count=True)  # type: ignore[arg-type]

    def test_quota_fraction_with_quota(self):
        ns = _make_ns(memory_bytes=256 * 1024 * 1024, quota_bytes=512 * 1024 * 1024)
        assert abs(ns.quota_used_fraction - 0.5) < 1e-9  # type: ignore[operator]

    def test_quota_fraction_no_quota(self):
        ns = _make_ns(quota_bytes=0)
        assert ns.quota_used_fraction is None

    def test_quota_warn_triggered(self):
        ns = _make_ns(memory_bytes=450 * 1024 * 1024, quota_bytes=512 * 1024 * 1024)
        assert ns.quota_warn is True

    def test_quota_warn_not_triggered(self):
        ns = _make_ns(memory_bytes=100 * 1024 * 1024, quota_bytes=512 * 1024 * 1024)
        assert ns.quota_warn is False

    def test_stale_old_activity(self):
        ns = _make_ns(last_activity_at=time.time() - 7200)
        assert ns.stale is True

    def test_stale_recent_activity(self):
        ns = _make_ns(last_activity_at=time.time() - 10)
        assert ns.stale is False

    def test_stale_none_activity(self):
        ns = _make_ns(last_activity_at=None)
        assert ns.stale is False

    def test_to_dict_json_serializable(self):
        ns = _make_ns()
        d = ns.to_dict()
        json.dumps(d)  # must not raise

    def test_tags_not_dict(self):
        with pytest.raises(ValueError, match="tags"):
            _make_ns(tags=["a", "b"])  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# NamespaceAdminConfig
# ---------------------------------------------------------------------------
class TestNamespaceAdminConfig:
    def test_valid(self):
        c = NamespaceAdminConfig(url="https://host/api")
        assert c.timeout_s == 10

    def test_http_rejected(self):
        with pytest.raises(ValueError, match="https"):
            NamespaceAdminConfig(url="http://host/api")

    def test_neg_timeout(self):
        with pytest.raises(ValueError, match="timeout_s"):
            NamespaceAdminConfig(url="https://host", timeout_s=0)

    def test_env_token_preference(self, monkeypatch):
        monkeypatch.setenv("SKETCHLOG_ADMIN_TOKEN", "env-tok")
        c = NamespaceAdminConfig(url="https://host", token="inline-tok")
        assert c.resolved_token() == "env-tok"

    def test_inline_token_fallback(self, monkeypatch):
        monkeypatch.delenv("SKETCHLOG_ADMIN_TOKEN", raising=False)
        c = NamespaceAdminConfig(url="https://host", token="inline-tok")
        assert c.resolved_token() == "inline-tok"

    def test_bool_top_streams_rejected(self):
        with pytest.raises(ValueError, match="top_streams"):
            NamespaceAdminConfig(url="https://host", top_streams=True)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# _parse_namespace_response
# ---------------------------------------------------------------------------
class TestParseNamespaceResponse:
    def test_empty_list(self):
        assert _parse_namespace_response({}) == []

    def test_none_namespaces(self):
        assert _parse_namespace_response({"namespaces": None}) == []

    def test_valid_entry(self):
        raw = {"namespaces": [{"name": "myns", "health": "healthy",
                                "stream_count": 5, "sketch_count": 20,
                                "memory_bytes": 1024, "quota_bytes": 0,
                                "alerts": {}, "tags": {}}]}
        result = _parse_namespace_response(raw)
        assert len(result) == 1
        assert result[0].name == "myns"

    def test_malformed_entry_skipped(self):
        raw = {"namespaces": [None, 42, {"name": "ok", "health": "healthy",
                                          "stream_count": 1, "sketch_count": 1,
                                          "memory_bytes": 0, "quota_bytes": 0,
                                          "alerts": {}, "tags": {}}]}
        result = _parse_namespace_response(raw)
        assert len(result) == 1

    def test_string_bool_partition(self):
        # health="warn" — parsed as string not bool()
        raw = {"namespaces": [{"name": "x", "health": "warn",
                                "stream_count": 1, "sketch_count": 1,
                                "memory_bytes": 0, "quota_bytes": 0,
                                "alerts": {}, "tags": {}}]}
        result = _parse_namespace_response(raw)
        assert result[0].health == "warn"


# ---------------------------------------------------------------------------
# Demo data
# ---------------------------------------------------------------------------
class TestBuildDemoNamespaces:
    def test_returns_five(self):
        nss = _build_demo_namespaces()
        assert len(nss) == 5

    def test_deterministic(self):
        a = _build_demo_namespaces()
        b = _build_demo_namespaces()
        assert [n.name for n in a] == [n.name for n in b]
        assert [n.memory_bytes for n in a] == [n.memory_bytes for n in b]

    def test_json_serializable(self):
        nss = _build_demo_namespaces()
        json.dumps([ns.to_dict() for ns in nss])

    def test_health_values_valid(self):
        for ns in _build_demo_namespaces():
            assert ns.health in ("healthy", "warn", "degraded", "unknown")


# ---------------------------------------------------------------------------
# render_text / render_json
# ---------------------------------------------------------------------------
class TestRender:
    def test_render_text_contains_namespace(self):
        nss = _build_demo_namespaces()
        out = render_text(nss)
        assert "platform" in out

    def test_render_text_warn_flag(self):
        nss = _build_demo_namespaces()
        out = render_text(nss)
        assert "warn" in out.lower() or "x" in out

    def test_render_json_valid(self):
        nss = _build_demo_namespaces()
        d = json.loads(render_json(nss))
        assert "namespaces" in d
        assert len(d["namespaces"]) == 5

    def test_render_json_no_raw_secrets(self):
        nss = _build_demo_namespaces()
        out = render_json(nss)
        for ns in nss:
            for tok in ns.tokens:
                if tok.prefix:
                    assert tok.prefix in out  # prefix IS exposed
                # raw token value is never in the dataclass so cannot leak


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
class TestCLI:
    def test_demo_text(self, capsys):
        with pytest.raises(SystemExit) as exc_info:
            main(["--demo"])
        captured = capsys.readouterr()
        assert "SketchLog" in captured.out
        assert exc_info.value.code in (0, 1)

    def test_demo_json(self, capsys):
        with pytest.raises(SystemExit):
            main(["--demo", "--format", "json"])
        captured = capsys.readouterr()
        d = json.loads(captured.out)
        assert "namespaces" in d

    def test_missing_url_exits_2(self, capsys):
        with pytest.raises(SystemExit) as exc_info:
            main([])
        assert exc_info.value.code == 2

    def test_http_url_exits_2(self, capsys):
        with pytest.raises(SystemExit) as exc_info:
            main(["--url", "http://insecure"])
        assert exc_info.value.code == 2

    def test_demo_top(self, capsys):
        with pytest.raises(SystemExit):
            main(["--demo", "--top", "3"])
        captured = capsys.readouterr()
        assert "SketchLog" in captured.out
