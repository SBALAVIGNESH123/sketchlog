"""
sketchlog.mesh_visualizer
~~~~~~~~~~~~~~~~~~~~~~~~~
Sketch Mesh cluster visualizer: node membership, gossip health,
sync lag, anti-entropy rate, and peer snapshot freshness.

Stdlib-only.  No external dependencies.
"""
from __future__ import annotations

import argparse
import dataclasses
import enum
import json
import math
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Sequence

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

GOSSIP_CONVERGENCE_WARN_S: float = 10.0   # warn if gossip convergence > this
SNAPSHOT_STALE_WARN_S: float = 30.0       # warn if snapshot older than this
PARTITION_DEAD_FRACTION: float = 0.5      # warn partition if dead >= this fraction
_DEFAULT_TIMEOUT_S: int = 10

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class NodeState(str, enum.Enum):
    ACTIVE = "active"
    SUSPECT = "suspect"
    DEAD = "dead"


class MeshCheckStatus(str, enum.Enum):
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"


# ---------------------------------------------------------------------------
# PeerInfo
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class PeerInfo:
    """Immutable snapshot of one peer node's state."""

    node_id: str
    address: str
    state: str                  # NodeState value
    gossip_age_s: float         # seconds since last heard gossip from this peer
    snapshot_age_s: float       # seconds since last snapshot sync with this peer
    stream_count: int           # number of streams this peer hosts
    sketch_count: int           # total sketch objects on this peer
    anti_entropy_rate: float    # anti-entropy syncs per second (rolling)

    def __post_init__(self) -> None:
        errors: List[str] = []
        if not isinstance(self.node_id, str) or not self.node_id.strip():
            errors.append("node_id must be a non-empty string")
        if not isinstance(self.address, str) or not self.address.strip():
            errors.append("address must be a non-empty string")
        valid_states = {s.value for s in NodeState}
        if self.state not in valid_states:
            errors.append(
                f"state must be one of {sorted(valid_states)}; got {self.state!r}"
            )
        for fname, val in [
            ("gossip_age_s", self.gossip_age_s),
            ("snapshot_age_s", self.snapshot_age_s),
            ("anti_entropy_rate", self.anti_entropy_rate),
        ]:
            if (
                isinstance(val, bool)
                or not isinstance(val, (int, float))
                or not math.isfinite(val)
                or val < 0.0
            ):
                errors.append(
                    f"{fname} must be a finite non-negative number; got {val!r}"
                )
        for fname, val in [
            ("stream_count", self.stream_count),
            ("sketch_count", self.sketch_count),
        ]:
            if isinstance(val, bool) or not isinstance(val, int) or val < 0:
                errors.append(
                    f"{fname} must be a non-negative integer; got {val!r}"
                )
        if errors:
            raise ValueError(
                "PeerInfo validation errors:\n  " + "\n  ".join(errors)
            )


