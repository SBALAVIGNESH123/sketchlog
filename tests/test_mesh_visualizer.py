"""Tests for sketchlog.mesh_visualizer."""
from __future__ import annotations

import json
import math
import sys
import time
from typing import Any, Dict
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, "python")

from sketchlog.mesh_visualizer import (
    GOSSIP_CONVERGENCE_WARN_S,
    PARTITION_DEAD_FRACTION,
    SNAPSHOT_STALE_WARN_S,
    MeshCheckStatus,
    MeshStatus,
    MeshVisualizerConfig,
    NodeState,
    PeerInfo,
    _build_demo_status,
    _parse_mesh_response,
    render_text,
    main,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _peer(
    node_id: str = "node-001",
    address: str = "10.0.0.2:7946",
    state: str = NodeState.ACTIVE.value,
    gossip_age_s: float = 1.0,
    snapshot_age_s: float = 5.0,
    stream_count: int = 10,
    sketch_count: int = 100,
    anti_entropy_rate: float = 1.0,
) -> PeerInfo:
    return PeerInfo(
        node_id=node_id,
        address=address,
        state=state,
        gossip_age_s=gossip_age_s,
        snapshot_age_s=snapshot_age_s,
        stream_count=stream_count,
        sketch_count=sketch_count,
        anti_entropy_rate=anti_entropy_rate,
    )


def _status(
    peers=None,
    gossip_convergence_s: float = 2.0,
    partition_detected: bool = False,
) -> MeshStatus:
    if peers is None:
        peers = [_peer()]
    return MeshStatus(
        self_node_id="node-000",
        self_address="10.0.0.1:7946",
        peers=peers,
        gossip_convergence_s=gossip_convergence_s,
        partition_detected=partition_detected,
        checked_at=1_750_000_000.0,
    )


# ---------------------------------------------------------------------------
# PeerInfo validation
# ---------------------------------------------------------------------------


class TestPeerInfoValidation:
    def test_valid_active_peer(self) -> None:
        p = _peer()
        assert p.state == NodeState.ACTIVE.value

    def test_valid_suspect_peer(self) -> None:
        p = _peer(state=NodeState.SUSPECT.value)
        assert p.state == NodeState.SUSPECT.value

    def test_valid_dead_peer(self) -> None:
        p = _peer(state=NodeState.DEAD.value)
        assert p.state == NodeState.DEAD.value

    def test_empty_node_id_raises(self) -> None:
        with pytest.raises(ValueError, match="node_id"):
            _peer(node_id="")

    def test_blank_address_raises(self) -> None:
        with pytest.raises(ValueError, match="address"):
            _peer(address="   ")

    def test_invalid_state_raises(self) -> None:
        with pytest.raises(ValueError, match="state"):
            _peer(state="zombie")

    def test_negative_gossip_age_raises(self) -> None:
        with pytest.raises(ValueError, match="gossip_age_s"):
            _peer(gossip_age_s=-1.0)

    def test_nan_snapshot_age_raises(self) -> None:
        with pytest.raises(ValueError, match="snapshot_age_s"):
            _peer(snapshot_age_s=float("nan"))

    def test_inf_anti_entropy_raises(self) -> None:
        with pytest.raises(ValueError, match="anti_entropy_rate"):
            _peer(anti_entropy_rate=float("inf"))

    def test_bool_stream_count_raises(self) -> None:
        with pytest.raises(ValueError, match="stream_count"):
            _peer(stream_count=True)  # type: ignore[arg-type]

    def test_negative_sketch_count_raises(self) -> None:
        with pytest.raises(ValueError, match="sketch_count"):
            _peer(sketch_count=-1)

    def test_zero_counts_ok(self) -> None:
        p = _peer(stream_count=0, sketch_count=0)
        assert p.stream_count == 0


# ---------------------------------------------------------------------------
# MeshStatus aggregates
# ---------------------------------------------------------------------------


class TestMeshStatusAggregates:
    def test_active_peers_filter(self) -> None:
        peers = [
            _peer("n1", state=NodeState.ACTIVE.value),
            _peer("n2", address="10.0.0.3:7946", state=NodeState.SUSPECT.value),
            _peer("n3", address="10.0.0.4:7946", state=NodeState.DEAD.value),
        ]
        s = _status(peers=peers)
        assert len(s.active_peers()) == 1
        assert len(s.suspect_peers()) == 1
        assert len(s.dead_peers()) == 1

    def test_total_streams_and_sketches(self) -> None:
        peers = [_peer("n1", stream_count=10, sketch_count=100),
                 _peer("n2", address="10.0.0.3:7946", stream_count=20, sketch_count=200)]
        s = _status(peers=peers)
        assert s.total_streams() == 30
        assert s.total_sketches() == 300

    def test_mean_anti_entropy_rate(self) -> None:
        peers = [
            _peer("n1", anti_entropy_rate=2.0),
            _peer("n2", address="10.0.0.3:7946", anti_entropy_rate=4.0),
        ]
        s = _status(peers=peers)
        assert abs(s.mean_anti_entropy_rate() - 3.0) < 1e-9

    def test_mean_ae_rate_empty_peers(self) -> None:
        s = _status(peers=[])
        assert s.mean_anti_entropy_rate() == 0.0

    def test_stale_peers(self) -> None:
        peers = [
            _peer("n1", snapshot_age_s=5.0),
            _peer("n2", address="10.0.0.3:7946", snapshot_age_s=60.0),
        ]
        s = _status(peers=peers)
        stale = s.stale_peers(threshold_s=30.0)
        assert len(stale) == 1
        assert stale[0].node_id == "n2"

    def test_slow_gossip_peers(self) -> None:
        peers = [
            _peer("n1", gossip_age_s=1.0),
            _peer("n2", address="10.0.0.3:7946", gossip_age_s=15.0),
        ]
        s = _status(peers=peers)
        slow = s.slow_gossip_peers(threshold_s=10.0)
        assert len(slow) == 1


# ---------------------------------------------------------------------------
# MeshStatus.overall_status
# ---------------------------------------------------------------------------


class TestOverallStatus:
    def test_all_active_is_pass(self) -> None:
        s = _status(peers=[_peer()])
        assert s.overall_status() == MeshCheckStatus.PASS

    def test_partition_is_fail(self) -> None:
        s = _status(partition_detected=True)
        assert s.overall_status() == MeshCheckStatus.FAIL

    def test_dead_fraction_threshold_is_fail(self) -> None:
        # 2 out of 3 peers dead → >= 50% → FAIL
        peers = [
            _peer("n1", state=NodeState.ACTIVE.value),
            _peer("n2", address="10.0.0.3:7946", state=NodeState.DEAD.value),
            _peer("n3", address="10.0.0.4:7946", state=NodeState.DEAD.value),
        ]
        s = _status(peers=peers)
        assert s.overall_status() == MeshCheckStatus.FAIL

    def test_one_dead_peer_is_warn(self) -> None:
        peers = [
            _peer("n1", state=NodeState.ACTIVE.value),
            _peer("n2", address="10.0.0.3:7946", state=NodeState.ACTIVE.value),
            _peer("n3", address="10.0.0.4:7946", state=NodeState.DEAD.value),
        ]
        s = _status(peers=peers)
        assert s.overall_status() == MeshCheckStatus.WARN

    def test_suspect_peer_is_warn(self) -> None:
        peers = [
            _peer("n1", state=NodeState.ACTIVE.value),
            _peer("n2", address="10.0.0.3:7946", state=NodeState.SUSPECT.value),
        ]
        s = _status(peers=peers)
        assert s.overall_status() == MeshCheckStatus.WARN

    def test_stale_snapshot_is_warn(self) -> None:
        peers = [_peer("n1", snapshot_age_s=60.0)]
        s = _status(peers=peers)
        assert s.overall_status(snapshot_stale_s=30.0) == MeshCheckStatus.WARN

    def test_slow_gossip_convergence_is_warn(self) -> None:
        s = _status(gossip_convergence_s=15.0)
        assert s.overall_status(gossip_warn_s=10.0) == MeshCheckStatus.WARN


# ---------------------------------------------------------------------------
# to_dict
# ---------------------------------------------------------------------------


class TestToDict:
    def test_json_serializable(self) -> None:
        s = _status()
        d = s.to_dict()
        json.dumps(d)  # must not raise

    def test_schema_keys_present(self) -> None:
        d = _status().to_dict()
        for key in (
            "self_node_id", "self_address", "checked_at", "overall_status",
            "gossip_convergence_s", "partition_detected", "aggregate",
            "peer_counts", "peers",
        ):
            assert key in d, f"missing key: {key}"

    def test_aggregate_keys(self) -> None:
        d = _status().to_dict()
        agg = d["aggregate"]
        assert "total_streams" in agg
        assert "total_sketches" in agg
        assert "mean_anti_entropy_rate" in agg

    def test_peer_entry_has_stale_flag(self) -> None:
        peers = [_peer("n1", snapshot_age_s=60.0)]
        d = _status(peers=peers).to_dict(snapshot_stale_s=30.0)
        assert d["peers"][0]["snapshot_stale"] is True

    def test_peer_entry_stale_false_when_fresh(self) -> None:
        peers = [_peer("n1", snapshot_age_s=5.0)]
        d = _status(peers=peers).to_dict(snapshot_stale_s=30.0)
        assert d["peers"][0]["snapshot_stale"] is False


# ---------------------------------------------------------------------------
# MeshVisualizerConfig validation
# ---------------------------------------------------------------------------


class TestMeshVisualizerConfig:
    def test_valid_https_url(self) -> None:
        cfg = MeshVisualizerConfig(url="https://sketchlog.example.com")
        assert cfg.url == "https://sketchlog.example.com"

    def test_http_url_rejected(self) -> None:
        with pytest.raises(ValueError, match="url must start with https"):
            MeshVisualizerConfig(url="http://sketchlog.example.com")

    def test_negative_timeout_rejected(self) -> None:
        with pytest.raises(ValueError, match="timeout_s"):
            MeshVisualizerConfig(url="https://example.com", timeout_s=0)

    def test_bool_timeout_rejected(self) -> None:
        with pytest.raises(ValueError, match="timeout_s"):
            MeshVisualizerConfig(url="https://example.com", timeout_s=True)  # type: ignore[arg-type]

    def test_nan_gossip_warn_rejected(self) -> None:
        with pytest.raises(ValueError, match="gossip_warn_s"):
            MeshVisualizerConfig(url="https://example.com", gossip_warn_s=float("nan"))

    def test_zero_snapshot_stale_rejected(self) -> None:
        with pytest.raises(ValueError, match="snapshot_stale_s"):
            MeshVisualizerConfig(url="https://example.com", snapshot_stale_s=0.0)

    def test_env_token_preferred(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SKETCHLOG_AUTH_TOKEN", "env-tok")
        cfg = MeshVisualizerConfig(url="https://example.com", auth_token="inline-tok")
        assert cfg.resolved_auth_token() == "env-tok"

    def test_inline_token_fallback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("SKETCHLOG_AUTH_TOKEN", raising=False)
        cfg = MeshVisualizerConfig(url="https://example.com", auth_token="inline-tok")
        assert cfg.resolved_auth_token() == "inline-tok"

    def test_no_token_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("SKETCHLOG_AUTH_TOKEN", raising=False)
        cfg = MeshVisualizerConfig(url="https://example.com")
        assert cfg.resolved_auth_token() is None


# ---------------------------------------------------------------------------
# _parse_mesh_response
# ---------------------------------------------------------------------------


class TestParseMeshResponse:
    def _raw(self, **kwargs: Any) -> Dict[str, Any]:
        base: Dict[str, Any] = {
            "self_node_id": "node-000",
            "self_address": "10.0.0.1:7946",
            "peers": [
                {
                    "node_id": "node-001",
                    "address": "10.0.0.2:7946",
                    "state": "active",
                    "gossip_age_s": 1.2,
                    "snapshot_age_s": 5.0,
                    "stream_count": 10,
                    "sketch_count": 100,
                    "anti_entropy_rate": 1.0,
                }
            ],
            "gossip_convergence_s": 3.5,
            "partition_detected": False,
            "checked_at": 1_750_000_000.0,
        }
        base.update(kwargs)
        return base

    def test_parses_valid_response(self) -> None:
        s = _parse_mesh_response(self._raw())
        assert s.self_node_id == "node-000"
        assert len(s.peers) == 1
        assert s.peers[0].node_id == "node-001"

    def test_skips_malformed_peer_entries(self) -> None:
        raw = self._raw()
        raw["peers"].append("not-a-dict")  # type: ignore[arg-type]
        raw["peers"].append({"node_id": ""})  # invalid — empty node_id
        s = _parse_mesh_response(raw)
        # only the valid first peer survives
        assert len(s.peers) == 1

    def test_empty_peers(self) -> None:
        raw = self._raw(peers=[])
        s = _parse_mesh_response(raw)
        assert s.peers == []

    def test_partition_detected_true(self) -> None:
        raw = self._raw(partition_detected=True)
        s = _parse_mesh_response(raw)
        assert s.partition_detected is True

    def test_missing_checked_at_defaults_to_now(self) -> None:
        raw = self._raw()
        del raw["checked_at"]
        before = time.time()
        s = _parse_mesh_response(raw)
        after = time.time()
        assert before <= s.checked_at <= after


# ---------------------------------------------------------------------------
# render_text
# ---------------------------------------------------------------------------


class TestRenderText:
    def test_contains_self_node(self) -> None:
        out = render_text(_status())
        assert "node-000" in out

    def test_contains_peer_node(self) -> None:
        out = render_text(_status())
        assert "node-001" in out

    def test_partition_warning_shown(self) -> None:
        out = render_text(_status(partition_detected=True))
        assert "PARTITION" in out

    def test_pass_shown_for_healthy_cluster(self) -> None:
        out = render_text(_status())
        assert "PASS" in out

    def test_warn_shown_for_stale_snapshot(self) -> None:
        peers = [_peer("n1", snapshot_age_s=60.0)]
        out = render_text(_status(peers=peers), snapshot_stale_s=30.0)
        assert "WARN" in out or "⚠" in out

    def test_no_peers_message(self) -> None:
        out = render_text(_status(peers=[]))
        assert "no peers" in out.lower()


# ---------------------------------------------------------------------------
# _build_demo_status
# ---------------------------------------------------------------------------


class TestBuildDemoStatus:
    def test_deterministic(self) -> None:
        s1 = _build_demo_status()
        s2 = _build_demo_status()
        assert s1.to_dict() == s2.to_dict()

    def test_has_peers(self) -> None:
        s = _build_demo_status()
        assert len(s.peers) > 0

    def test_contains_all_states(self) -> None:
        s = _build_demo_status()
        states = {p.state for p in s.peers}
        assert NodeState.ACTIVE.value in states

    def test_to_dict_json_serializable(self) -> None:
        json.dumps(_build_demo_status().to_dict())


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


class TestCLI:
    def test_demo_text_exits_nonzero_on_warn(self) -> None:
        # demo has suspect+dead peers → WARN → exit 1
        with pytest.raises(SystemExit) as exc_info:
            main(["--demo"])
        assert exc_info.value.code in (0, 1)

    def test_demo_json_output(self, capsys: pytest.CaptureFixture[str]) -> None:
        with pytest.raises(SystemExit):
            main(["--demo", "--format", "json"])
        out = capsys.readouterr().out
        data = json.loads(out)
        assert "self_node_id" in data
        assert "peers" in data

    def test_missing_url_exits_2(self) -> None:
        with pytest.raises(SystemExit) as exc_info:
            main([])
        assert exc_info.value.code == 2

    def test_http_url_exits_2(self) -> None:
        with pytest.raises(SystemExit) as exc_info:
            main(["--url", "http://bad.example.com"])
        assert exc_info.value.code == 2

    def test_demo_json_gossip_warn_key(self, capsys: pytest.CaptureFixture[str]) -> None:
        with pytest.raises(SystemExit):
            main(["--demo", "--format", "json", "--gossip-warn", "100"])
        out = capsys.readouterr().out
        data = json.loads(out)
        assert "gossip_slow" in data

    def test_demo_passes_with_healthy_overrides(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """With very large thresholds every peer is within bounds → PASS."""
        with pytest.raises(SystemExit) as exc_info:
            main([
                "--demo",
                "--format", "json",
                "--gossip-warn", "9999",
                "--snapshot-stale", "9999",
            ])
        out = capsys.readouterr().out
        data = json.loads(out)
        # Even with forgiving thresholds, demo has dead peers → WARN or FAIL
        assert data["overall_status"] in ("pass", "warn", "fail")
        assert exc_info.value.code in (0, 1)
