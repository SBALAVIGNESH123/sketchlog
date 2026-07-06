"""Tests for sketchlog.alert_manager."""
from __future__ import annotations
import json, math, os, sys, tempfile
from typing import Any
from unittest.mock import patch
import pytest, time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python"))
from sketchlog.alert_manager import (
    Alert, AlertManager, AlertRouter, AlertStatus, AlertStore,
    ChannelConfig, DeliveryEngine, DeliveryRecord, DeliveryStatus,
    Route, Silence, SilenceManager,
    _build_discord_payload, _build_pagerduty_payload,
    _build_slack_payload, _build_webhook_payload,
    _now, main,
)


def _a(**kw: Any) -> Alert:
    d = dict(name="T", namespace="prod", stream="lat", severity="warning")
    return Alert(**{**d, **kw})

def _s(**kw: Any) -> Silence:
    t = _now()
    return Silence(**{**dict(starts_at=t-1, ends_at=t+3600), **kw})

def _c(**kw: Any) -> ChannelConfig:
    return ChannelConfig(**{**dict(name="ops", adapter="webhook",
                                   url="https://example.com/h"), **kw})


class TestAlertValidation:
    def test_valid(self) -> None:           assert _a().namespace == "prod"
    def test_empty_name(self) -> None:
        with pytest.raises(ValueError, match="name"): _a(name="")
    def test_empty_ns(self) -> None:
        with pytest.raises(ValueError, match="namespace"): _a(namespace="")
    def test_bad_severity(self) -> None:
        with pytest.raises(ValueError, match="severity"): _a(severity="x")
    def test_bad_status(self) -> None:
        with pytest.raises(ValueError, match="status"): _a(status="x")
    def test_nan_fired(self) -> None:
        with pytest.raises(ValueError, match="fired_at"): _a(fired_at=math.nan)
    def test_bad_labels(self) -> None:
        with pytest.raises(ValueError, match="labels"): _a(labels="bad")  # type: ignore
    def test_all_severities(self) -> None:
        for s in ("critical","warning","info"): assert _a(severity=s).severity == s
    def test_resolve(self) -> None:
        r = _a().resolve()
        assert r.status == "resolved" and r.resolved_at is not None
    def test_silence_method(self) -> None:
        assert _a().silence().status == "silenced"
    def test_to_dict(self) -> None:
        json.dumps(_a(labels={"e":"p"}).to_dict())


class TestSilenceValidation:
    def test_valid(self) -> None:           assert _s().is_active()
    def test_ends_before_starts(self) -> None:
        t = _now()
        with pytest.raises(ValueError, match="ends_at"):
            Silence(starts_at=t+100, ends_at=t)
    def test_nan_starts(self) -> None:
        with pytest.raises(ValueError, match="starts_at"):
            Silence(starts_at=math.nan, ends_at=_now()+60)
    def test_bad_sev(self) -> None:
        t = _now()
        with pytest.raises(ValueError, match="match_severity"):
            Silence(starts_at=t, ends_at=t+60, match_severity="x")
    def test_inactive(self) -> None:
        t = _now()
        assert not Silence(starts_at=t-100, ends_at=t-1).matches(_a())
    def test_ns_filter(self) -> None:
        s = _s(match_namespace="prod")
        assert s.matches(_a()) and not s.matches(_a(namespace="stg"))
    def test_sev_filter(self) -> None:
        s = _s(match_severity="critical")
        assert s.matches(_a(severity="critical")) and not s.matches(_a())
    def test_label_filter(self) -> None:
        s = _s(match_labels={"t":"b"})
        assert s.matches(_a(labels={"t":"b"})) and not s.matches(_a(labels={}))
    def test_to_dict(self) -> None:
        json.dumps(_s().to_dict())


class TestRouteValidation:
    def test_valid(self) -> None:     assert Route(channels=["ops"]).channels == ["ops"]
    def test_empty_ch(self) -> None:
        with pytest.raises(ValueError, match="channels"): Route(channels=[])
    def test_bad_sev(self) -> None:
        with pytest.raises(ValueError, match="match_severity"):
            Route(channels=["ops"], match_severity="x")
    def test_ns_match(self) -> None:
        r = Route(channels=["ops"], match_namespace="prod")
        assert r.matches(_a()) and not r.matches(_a(namespace="dev"))
    def test_any(self) -> None:
        r = Route(channels=["d"])
        for s in ("critical","warning","info"): assert r.matches(_a(severity=s))
    def test_bad_match_labels(self) -> None:
        with pytest.raises(ValueError, match="match_labels"):
            Route(channels=["ops"], match_labels="not-a-dict")  # type: ignore