# ---------------------------------------------------------------------------
# MeshStatus
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class MeshStatus:
    """Full snapshot of the local node's view of the Sketch Mesh cluster."""

    self_node_id: str
    self_address: str
    peers: List[PeerInfo]
    gossip_convergence_s: float     # estimated convergence time across the cluster
    partition_detected: bool        # True if a network partition is suspected
    checked_at: float               # unix timestamp of the snapshot

    # ------------------------------------------------------------------
    # Filtered views
    # ------------------------------------------------------------------

    def active_peers(self) -> List[PeerInfo]:
        return [p for p in self.peers if p.state == NodeState.ACTIVE.value]

    def suspect_peers(self) -> List[PeerInfo]:
        return [p for p in self.peers if p.state == NodeState.SUSPECT.value]

    def dead_peers(self) -> List[PeerInfo]:
        return [p for p in self.peers if p.state == NodeState.DEAD.value]

    def stale_peers(
        self, threshold_s: float = SNAPSHOT_STALE_WARN_S
    ) -> List[PeerInfo]:
        return [p for p in self.peers if p.snapshot_age_s > threshold_s]

    def slow_gossip_peers(
        self, threshold_s: float = GOSSIP_CONVERGENCE_WARN_S
    ) -> List[PeerInfo]:
        return [p for p in self.peers if p.gossip_age_s > threshold_s]

    # ------------------------------------------------------------------
    # Aggregate metrics
    # ------------------------------------------------------------------

    def total_streams(self) -> int:
        return sum(p.stream_count for p in self.peers)

    def total_sketches(self) -> int:
        return sum(p.sketch_count for p in self.peers)

    def mean_anti_entropy_rate(self) -> float:
        if not self.peers:
            return 0.0
        return sum(p.anti_entropy_rate for p in self.peers) / len(self.peers)

    # ------------------------------------------------------------------
    # Overall status
    # ------------------------------------------------------------------

    def overall_status(
        self,
        gossip_warn_s: float = GOSSIP_CONVERGENCE_WARN_S,
        snapshot_stale_s: float = SNAPSHOT_STALE_WARN_S,
    ) -> MeshCheckStatus:
        if self.partition_detected:
            return MeshCheckStatus.FAIL
        n = len(self.peers)
        if n > 0 and len(self.dead_peers()) / n >= PARTITION_DEAD_FRACTION:
            return MeshCheckStatus.FAIL
        if (
            self.dead_peers()
            or self.suspect_peers()
            or self.stale_peers(snapshot_stale_s)
            or self.gossip_convergence_s > gossip_warn_s
        ):
            return MeshCheckStatus.WARN
        return MeshCheckStatus.PASS

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(
        self,
        gossip_warn_s: float = GOSSIP_CONVERGENCE_WARN_S,
        snapshot_stale_s: float = SNAPSHOT_STALE_WARN_S,
    ) -> Dict[str, Any]:
        return {
            "self_node_id": self.self_node_id,
            "self_address": self.self_address,
            "checked_at": self.checked_at,
            "overall_status": self.overall_status(gossip_warn_s, snapshot_stale_s).value,
            "gossip_convergence_s": round(self.gossip_convergence_s, 3),
            "gossip_slow": self.gossip_convergence_s > gossip_warn_s,
            "partition_detected": self.partition_detected,
            "aggregate": {
                "total_streams": self.total_streams(),
                "total_sketches": self.total_sketches(),
                "mean_anti_entropy_rate": round(self.mean_anti_entropy_rate(), 4),
            },
            "peer_counts": {
                "active": len(self.active_peers()),
                "suspect": len(self.suspect_peers()),
                "dead": len(self.dead_peers()),
                "total": len(self.peers),
            },
            "peers": [
                {
                    "node_id": p.node_id,
                    "address": p.address,
                    "state": p.state,
                    "gossip_age_s": round(p.gossip_age_s, 3),
                    "snapshot_age_s": round(p.snapshot_age_s, 3),
                    "snapshot_stale": p.snapshot_age_s > snapshot_stale_s,
                    "stream_count": p.stream_count,
                    "sketch_count": p.sketch_count,
                    "anti_entropy_rate": round(p.anti_entropy_rate, 4),
                }
                for p in self.peers
            ],
        }


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class MeshVisualizerConfig:
    """Configuration for fetching mesh status from a live server."""

    url: str
    timeout_s: int = _DEFAULT_TIMEOUT_S
    gossip_warn_s: float = GOSSIP_CONVERGENCE_WARN_S
    snapshot_stale_s: float = SNAPSHOT_STALE_WARN_S
    auth_token: Optional[str] = None

    def __post_init__(self) -> None:
        errors: List[str] = []
        if not isinstance(self.url, str) or not self.url.startswith("https://"):
            errors.append("url must start with https://")
        if (
            isinstance(self.timeout_s, bool)
            or not isinstance(self.timeout_s, int)
            or self.timeout_s < 1
        ):
            errors.append("timeout_s must be a positive integer")
        for fname, val in [
            ("gossip_warn_s", self.gossip_warn_s),
            ("snapshot_stale_s", self.snapshot_stale_s),
        ]:
            if (
                isinstance(val, bool)
                or not isinstance(val, (int, float))
                or not math.isfinite(val)
                or val <= 0.0
            ):
                errors.append(
                    f"{fname} must be a positive finite number; got {val!r}"
                )
        if errors:
            raise ValueError(
                "MeshVisualizerConfig validation errors:\n  "
                + "\n  ".join(errors)
            )

    def resolved_auth_token(self) -> Optional[str]:
        return os.environ.get("SKETCHLOG_AUTH_TOKEN") or self.auth_token


# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------


def _redact_url(url: str) -> str:
    """Redact userinfo (password) from a URL for safe error messages."""
    try:
        parsed = urllib.parse.urlparse(url)
        if parsed.password:
            host = parsed.hostname or ""
            port_part = f":{parsed.port}" if parsed.port else ""
            return urllib.parse.urlunparse(
                parsed._replace(netloc=f"{host}{port_part}")
            )
    except Exception:
        pass
    return url


def _parse_bool_field(val: object) -> bool:
    """Parse a boolean field from an API response correctly.

    Handles native booleans, integers, and avoids the pitfall where
    bool("false") == True because any non-empty string is truthy.
    """
    if isinstance(val, bool):
        return val
    if isinstance(val, str):
        return val.lower() == "true"
    return bool(val)


# ---------------------------------------------------------------------------
# Fetch from live server
# ---------------------------------------------------------------------------


def _fetch_mesh_status(config: MeshVisualizerConfig) -> MeshStatus:
    """GET /api/mesh/status from a live SketchLog server."""
    url = config.url.rstrip("/") + "/api/mesh/status"
    headers: Dict[str, str] = {"Accept": "application/json"}
    token = config.resolved_auth_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)  # nosec B310
    try:
        with urllib.request.urlopen(req, timeout=config.timeout_s) as resp:  # nosec B310
            raw: Dict[str, Any] = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read(300).decode("utf-8", errors="replace")
        raise RuntimeError(
            f"HTTP {exc.code} from {_redact_url(url)}: {body}"
        ) from exc
    return _parse_mesh_response(raw)


# ---------------------------------------------------------------------------
# Parse
# ---------------------------------------------------------------------------


def _parse_mesh_response(raw: Dict[str, Any]) -> MeshStatus:
    """Parse a raw API response dict into a typed MeshStatus."""
    peers: List[PeerInfo] = []
    # Normalise non-list peers payload (e.g. null/None from some servers)
    raw_peers = raw.get("peers", [])
    if not isinstance(raw_peers, list):
        raw_peers = []
    for p in raw_peers:
        if not isinstance(p, dict):
            continue
        try:
            peers.append(
                PeerInfo(
                    node_id=str(p.get("node_id", "")),
                    address=str(p.get("address", "")),
                    state=str(p.get("state", NodeState.ACTIVE.value)),
                    gossip_age_s=float(p.get("gossip_age_s", 0.0)),
                    snapshot_age_s=float(p.get("snapshot_age_s", 0.0)),
                    stream_count=int(p.get("stream_count", 0)),
                    sketch_count=int(p.get("sketch_count", 0)),
                    anti_entropy_rate=float(p.get("anti_entropy_rate", 0.0)),
                )
            )
        except (ValueError, TypeError):
            # Skip malformed peer entries; do not abort the whole parse
            continue
    return MeshStatus(
        self_node_id=str(raw.get("self_node_id", "unknown")),
        self_address=str(raw.get("self_address", "unknown")),
        peers=peers,
        gossip_convergence_s=float(raw.get("gossip_convergence_s", 0.0)),
        # Use explicit bool parsing — bool("false") == True is a pitfall
        partition_detected=_parse_bool_field(raw.get("partition_detected", False)),
        checked_at=float(raw.get("checked_at", time.time())),
    )


# ---------------------------------------------------------------------------
# Text renderer
# ---------------------------------------------------------------------------

