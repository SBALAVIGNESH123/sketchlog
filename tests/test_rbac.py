"""Tests for sketchlog.rbac — RBAC and audit logging."""
from __future__ import annotations
import json, os, sys, tempfile
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python"))
from sketchlog.rbac import (
    Role, Permission, TokenGrant, RBACConfig, AuditEvent,
    AuditLogger, RBACEnforcer, RBACCheckResult, check_rbac, main,
)


# ── TokenGrant validation ────────────────────────────────────────────────────

class TestTokenGrantValidation:
    def test_valid_admin(self):
        g = TokenGrant("tok1", Role.ADMIN, [])
        assert g.role == Role.ADMIN

    def test_valid_role_string(self):
        g = TokenGrant("tok1", "ingest", ["ns1"])  # type: ignore[arg-type]
        assert g.role == Role.INGEST

    def test_empty_token_id_raises(self):
        with pytest.raises(ValueError, match="token_id"):
            TokenGrant("", Role.READ, [])

    def test_invalid_role_raises(self):
        with pytest.raises(ValueError, match="role"):
            TokenGrant("tok1", "superuser", [])  # type: ignore[arg-type]

    def test_namespaces_not_list_raises(self):
        with pytest.raises(ValueError, match="namespaces"):
            TokenGrant("tok1", Role.READ, "ns1")  # type: ignore[arg-type]

    def test_allows_namespace_wildcard(self):
        g = TokenGrant("tok1", Role.ADMIN, [])
        assert g.allows_namespace("any-namespace")

    def test_allows_namespace_specific(self):
        g = TokenGrant("tok1", Role.READ, ["prod"])
        assert g.allows_namespace("prod")
        assert not g.allows_namespace("staging")

    def test_allows_namespace_star(self):
        g = TokenGrant("tok1", Role.READ, ["*"])
        assert g.allows_namespace("anything")


# ── Permission matrix ────────────────────────────────────────────────────────

class TestPermissionMatrix:
    def test_admin_has_all(self):
        g = TokenGrant("t", Role.ADMIN, [])
        assert g.has_permission(Permission.INGEST)
        assert g.has_permission(Permission.QUERY)
        assert g.has_permission(Permission.ADMIN)

    def test_ingest_only(self):
        g = TokenGrant("t", Role.INGEST, [])
        assert g.has_permission(Permission.INGEST)
        assert not g.has_permission(Permission.QUERY)
        assert not g.has_permission(Permission.ADMIN)

    def test_read_only(self):
        g = TokenGrant("t", Role.READ, [])
        assert g.has_permission(Permission.QUERY)
        assert not g.has_permission(Permission.INGEST)

    def test_read_write(self):
        g = TokenGrant("t", Role.READ_WRITE, [])
        assert g.has_permission(Permission.INGEST)
        assert g.has_permission(Permission.QUERY)
        assert not g.has_permission(Permission.ADMIN)


# ── RBACConfig validation ─────────────────────────────────────────────────────

class TestRBACConfigValidation:
    def test_valid_config(self):
        cfg = RBACConfig(grants=[TokenGrant("t", Role.ADMIN, [])], enabled=True)
        assert cfg.enabled

    def test_grants_not_list_raises(self):
        with pytest.raises(ValueError, match="grants"):
            RBACConfig(grants="bad")  # type: ignore[arg-type]

    def test_token_grant_lookup(self):
        g = TokenGrant("tok-abc", Role.READ, ["prod"])
        cfg = RBACConfig(grants=[g])
        assert cfg.token_grant("tok-abc") is g
        assert cfg.token_grant("unknown") is None


# ── AuditEvent ────────────────────────────────────────────────────────────────

class TestAuditEvent:
    def test_to_dict_keys(self):
        e = AuditEvent(ts=1.0, token_id="t", action="query",
                       namespace="ns", stream="s",
                       result="ALLOW", reason="ok", role="read")
        d = e.to_dict()
        for k in ("ts","token_id","action","namespace","stream","result","reason","role"):
            assert k in d

    def test_to_json_valid(self):
        e = AuditEvent(ts=1.0, token_id="t", action="ingest",
                       namespace="ns", stream="s",
                       result="DENY", reason="no_perm", role="read")
        parsed = json.loads(e.to_json())
        assert parsed["result"] == "DENY"

    def test_hmac_tag_present(self):
        e = AuditEvent(ts=1.0, token_id="t", action="query",
                       namespace="ns", stream="s",
                       result="ALLOW", reason="ok", hmac_tag="abc123")
        assert e.to_dict()["hmac_tag"] == "abc123"


# ── AuditLogger ───────────────────────────────────────────────────────────────

class TestAuditLogger:
    def test_stdout_logging(self, capsys):
        cfg = RBACConfig(grants=[], audit_hmac_key="key123")
        al  = AuditLogger(cfg)
        ev  = al.log("tok", "query", "ns", "stream", "ALLOW", "ok", role="read")
        al.close()
        out = capsys.readouterr().out
        assert '"ALLOW"' in out
        assert ev.hmac_tag is not None

    def test_file_logging(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "audit.jsonl")
            cfg  = RBACConfig(grants=[], audit_file=path, audit_hmac_key="k")
            al   = AuditLogger(cfg)
            al.log("tok", "ingest", "ns", "s", "DENY", "no_perm")
            al.close()
            lines = open(path).readlines()
            assert len(lines) == 1
            d = json.loads(lines[0])
            assert d["result"] == "DENY"

    def test_hmac_computed(self, capsys):
        cfg = RBACConfig(grants=[], audit_hmac_key="secret")
        al  = AuditLogger(cfg)
        ev  = al.log("t", "query", "ns", "s", "ALLOW", "ok")
        al.close()
        assert ev.hmac_tag is not None and len(ev.hmac_tag) == 64

    def test_no_hmac_when_key_empty(self, capsys):
        cfg = RBACConfig(grants=[], audit_hmac_key="")
        al  = AuditLogger(cfg)
        ev  = al.log("t", "query", "ns", "s", "ALLOW", "ok")
        al.close()
        assert ev.hmac_tag is None


