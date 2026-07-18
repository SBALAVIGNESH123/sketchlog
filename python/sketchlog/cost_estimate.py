"""
sketchlog.cost_estimate
=======================
Offline cost-savings calculator for sketch-based observability.

Estimates the storage and memory difference between retaining every raw
telemetry event versus retaining only SketchLog summaries (DDSketch-style
quantile sketches for latency streams; counter aggregates for event streams).

All calculations are purely local -- no server connection is required.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import math
import sys
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Model constants (documented so tests and users can audit them)
# ---------------------------------------------------------------------------

#: Bytes consumed by each DDSketch bucket (8-byte boundary + 8-byte count).
_BYTES_PER_SKETCH_BUCKET: int = 16

#: Fixed per-sketch overhead: stream name, timestamps, version tag (~128 B).
_SKETCH_FIXED_OVERHEAD_BYTES: int = 128

#: Number of time-window sketches stored per stream per day (hourly windows).
_HOURLY_WINDOWS_PER_DAY: int = 24

#: Fraction of streams assumed to be latency/quantile streams (the rest are
#: event/counter streams, which are cheaper to store as running aggregates).
_LATENCY_STREAM_FRACTION: float = 0.6

#: Storage per event/counter stream per day (running totals + metadata only).
_COUNTER_STREAM_BYTES_PER_DAY: int = 64

#: Default raw-store compression ratio. 1.0 means no compression.
_RAW_COMPRESSION_RATIO_DEFAULT: float = 1.0

#: Default persistence backend used by the estimator.
_STORAGE_BACKEND_DEFAULT: str = "memory"

#: Operational storage headroom for each backend.  These are intentionally
#: conservative planning multipliers, not billing guarantees.
_STORAGE_BACKEND_MULTIPLIERS: dict[str, float] = {
    "memory": 1.0,
    "postgres": 1.25,
    "omnikv": 1.15,
}

#: Human-readable backend labels.
_STORAGE_BACKEND_LABELS: dict[str, str] = {
    "memory": "In-memory",
    "postgres": "PostgreSQL durable",
    "omnikv": "OmniKV embedded",
}

#: Backend notes emitted in reports and JSON for operators.
_STORAGE_BACKEND_NOTES: dict[str, str] = {
    "memory": "Volatile hot-path state for demos, tests, and short-lived evaluations.",
    "postgres": "Adds planning headroom for rows, indexes, WAL, and SQL metadata.",
    "omnikv": "Adds planning headroom for embedded key/value metadata and compaction.",
}

#: Hot memory model: each active latency stream keeps bucket counters plus a
#: fixed metadata envelope while the process is running.
_HOT_LATENCY_STREAM_FIXED_BYTES: int = 512

#: Hot memory model for active event/counter streams.
_HOT_COUNTER_STREAM_BYTES: int = 256

#: Decimal precision for percentage fields.
_PCT_DECIMALS: int = 2


# ---------------------------------------------------------------------------
# Input config
# ---------------------------------------------------------------------------

@dataclasses.dataclass(frozen=True)
class CostEstimateConfig:
    """Validated inputs for a cost estimate run.

    Parameters
    ----------
    events_per_day:
        Total raw telemetry events ingested per day across all streams.
    avg_event_bytes:
        Mean size of a single raw event in bytes (e.g. a JSON log line).
    retention_days:
        How many days of data must be retained (raw or sketch).
    sketch_accuracy:
        Relative error guarantee of the sketch (e.g. 0.01 = 1 %).
        Smaller values produce more accurate but larger sketches.
        Must be a finite float strictly between 0 and 1.
    stream_count:
        Number of SketchLog streams per namespace.
        Total streams across all namespaces = stream_count * namespace_count.
    namespace_count:
        Number of SketchLog namespaces.
    raw_compression_ratio:
        Compression ratio for a raw-event store baseline. ``1.0`` means
        uncompressed raw telemetry. ``4.0`` models a 4x compressed raw store.
    storage_backend:
        Persistence backend profile used for operational footprint planning.
        Supported values are ``memory``, ``postgres``, and ``omnikv``.
    """

    events_per_day: int
    avg_event_bytes: int
    retention_days: int
    sketch_accuracy: float
    stream_count: int
    namespace_count: int
    raw_compression_ratio: float = _RAW_COMPRESSION_RATIO_DEFAULT
    storage_backend: str = _STORAGE_BACKEND_DEFAULT

    def __post_init__(self) -> None:  # noqa: C901
        errors: list[str] = []

        if not isinstance(self.events_per_day, int) or isinstance(self.events_per_day, bool):
            errors.append("events_per_day must be an integer")
        elif self.events_per_day < 1:
            errors.append("events_per_day must be a positive integer (>= 1)")

        if not isinstance(self.avg_event_bytes, int) or isinstance(self.avg_event_bytes, bool):
            errors.append("avg_event_bytes must be an integer")
        elif self.avg_event_bytes < 1:
            errors.append("avg_event_bytes must be a positive integer (>= 1)")

        if not isinstance(self.retention_days, int) or isinstance(self.retention_days, bool):
            errors.append("retention_days must be an integer")
        elif self.retention_days < 1:
            errors.append("retention_days must be a positive integer (>= 1)")

        # sketch_accuracy: reject bool first, then check numeric type, then range.
        # Avoids an unreachable except clause that pyright/mypy flag as dead code.
        if isinstance(self.sketch_accuracy, bool):
            errors.append("sketch_accuracy must be a float, not bool")
        elif not isinstance(self.sketch_accuracy, (int, float)):
            errors.append(
                "sketch_accuracy must be a float; "
                f"got {type(self.sketch_accuracy).__name__}"
            )
        else:
            _acc = float(self.sketch_accuracy)
            if not math.isfinite(_acc) or not (0.0 < _acc < 1.0):
                errors.append(
                    "sketch_accuracy must be a finite float strictly in (0, 1); "
                    f"got {self.sketch_accuracy!r}"
                )

        if not isinstance(self.stream_count, int) or isinstance(self.stream_count, bool):
            errors.append("stream_count must be an integer")
        elif self.stream_count < 1:
            errors.append("stream_count must be a positive integer (>= 1)")

        if not isinstance(self.namespace_count, int) or isinstance(self.namespace_count, bool):
            errors.append("namespace_count must be an integer")
        elif self.namespace_count < 1:
            errors.append("namespace_count must be a positive integer (>= 1)")

        if isinstance(self.raw_compression_ratio, bool):
            errors.append("raw_compression_ratio must be a float, not bool")
        elif not isinstance(self.raw_compression_ratio, (int, float)):
            errors.append(
                "raw_compression_ratio must be a float; "
                f"got {type(self.raw_compression_ratio).__name__}"
            )
        else:
            _ratio = float(self.raw_compression_ratio)
            if not math.isfinite(_ratio) or _ratio < 1.0:
                errors.append(
                    "raw_compression_ratio must be a finite float >= 1.0; "
                    f"got {self.raw_compression_ratio!r}"
                )

        if not isinstance(self.storage_backend, str):
            errors.append("storage_backend must be a string")
        elif self.storage_backend not in _STORAGE_BACKEND_MULTIPLIERS:
            allowed = ", ".join(sorted(_STORAGE_BACKEND_MULTIPLIERS))
            errors.append(
                f"storage_backend must be one of: {allowed}; "
                f"got {self.storage_backend!r}"
            )

        if errors:
            raise ValueError("; ".join(errors))


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------

@dataclasses.dataclass(frozen=True)
class CostEstimateResult:
    """Computed cost-estimate output.

    All ``*_bytes`` fields are raw byte counts.
    Use :meth:`to_dict` for a JSON-serialisable view or
    :meth:`render_text` for a human-readable report.
    """

    config: CostEstimateConfig

    # Raw telemetry totals
    raw_total_bytes: int
    compressed_raw_total_bytes: int

    # SketchLog totals
    sketch_total_bytes: int
    backend_adjusted_sketch_total_bytes: int
    backend_overhead_multiplier: float

    # Derived savings; negative means SketchLog uses more storage than raw
    savings_bytes: int
    savings_fraction: float  # negative if sketch > raw

    # Operational footprint
    hot_memory_bytes: int
    events_per_second: float
    total_stream_count: int

    # Per-stream breakdown
    latency_stream_count: int
    counter_stream_count: int
    sketch_bytes_per_latency_stream_per_day: int
    sketch_buckets_per_stream: int

    # Caveats included in every output
    caveats: tuple[str, ...]

    # ------------------------------------------------------------------

    def savings_percent(self) -> float:
        """Return savings as a percentage, rounded to ``_PCT_DECIMALS``."""
        return round(self.savings_fraction * 100.0, _PCT_DECIMALS)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable representation of the full result."""
        return {
            "inputs": {
                "events_per_day": self.config.events_per_day,
                "avg_event_bytes": self.config.avg_event_bytes,
                "retention_days": self.config.retention_days,
                "sketch_accuracy": self.config.sketch_accuracy,
                "stream_count": self.config.stream_count,
                "namespace_count": self.config.namespace_count,
                "raw_compression_ratio": self.config.raw_compression_ratio,
                "storage_backend": self.config.storage_backend,
            },
            "raw_telemetry": {
                "total_bytes": self.raw_total_bytes,
                "compressed_bytes": self.compressed_raw_total_bytes,
                "human": _fmt_bytes(self.raw_total_bytes),
                "human_compressed": _fmt_bytes(self.compressed_raw_total_bytes),
            },
            "sketchlog_summary": {
                "total_bytes": self.sketch_total_bytes,
                "backend_adjusted_bytes": self.backend_adjusted_sketch_total_bytes,
                "human": _fmt_bytes(self.sketch_total_bytes),
                "human_backend_adjusted": _fmt_bytes(self.backend_adjusted_sketch_total_bytes),
                "storage_backend": self.config.storage_backend,
                "storage_backend_label": _STORAGE_BACKEND_LABELS[self.config.storage_backend],
                "backend_overhead_multiplier": self.backend_overhead_multiplier,
                "backend_note": _STORAGE_BACKEND_NOTES[self.config.storage_backend],
                "total_streams": self.total_stream_count,
                "latency_streams": self.latency_stream_count,
                "counter_streams": self.counter_stream_count,
                "sketch_buckets_per_stream": self.sketch_buckets_per_stream,
                "sketch_bytes_per_latency_stream_per_day": (
                    self.sketch_bytes_per_latency_stream_per_day
                ),
            },
            "operational_footprint": {
                "events_per_second": round(self.events_per_second, 4),
                "hot_memory_bytes": self.hot_memory_bytes,
                "human_hot_memory": _fmt_bytes(self.hot_memory_bytes),
            },
            "savings": {
                "bytes": self.savings_bytes,
                "human": _fmt_bytes(abs(self.savings_bytes)),
                "percent": self.savings_percent(),
                "fraction": round(self.savings_fraction, 8),
            },
            "caveats": list(self.caveats),
        }

    def render_text(self) -> str:
        """Return a multi-line human-readable cost-savings report."""
        lat_pct = int(_LATENCY_STREAM_FRACTION * 100)
        ctr_pct = 100 - lat_pct
        lines: list[str] = [
            "",
            "\u2554\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2557",
            "\u2551      SketchLog Cost Savings Estimate             \u2551",
            "\u255a\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u255d",
            "",
            "  Inputs",
            f"    Events per day      : {self.config.events_per_day:>15,}",
            f"    Avg event size      : {_fmt_bytes(self.config.avg_event_bytes):>15}",
            f"    Retention           : {self.config.retention_days:>12,} days",
            f"    Sketch accuracy     : {self.config.sketch_accuracy:>15.6g}"
            "  (relative error)",
            f"    Streams (per ns)    : {self.config.stream_count:>15,}",
            f"    Namespaces          : {self.config.namespace_count:>15,}",
            f"    Raw compression     : {self.config.raw_compression_ratio:>15.2f}x",
            f"    Storage backend     : "
            f"{_STORAGE_BACKEND_LABELS[self.config.storage_backend]:>15}",
            "",
            "  Storage comparison",
            f"    Raw telemetry total : {_fmt_bytes(self.raw_total_bytes):>15}",
            f"    Compressed raw      : {_fmt_bytes(self.compressed_raw_total_bytes):>15}",
            f"    SketchLog compact   : {_fmt_bytes(self.sketch_total_bytes):>15}",
            f"    Backend-adjusted    : "
            f"{_fmt_bytes(self.backend_adjusted_sketch_total_bytes):>15}",
            f"    Savings             : {_fmt_bytes(abs(self.savings_bytes)):>15}"
            f"  ({self.savings_percent():.2f} %)",
            "",
            "  Operational footprint",
            f"    Ingest rate         : {self.events_per_second:>15,.2f} events/s",
            f"    Total streams       : {self.total_stream_count:>15,}",
            f"    Hot memory estimate : {_fmt_bytes(self.hot_memory_bytes):>15}",
            f"    Backend note        : {_STORAGE_BACKEND_NOTES[self.config.storage_backend]}",
            "",
            "  Sketch model details",
            f"    Latency streams     : {self.latency_stream_count:>15,}"
            f"  ({lat_pct} % of total)",
            f"    Counter streams     : {self.counter_stream_count:>15,}"
            f"  ({ctr_pct} % of total)",
            f"    Buckets / stream    : {self.sketch_buckets_per_stream:>15,}",
            f"    Bytes / lat. stream : "
            f"{_fmt_bytes(self.sketch_bytes_per_latency_stream_per_day):>15}"
            "  per day",
            "",
            "  Caveats",
        ]
        for caveat in self.caveats:
            lines.append(f"    \u2022 {caveat}")
        lines.append("")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Computation