_STATE_ICON: Dict[str, str] = {
    NodeState.ACTIVE.value:  "v",
    NodeState.SUSPECT.value: "?",
    NodeState.DEAD.value:    "x",
}

_STATUS_LABEL: Dict[str, str] = {
    MeshCheckStatus.PASS.value: "PASS",
    MeshCheckStatus.WARN.value: "WARN",
    MeshCheckStatus.FAIL.value: "FAIL",
}


def render_text(
    status: MeshStatus,
    *,
    gossip_warn_s: float = GOSSIP_CONVERGENCE_WARN_S,
    snapshot_stale_s: float = SNAPSHOT_STALE_WARN_S,
) -> str:
    """Return a human-readable cluster status report."""
    lines: List[str] = []
    W = 106

    def banner(text: str) -> str:
        pad = W - len(text) - 4
        return "||  " + text + " " * pad + "  ||"

    lines.append("=" * (W + 2))
    lines.append(banner("SketchLog -- Sketch Mesh Cluster Visualizer"))
    lines.append("=" * (W + 2))
    lines.append("")

    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(status.checked_at))
    lines.append(f"  Self       : {status.self_node_id}  ({status.self_address})")
    lines.append(f"  Checked at : {ts}")

    conv_warn = "  [slow]" if status.gossip_convergence_s > gossip_warn_s else ""
    lines.append(
        f"  Gossip convergence : {status.gossip_convergence_s:.3f} s{conv_warn}"
    )

    if status.partition_detected:
        lines.append("")
        lines.append("  [!] PARTITION DETECTED  cluster may be split -- investigate immediately")

    dead_frac = (
        len(status.dead_peers()) / len(status.peers) if status.peers else 0.0
    )
    if not status.partition_detected and dead_frac >= PARTITION_DEAD_FRACTION:
        lines.append(
            f"  [!] {len(status.dead_peers())} / {len(status.peers)} peers dead"
            f" ({dead_frac:.0%}) -- possible partition"
        )

    lines.append("")
    lines.append(
        f"  Peers : {len(status.active_peers())} active  "
        f"{len(status.suspect_peers())} suspect  "
        f"{len(status.dead_peers())} dead  "
        f"(total {len(status.peers)})"
    )
    lines.append(
        f"  Cluster totals : {status.total_streams()} streams  "
        f"{status.total_sketches()} sketches  "
        f"mean AE rate {status.mean_anti_entropy_rate():.4f}/s"
    )
    lines.append("")

    if status.peers:
        hdr = (
            f"  {'NODE ID':<24} {'ADDRESS':<22} {'STATE':<10}"
            f" {'GOSSIP(s)':>10} {'SNAP(s)':>10}"
            f" {'STREAMS':>8} {'SKETCHES':>9} {'AE /s':>8}"
        )
        lines.append(hdr)
        lines.append("  " + "-" * (W - 2))
        for p in sorted(status.peers, key=lambda x: x.node_id):
            stale_flag = " [stale]" if p.snapshot_age_s > snapshot_stale_s else "        "
            icon = _STATE_ICON.get(p.state, "?")
            lines.append(
                f"  {p.node_id:<24} {p.address:<22} {icon} {p.state:<8}"
                f" {p.gossip_age_s:>10.3f} {p.snapshot_age_s:>8.3f}{stale_flag}"
                f" {p.stream_count:>8} {p.sketch_count:>9}"
                f" {p.anti_entropy_rate:>8.4f}"
            )
    else:
        lines.append("  (no peers reported)")

    lines.append("")
    overall = status.overall_status(gossip_warn_s, snapshot_stale_s)
    lines.append(
        f"  Overall status : {_STATUS_LABEL.get(overall.value, overall.value)}"
    )
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Demo / synthetic data (for --demo and testing without a live server)
# ---------------------------------------------------------------------------