# ── RBACEnforcer ──────────────────────────────────────────────────────────────

class TestRBACEnforcer:
    def _cfg(self, enabled=True):
        return RBACConfig(
            grants=[
                TokenGrant("admin",   Role.ADMIN,      []),
                TokenGrant("ingester",Role.INGEST,     ["prod"]),
                TokenGrant("reader",  Role.READ,       ["prod"]),
                TokenGrant("rw",      Role.READ_WRITE, ["prod","staging"]),
            ],
            audit_hmac_key="test-key",
            enabled=enabled,
        )

    def test_admin_can_ingest(self, capsys):
        e = RBACEnforcer(self._cfg())
        assert e.check("admin", Permission.INGEST, "any-ns") is True
        e.close()

    def test_admin_can_query(self, capsys):
        e = RBACEnforcer(self._cfg())
        assert e.check("admin", Permission.QUERY, "any-ns") is True
        e.close()

    def test_reader_can_query(self, capsys):
        e = RBACEnforcer(self._cfg())
        assert e.check("reader", Permission.QUERY, "prod") is True
        e.close()

    def test_reader_cannot_ingest(self, capsys):
        e = RBACEnforcer(self._cfg())
        assert e.check("reader", Permission.INGEST, "prod") is False
        e.close()

    def test_ingester_cannot_query(self, capsys):
        e = RBACEnforcer(self._cfg())
        assert e.check("ingester", Permission.QUERY, "prod") is False
        e.close()

    def test_namespace_not_allowed(self, capsys):
        e = RBACEnforcer(self._cfg())
        assert e.check("ingester", Permission.INGEST, "staging") is False
        e.close()

    def test_unknown_token_denied(self, capsys):
        e = RBACEnforcer(self._cfg())
        assert e.check("ghost-token", Permission.QUERY, "prod") is False
        e.close()

    def test_rbac_disabled_allows_all(self, capsys):
        e = RBACEnforcer(self._cfg(enabled=False))
        assert e.check("ghost", Permission.ADMIN, "ns") is True
        e.close()

    def test_audit_event_emitted(self, capsys):
        e = RBACEnforcer(self._cfg())
        e.check("reader", Permission.QUERY, "prod", "stream-1")
        e.close()
        out = capsys.readouterr().out
        assert "ALLOW" in out
        assert "reader" in out


# ── check_rbac ────────────────────────────────────────────────────────────────

class TestCheckRBAC:
    def test_full_config_passes(self):
        cfg = RBACConfig(
            grants=[TokenGrant("admin", Role.ADMIN, [])],
            audit_hmac_key="key",
            enabled=True,
        )
        r = check_rbac(cfg)
        assert r.status == "PASS"
        assert r.grants_count == 1
        assert r.hmac_enabled is True

    def test_no_hmac_warns(self):
        cfg = RBACConfig(
            grants=[TokenGrant("admin", Role.ADMIN, [])],
            audit_hmac_key="",
            enabled=True,
        )
        r = check_rbac(cfg)
        assert r.status in ("PASS", "WARN")
        assert any("hmac" in i.lower() for i in r.issues)

    def test_disabled_rbac_fails(self):
        cfg = RBACConfig(grants=[], enabled=False)
        r = check_rbac(cfg)
        assert r.status in ("WARN", "FAIL")

    def test_no_grants_fails(self):
        cfg = RBACConfig(grants=[], enabled=True)
        r = check_rbac(cfg)
        assert r.status in ("WARN", "FAIL")

    def test_to_dict_json_serializable(self):
        cfg = RBACConfig(grants=[TokenGrant("t", Role.READ, [])], enabled=True)
        r = check_rbac(cfg)
        json.dumps(r.to_dict())  # must not raise


# ── CLI ───────────────────────────────────────────────────────────────────────

class TestCLI:
    def test_demo_text(self, capsys):
        rc = main(["--demo", "--format", "text"])
        assert rc in (0, 1)
        out = capsys.readouterr().out
        assert "Result:" in out

    def test_demo_json(self, capsys):
        rc = main(["--demo", "--format", "json"])
        out = capsys.readouterr().out
        d = json.loads(out)
        assert "status" in d
        assert rc in (0, 1)

    def test_config_file(self, capsys):
        with tempfile.TemporaryDirectory() as td:
            cfg_path = os.path.join(td, "rbac.json")
            with open(cfg_path, "w") as f:
                json.dump({
                    "grants": [{"token_id": "t1", "role": "admin", "namespaces": []}],
                    "audit_hmac_key": "test",
                    "enabled": True,
                }, f)
            rc = main(["--config", cfg_path, "--format", "json"])
            out = capsys.readouterr().out
            d = json.loads(out)
            assert d["grants_count"] == 1

    def test_bad_config_exits_2(self, capsys):
        with tempfile.TemporaryDirectory() as td:
            bad = os.path.join(td, "bad.json")
            with open(bad, "w") as f:
                f.write("[1,2,3]")
            rc = main(["--config", bad])
            assert rc == 2