class TestChannelConfig:
    def test_valid(self) -> None:     assert _c().adapter == "webhook"
    def test_bad_adapter(self) -> None:
        with pytest.raises(ValueError, match="adapter"): _c(adapter="email")
    def test_bad_url(self) -> None:
        with pytest.raises(ValueError, match="url"): _c(url="ftp://x")
    def test_http_url_rejected(self) -> None:
        with pytest.raises(ValueError, match="url"): _c(url="http://example.com/hook")
    def test_bad_timeout(self) -> None:
        with pytest.raises(ValueError, match="timeout_s"): _c(timeout_s=0)
    def test_token_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MY_TOK","secret")
        assert _c(token="fb", token_env="MY_TOK").resolved_token() == "secret"
    def test_token_fallback(self) -> None:
        assert _c(token="fb", token_env="ABSENT_XYZ").resolved_token() == "fb"
    def test_all_adapters(self) -> None:
        for a in ("slack","discord","pagerduty","opsgenie","webhook"):
            assert _c(adapter=a).adapter == a


class TestPayloads:
    def test_slack_firing(self) -> None:
        p = _build_slack_payload(_a(severity="critical", status="firing"))
        assert "FIRING" in p["text"] and ":red_circle:" in p["text"]
    def test_slack_resolved(self) -> None:
        assert "RESOLVED" in _build_slack_payload(_a(status="resolved"))["text"]
    def test_discord_color(self) -> None:
        assert _build_discord_payload(_a(severity="critical"))["embeds"][0]["color"] == 0xE53935
    def test_discord_fields(self) -> None:
        ns = [f["name"] for f in _build_discord_payload(_a())["embeds"][0]["fields"]]
        assert "Namespace" in ns and "Severity" in ns
    def test_pd_trigger(self) -> None:
        p = _build_pagerduty_payload(_a(), "rk")
        assert p["event_action"] == "trigger" and p["routing_key"] == "rk"
    def test_pd_resolve(self) -> None:
        assert _build_pagerduty_payload(_a(status="resolved"), "rk")["event_action"] == "resolve"
    def test_webhook(self) -> None:
        a = _a(); p = _build_webhook_payload(a)
        json.dumps(p); assert p["alert_id"] == a.alert_id