def _build_demo_status(seed: int = 0xC0FFEE) -> MeshStatus:
    """Return a deterministic synthetic MeshStatus for demos and smoke tests."""
    import random

    rng = random.Random(seed)
    now = 1_750_000_000.0  # fixed timestamp for reproducibility
    states = (
        [NodeState.ACTIVE.value] * 5
        + [NodeState.SUSPECT.value]
        + [NodeState.DEAD.value]
    )
    peers: List[PeerInfo] = []
    for i, state in enumerate(states):
        peers.append(
            PeerInfo(
                node_id=f"node-{i + 1:03d}",
                address=f"10.0.{i // 256}.{(i % 256) + 2}:7946",
                state=state,
                gossip_age_s=round(rng.uniform(0.1, 8.0), 3),
                snapshot_age_s=round(rng.uniform(0.5, 45.0), 3),
                stream_count=rng.randint(10, 200),
                sketch_count=rng.randint(50, 2000),
                anti_entropy_rate=round(rng.uniform(0.1, 5.0), 4),
            )
        )
    return MeshStatus(
        self_node_id="node-000",
        self_address="10.0.0.1:7946",
        peers=peers,
        gossip_convergence_s=round(rng.uniform(1.0, 9.0), 3),
        partition_detected=False,
        checked_at=now,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: Optional[Sequence[str]] = None) -> None:
    parser = argparse.ArgumentParser(
        prog="sketchlog-mesh-viz",
        description=(
            "Visualize a Sketch Mesh cluster: membership, gossip health, "
            "sync lag, and snapshot freshness."
        ),
    )
    parser.add_argument(
        "--url",
        default="",
        help=(
            "SketchLog server base URL "
            "(e.g. https://sketchlog.example.com). "
            "Required unless --demo is set."
        ),
    )
    parser.add_argument(
        "--token",
        default=None,
        help="Auth token. Prefer SKETCHLOG_AUTH_TOKEN env var.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=_DEFAULT_TIMEOUT_S,
        metavar="SEC",
        help=f"HTTP timeout in seconds (default {_DEFAULT_TIMEOUT_S}).",
    )
    parser.add_argument(
        "--gossip-warn",
        type=float,
        default=GOSSIP_CONVERGENCE_WARN_S,
        metavar="SEC",
        help=(
            f"Gossip convergence warn threshold in seconds "
            f"(default {GOSSIP_CONVERGENCE_WARN_S})."
        ),
    )
    parser.add_argument(
        "--snapshot-stale",
        type=float,
        default=SNAPSHOT_STALE_WARN_S,
        metavar="SEC",
        help=(
            f"Snapshot age warn threshold in seconds "
            f"(default {SNAPSHOT_STALE_WARN_S})."
        ),
    )
    parser.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="Output format (default text).",
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Run with synthetic data (no live server required).",
    )
    args = parser.parse_args(argv)

    if args.demo:
        status = _build_demo_status()
    else:
        if not args.url:
            print("ERROR: --url is required unless --demo is set.", file=sys.stderr)
            sys.exit(2)
        try:
            cfg = MeshVisualizerConfig(
                url=args.url,
                timeout_s=args.timeout,
                gossip_warn_s=args.gossip_warn,
                snapshot_stale_s=args.snapshot_stale,
                auth_token=args.token,
            )
        except ValueError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            sys.exit(2)
        try:
            status = _fetch_mesh_status(cfg)
        except (RuntimeError, OSError, urllib.error.URLError) as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            sys.exit(1)

    gossip_warn_s = args.gossip_warn
    snapshot_stale_s = args.snapshot_stale

    if args.format == "json":
        print(
            json.dumps(
                status.to_dict(gossip_warn_s=gossip_warn_s, snapshot_stale_s=snapshot_stale_s),
                indent=2,
            )
        )
    else:
        print(render_text(status, gossip_warn_s=gossip_warn_s, snapshot_stale_s=snapshot_stale_s))

    overall = status.overall_status(gossip_warn_s, snapshot_stale_s)
    sys.exit(0 if overall == MeshCheckStatus.PASS else 1)


if __name__ == "__main__":
    main()