# ---------------------------------------------------------------------------

def estimate(config: CostEstimateConfig) -> CostEstimateResult:
    """Compute a :class:`CostEstimateResult` from a validated config.

    Model
    -----
    **Raw telemetry**::

        raw_total = events_per_day x avg_event_bytes x retention_days
        compressed_raw_total = raw_total / raw_compression_ratio

    **SketchLog latency/quantile streams**

    A DDSketch with relative accuracy epsilon requires approximately
    ceil(2 / epsilon) buckets.  Each bucket occupies
    ``_BYTES_PER_SKETCH_BUCKET`` bytes plus ``_SKETCH_FIXED_OVERHEAD_BYTES``
    of fixed per-sketch metadata.  Hourly windows are maintained, so
    ``_HOURLY_WINDOWS_PER_DAY`` sketches are written per stream per day::

        sketch_bytes_per_stream_per_day =
            (ceil(2/epsilon) x bytes_per_bucket + fixed_overhead)
            x hourly_windows

    **SketchLog event/counter streams**

    Counter streams store only running totals and are capped at
    ``_COUNTER_STREAM_BYTES_PER_DAY`` per stream per day.

    **Total sketch size**::

        sketch_total =
            (latency_streams x latency_bytes_per_stream_per_day
             + counter_streams x _COUNTER_STREAM_BYTES_PER_DAY)
            x namespace_count x retention_days

    where ``latency_streams`` and ``counter_streams`` are per-namespace counts
    derived from ``stream_count``.

    **Backend planning total**::

        backend_adjusted_sketch_total =
            sketch_total x storage_backend_multiplier

    **Savings**::

        savings = compressed_raw_total - backend_adjusted_sketch_total

    A negative value means SketchLog uses *more* storage than raw for this
    configuration (only possible at very low event volumes or very high
    stream/namespace counts).

    Parameters
    ----------
    config:
        A fully validated :class:`CostEstimateConfig`.

    Returns
    -------
    CostEstimateResult
        Immutable result dataclass with all computed fields and caveats.
    """
    # --- raw telemetry -------------------------------------------------------
    raw_total: int = (
        config.events_per_day
        * config.avg_event_bytes
        * config.retention_days
    )
    compressed_raw_total: int = round(raw_total / config.raw_compression_ratio)

    # --- sketch model --------------------------------------------------------
    sketch_buckets: int = max(1, math.ceil(2.0 / config.sketch_accuracy))
    sketch_bytes_per_sketch: int = (
        sketch_buckets * _BYTES_PER_SKETCH_BUCKET
        + _SKETCH_FIXED_OVERHEAD_BYTES
    )
    sketch_bytes_per_latency_stream_per_day: int = (
        sketch_bytes_per_sketch * _HOURLY_WINDOWS_PER_DAY
    )

    # stream_count is per-namespace; derive latency/counter split per namespace
    latency_streams: int = max(1, round(config.stream_count * _LATENCY_STREAM_FRACTION))
    counter_streams: int = max(0, config.stream_count - latency_streams)

    sketch_total: int = (
        (
            latency_streams * sketch_bytes_per_latency_stream_per_day
            + counter_streams * _COUNTER_STREAM_BYTES_PER_DAY
        )
        * config.namespace_count
        * config.retention_days
    )
    backend_overhead_multiplier: float = _STORAGE_BACKEND_MULTIPLIERS[
        config.storage_backend
    ]
    backend_adjusted_sketch_total: int = round(
        sketch_total * backend_overhead_multiplier
    )

    # Hot memory is intentionally a separate planning number from persisted
    # storage. It estimates the active in-process footprint for the current
    # stream set before compaction/export/persistence concerns are applied.
    hot_memory_bytes: int = round(
        config.namespace_count
        * (
            latency_streams
            * (sketch_buckets * 8 + _HOT_LATENCY_STREAM_FIXED_BYTES)
            + counter_streams * _HOT_COUNTER_STREAM_BYTES
        )
    )
    events_per_second: float = config.events_per_day / 86_400.0
    total_stream_count: int = config.stream_count * config.namespace_count

    # --- savings -------------------------------------------------------------
    # Not clamped: a negative value correctly signals that SketchLog uses more
    # storage than raw for this configuration (e.g. very low event volume with
    # many streams).
    savings: int = compressed_raw_total - backend_adjusted_sketch_total
    savings_fraction: float = (
        savings / compressed_raw_total if compressed_raw_total > 0 else 0.0
    )

    caveats: tuple[str, ...] = (
        "All figures are estimates. Actual savings depend on your workload, "
        "event shape, and cardinality.",
        "Raw telemetry compression is user-selected. Use --raw-compression-ratio "
        "to compare against your current raw store more fairly.",
        f"The model assumes {int(_LATENCY_STREAM_FRACTION * 100)} % latency/quantile streams "
        f"and {int((1.0 - _LATENCY_STREAM_FRACTION) * 100)} % event/counter streams. "
        "Adjust --streams if your split differs significantly.",
        "Sketch bucket counts follow ceil(2/epsilon) (DDSketch worst-case range ratio). "
        "Your actual sketch implementation may differ.",
        "Hot memory and persisted storage are separate planning figures. Validate "
        "real workloads with the proof commands before capacity decisions.",
        "Sketch accuracy (epsilon) is the relative error guarantee on any quantile query "
        "(e.g. 0.01 = at most 1 % relative error at p50, p95, p99, etc.).",
    )

    return CostEstimateResult(
        config=config,
        raw_total_bytes=raw_total,
        compressed_raw_total_bytes=compressed_raw_total,
        sketch_total_bytes=sketch_total,
        backend_adjusted_sketch_total_bytes=backend_adjusted_sketch_total,
        backend_overhead_multiplier=backend_overhead_multiplier,
        savings_bytes=savings,
        savings_fraction=savings_fraction,
        hot_memory_bytes=hot_memory_bytes,
        events_per_second=events_per_second,
        total_stream_count=total_stream_count,
        latency_stream_count=latency_streams,
        counter_stream_count=counter_streams,
        sketch_bytes_per_latency_stream_per_day=sketch_bytes_per_latency_stream_per_day,
        sketch_buckets_per_stream=sketch_buckets,
        caveats=caveats,
    )


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def _fmt_bytes(n: int) -> str:
    """Return a human-readable IEC byte count (B, KiB, MiB, ...)."""
    value = float(n)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB", "PiB"):
        if abs(value) < 1024.0:
            return f"{value:.2f} {unit}"
        value /= 1024.0
    return f"{value:.2f} EiB"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sketchlog-cost-estimate",
        description=(
            "Estimate storage and memory savings from using SketchLog\n"
            "instead of retaining every raw telemetry event.\n\n"
            "All calculations are offline -- no server connection is required."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
examples:
  sketchlog-cost-estimate --events-per-day 1000000 --avg-event-bytes 512 \\
      --retention-days 30 --sketch-accuracy 0.01 --streams 50 --namespaces 5

  sketchlog-cost-estimate --events-per-day 5000000 --avg-event-bytes 256 \\
      --retention-days 90 --sketch-accuracy 0.005 --streams 200 --namespaces 10 \\
      --raw-compression-ratio 4 --storage-backend postgres --json
""",
    )
    parser.add_argument(
        "--events-per-day",
        type=int,
        required=True,
        metavar="N",
        help="Total raw telemetry events ingested per day across all streams.",
    )
    parser.add_argument(
        "--avg-event-bytes",
        type=int,
        required=True,
        metavar="BYTES",
        help="Mean size of a single raw event in bytes (e.g. 512 for a JSON log line).",
    )
    parser.add_argument(
        "--retention-days",
        type=int,
        required=True,
        metavar="DAYS",
        help="How many days of data must be retained (raw or sketch).",
    )
    parser.add_argument(
        "--sketch-accuracy",
        type=float,
        required=True,
        metavar="EPSILON",
        help=(
            "Relative error guarantee of the sketch, e.g. 0.01 for 1 %%. "
            "Smaller values produce more accurate but larger sketches. "
            "Must be strictly between 0 and 1."
        ),
    )
    parser.add_argument(
        "--streams",
        type=int,
        required=True,
        metavar="N",
        help="Number of SketchLog streams per namespace.",
    )
    parser.add_argument(
        "--namespaces",
        type=int,
        required=True,
        metavar="N",
        help="Number of SketchLog namespaces.",
    )
    parser.add_argument(
        "--raw-compression-ratio",
        type=float,
        default=_RAW_COMPRESSION_RATIO_DEFAULT,
        metavar="RATIO",
        help=(
            "Compression ratio for the raw-event baseline, e.g. 4 for a 4x "
            "compressed raw store. Defaults to 1.0."
        ),
    )
    parser.add_argument(
        "--storage-backend",
        choices=sorted(_STORAGE_BACKEND_MULTIPLIERS),
        default=_STORAGE_BACKEND_DEFAULT,
        help=(
            "Backend planning profile for the SketchLog footprint. "
            "Defaults to memory."
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        default=False,
        dest="output_json",
        help="Emit machine-readable JSON instead of the human-readable report.",
    )
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    """CLI entry point.

    Parameters
    ----------
    argv:
        Argument list for testing.  Defaults to ``sys.argv[1:]``.

    Returns
    -------
    int
        Exit code: 0 on success, 2 on invalid input.
    """
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        config = CostEstimateConfig(
            events_per_day=args.events_per_day,
            avg_event_bytes=args.avg_event_bytes,
            retention_days=args.retention_days,
            sketch_accuracy=args.sketch_accuracy,
            stream_count=args.streams,
            namespace_count=args.namespaces,
            raw_compression_ratio=args.raw_compression_ratio,
            storage_backend=args.storage_backend,
        )
    except ValueError as exc:
        print(f"error: invalid input \u2014 {exc}", file=sys.stderr)
        return 2

    result = estimate(config)

    if args.output_json:
        print(json.dumps(result.to_dict(), indent=2))
    else:
        print(result.render_text())

    return 0


if __name__ == "__main__":
    sys.exit(main())