class TestAlertStore:
    def test_upsert(self) -> None:
        s = AlertStore(); a = _a(); s.upsert(a); assert a in s.list_active()
    def test_resolve_history(self) -> None:
        s = AlertStore(); a = _a(); s.upsert(a); s.upsert(a.resolve())
        assert a not in s.list_active()
        assert any(h.alert_id == a.alert_id for h in s.list_history())
    def test_filter(self) -> None:
        s = AlertStore()
        s.upsert(_a(namespace="prod").resolve()); s.upsert(_a(namespace="stg").resolve())
        assert all(x.namespace == "prod" for x in s.list_history(namespace="prod"))
    def test_limit(self) -> None:
        s = AlertStore()
        for _ in range(10): s.upsert(_a().resolve())
        assert len(s.list_history(limit=3)) == 3
    def test_persist(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tf:
            path = tf.name
        try:
            s1 = AlertStore(persist_path=path); a = _a(); s1.upsert(a)
            s2 = AlertStore(persist_path=path)
            assert any(x.alert_id == a.alert_id for x in s2.list_active())
        finally:
            os.unlink(path)
    def test_get_active(self) -> None:
        s = AlertStore(); a = _a(); s.upsert(a)
        assert s.get_active(a.alert_id) == a
        assert s.get_active("x") is None
    def test_corrupt_persist(self, tmp_path: Any) -> None:
        bad = tmp_path / "corrupt.json"
        bad.write_text("not json at all", encoding="utf-8")
        s = AlertStore(persist_path=str(bad))  # must not raise
        assert s.list_active() == []
    def test_empty_persist(self, tmp_path: Any) -> None:
        empty = tmp_path / "empty.json"
        empty.write_text("", encoding="utf-8")
        s = AlertStore(persist_path=str(empty))  # must not raise
        assert s.list_active() == []


class TestSilenceManager:
    def test_add(self) -> None:
        sm = SilenceManager(); sm.add(_s()); assert sm.is_silenced(_a())
    def test_remove(self) -> None:
        sm = SilenceManager(); s = _s(); sm.add(s); sm.remove(s.silence_id)
        assert not sm.is_silenced(_a())
    def test_expired(self) -> None:
        sm = SilenceManager(); t = _now()
        sm.add(Silence(starts_at=t-100, ends_at=t-1)); assert not sm.is_silenced(_a())
    def test_list_active(self) -> None:
        sm = SilenceManager(); sm.add(_s()); assert len(sm.list_active()) == 1
    def test_ns_mismatch(self) -> None:
        sm = SilenceManager(); sm.add(_s(match_namespace="stg"))
        assert not sm.is_silenced(_a(namespace="prod"))


class TestAlertRouter:
    def test_first_match(self) -> None:
        r = AlertRouter(routes=[Route(channels=["c1"], match_severity="critical"),
                                 Route(channels=["c2"])])
        assert r.resolve_channels(_a(severity="critical")) == ["c1"]
    def test_fallback(self) -> None:
        assert AlertRouter(default_channels=["d"]).resolve_channels(_a()) == ["d"]
    def test_continue(self) -> None:
        r = AlertRouter(routes=[Route(channels=["c1"], match_severity="critical",
                                       continue_matching=True),
                                 Route(channels=["c2"])])
        res = r.resolve_channels(_a(severity="critical"))
        assert "c1" in res and "c2" in res
    def test_no_dup(self) -> None:
        r = AlertRouter(routes=[Route(channels=["ops"], match_severity="critical",
                                       continue_matching=True),
                                 Route(channels=["ops"])])
        assert r.resolve_channels(_a(severity="critical")).count("ops") == 1
    def test_empty(self) -> None:
        assert AlertRouter().resolve_channels(_a()) == []


class TestDeliveryEngine:
    def test_unknown(self) -> None:
        assert DeliveryEngine().deliver(_a(), ["x"])[0].status == DeliveryStatus.FAILED.value
    def test_success(self) -> None:
        c = _c(); eng = DeliveryEngine(channels={c.name: c})
        with patch("sketchlog.alert_manager._http_post"):
            assert eng.deliver(_a(), [c.name])[0].status == DeliveryStatus.SENT.value
    def test_retry(self) -> None:
        c = _c(); eng = DeliveryEngine(channels={c.name: c}); n = {"v": 0}
        def flaky(*_a: Any, **_k: Any) -> None:
            n["v"] += 1
            if n["v"] < 3: raise RuntimeError("transient")
        alert = _a()  # create before patching time
        with patch("sketchlog.alert_manager._http_post", side_effect=flaky):
            with patch("sketchlog.alert_manager.time") as mt:
                mt.perf_counter = time.perf_counter
                mt.sleep = lambda _: None
                mt.time = time.time
                rec = eng.deliver(alert, [c.name])[0]
        assert rec.status == DeliveryStatus.SENT.value and rec.attempts == 3
    def test_all_fail(self) -> None:
        c = _c(); eng = DeliveryEngine(channels={c.name: c})
        alert = _a()  # create before patching time
        with patch("sketchlog.alert_manager._http_post", side_effect=RuntimeError("down")):
            with patch("sketchlog.alert_manager.time") as mt:
                mt.perf_counter = time.perf_counter
                mt.sleep = lambda _: None
                mt.time = time.time
                assert eng.deliver(alert, [c.name])[0].status == DeliveryStatus.FAILED.value
    def test_skip_resolved(self) -> None:
        c = _c(send_resolved=False); eng = DeliveryEngine(channels={c.name: c})
        assert eng.deliver(_a(status="resolved"), [c.name])[0].status == DeliveryStatus.SKIPPED.value


class TestAlertManager:
    def _am(self) -> AlertManager:
        return AlertManager(channels=[_c()], default_channels=["ops"])
    def test_ingest(self) -> None:
        am = self._am(); a = _a()
        with patch("sketchlog.alert_manager._http_post"): am.ingest(a)
        assert any(x.alert_id == a.alert_id for x in am.active_alerts())
    def test_silence(self) -> None:
        am = self._am(); am.add_silence(_s())
        with patch("sketchlog.alert_manager._http_post") as mp:
            assert am.ingest(_a()) == []
        mp.assert_not_called()
    def test_resolve(self) -> None:
        am = self._am(); a = _a()
        with patch("sketchlog.alert_manager._http_post"): am.ingest(a)
        with patch("sketchlog.alert_manager._http_post"):
            assert am.resolve(a.alert_id) is not None
    def test_resolve_unknown(self) -> None:
        assert self._am().resolve("x") is None
    def test_routing(self) -> None:
        cp = _c(name="pd", adapter="pagerduty",
                url="https://events.pagerduty.com/v2/enqueue")
        cs = _c(name="sl", adapter="slack", url="https://hooks.slack.com/x")
        am = AlertManager(channels=[cp, cs],
                          routes=[Route(channels=["pd"], match_severity="critical")],
                          default_channels=["sl"])
        assert am.router.resolve_channels(_a(severity="critical")) == ["pd"]
        assert am.router.resolve_channels(_a(severity="warning")) == ["sl"]
    def test_add_route(self) -> None:
        am = self._am()
        am.add_route(Route(channels=["ops"], match_namespace="prod"))
        assert "ops" in am.router.resolve_channels(_a(namespace="prod"))


class TestCLI:
    def test_help(self) -> None:
        with pytest.raises(SystemExit) as e: main(["--help"])
        assert e.value.code == 0
    def test_json(self, capsys: pytest.CaptureFixture[str]) -> None:
        main(["--format","json"]); d = json.loads(capsys.readouterr().out)
        assert "active_alerts" in d
    def test_text(self, capsys: pytest.CaptureFixture[str]) -> None:
        main(["--format","text"]); assert "Active alerts" in capsys.readouterr().out
    def test_bad_cfg(self, tmp_path: Any) -> None:
        cfg = tmp_path/"b.json"
        cfg.write_text('{"channels":[{"name":1,"adapter":"bad","url":"ftp://x"}]}')
        with pytest.raises(SystemExit) as e: main(["--config",str(cfg)])
        assert e.value.code == 2
    def test_cfg_type_error(self, tmp_path: Any) -> None:
        # channels is a string, not a list — triggers TypeError
        cfg = tmp_path/"t.json"
        cfg.write_text('{"channels": "not-a-list"}')
        with pytest.raises(SystemExit) as e: main(["--config",str(cfg)])
        assert e.value.code == 2
    def test_ingest_type_error(self, tmp_path: Any, capsys: pytest.CaptureFixture[str]) -> None:
        # a non-dict entry in ingest file triggers TypeError — should be skipped, not crash
        af = tmp_path/"t.json"
        af.write_text(json.dumps(["not-a-dict"]))
        main(["--ingest", str(af), "--format", "json"])  # must not raise
        out = json.loads(capsys.readouterr().out)
        assert out["active_alerts"] == []
    def test_ingest(self, tmp_path: Any, capsys: pytest.CaptureFixture[str]) -> None:
        af = tmp_path/"a.json"
        af.write_text(json.dumps([{"name":"A","namespace":"ns","stream":"s","severity":"info"}]))
        main(["--ingest",str(af),"--format","json"])
        assert len(json.loads(capsys.readouterr().out)["active_alerts"]) == 1
    def test_silence(self, tmp_path: Any, capsys: pytest.CaptureFixture[str]) -> None:
        t = _now(); sf = tmp_path/"s.json"
        sf.write_text(json.dumps({"starts_at":t-1,"ends_at":t+3600}))
        main(["--add-silence",str(sf),"--format","json"])
        assert len(json.loads(capsys.readouterr().out)["active_silences"]) == 1
    def test_resolve_warn(self, capsys: pytest.CaptureFixture[str]) -> None:
        main(["--resolve","no-id"]); assert "not found" in capsys.readouterr().err
    def test_bad_ingest_file(self, tmp_path: Any) -> None:
        bad = tmp_path/"b.json"; bad.write_text("not json")
        with pytest.raises(SystemExit) as e: main(["--ingest",str(bad)])
        assert e.value.code == 2
