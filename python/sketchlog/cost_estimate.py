"""
sketchlog.cost_estimate
=======================
Offline cost-savings calculator for sketch-based observability.

Estimates the storage and memory difference between retaining every raw
telemetry event versus retaining only SketchLog summaries (DDSketch-style
quantile sketches for latency streams; counter aggregates for event streams).

All calculations are purely local — no server connection is required.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import math
import sys
from typing import Optional

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
        Number of SketchLog streams across all namespaces.
    namespace_count:
        Number of SketchLog namespaces.
    """

    events_per_day: int
    avg_event_bytes: int
    retention_days: int
    sketch_accuracy: float
    stream_count: int
    namespace_count: int

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

        if isinstance(self.sketch_accuracy, bool):
            errors.append("sketch_accuracy must be a float, not bool")
        else:
            try:
                _acc = float(self.sketch_accuracy)
            except (TypeError, ValueError):
                _acc = float("nan")
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

    # SketchLog totals
    sketch_total_bytes: int

    # Derived savings
    savings_bytes: int
    savings_fraction: float  # 0.0–1.0

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

    def to_dict(self) -> dict:
        """Return a JSON-serialisable representation of the full result."""
        return {
            "inputs": {
                "events_per_day": self.config.events_per_day,
                "avg_event_bytes": self.config.avg_event_bytes,
                "retention_days": self.config.retention_days,
                "sketch_accuracy": self.config.sketch_accuracy,
                "stream_count": self.config.stream_count,
                "namespace_count": self.config.namespace_count,
            },
            "raw_telemetry": {
                "total_bytes": self.raw_total_bytes,
                "human": _fmt_bytes(self.raw_total_bytes),
            },
            "sketchlog_summary": {
                "total_bytes": self.sketch_total_bytes,
                "human": _fmt_bytes(self.sketch_total_bytes),
                "latency_streams": self.latency_stream_count,
                "counter_streams": self.counter_stream_count,
                "sketch_buckets_per_stream": self.sketch_buckets_per_stream,
                "sketch_bytes_per_latency_stream_per_day": (
                    self.sketch_bytes_per_latency_stream_per_day
                ),
            },
            "savings": {
                "bytes": self.savings_bytes,
                "human": _fmt_bytes(self.savings_bytes),
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
            "╔══════════════════════════════════════════════════╗",
            "║      SketchLog Cost Savings Estimate             ║",
            "╚══════════════════════════════════════════════════╝",
            "",
            "  Inputs",
            f"    Events per day      : {self.config.events_per_day:>15,}",
            f"    Avg event size      : {_fmt_bytes(self.config.avg_event_bytes):>15}",
            f"    Retention           : {self.config.retention_days:>12,} days",
            f"    Sketch accuracy     : {self.config.sketch_accuracy:>15.6g}"
            "  (relative error)",
            f"    Streams             : {self.config.stream_count:>15,}",
            f"    Namespaces          : {self.config.namespace_count:>15,}",
            "",
            "  Storage comparison",
            f"    Raw telemetry total : {_fmt_bytes(self.raw_total_bytes):>15}",
            f"    SketchLog total     : {_fmt_bytes(self.sketch_total_bytes):>15}",
            f"    Savings             : {_fmt_bytes(self.savings_bytes):>15}"
            f"  ({self.savings_percent():.2f} %)",
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

        raw_total = events_per_day \u00d7 avg_event_bytes \u00d7 retention_days

    **SketchLog latency/quantile streams**

    A DDSketch with relative accuracy \u03b5 requires approximately \u2308\u202f2\u202f/\u202f\u03b5\u202f\u2309 buckets.
    Each bucket occupies ``_BYTES_PER_SKETCH_BUCKET`` bytes plus
    ``_SKETCH_FIXED_OVERHEAD_BYTES`` of fixed per-sketch metadata.
    Hourly windows are maintained, so ``_HOURLY_WINDOWS_PER_DAY`` sketches
    are written per stream per day::

        sketch_bytes_per_stream_per_day =
            (\u2308\u202f2\u202f/\u202f\u03b5\u202f\u2309 \u00d7 bytes_per_bucket + fixed_overhead) \u00d7 hourly_windows

    **SketchLog event/counter streams**

    Counter streams store only running totals and are capped at
    ``_COUNTER_STREAM_BYTES_PER_DAY`` per stream per day.

    **Total sketch size**::

        sketch_total =
            (latency_streams \u00d7 latency_bytes_per_stream_per_day
             + counter_streams \u00d7 _COUNTER_STREAM_BYTES_PER_DAY)
            \u00d7 namespace_count \u00d7 retention_days

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

    # --- sketch model --------------------------------------------------------
    sketch_buckets: int = max(1, math.ceil(2.0 / config.sketch_accuracy))
    sketch_bytes_per_sketch: int = (
        sketch_buckets * _BYTES_PER_SKETCH_BUCKET
        + _SKETCH_FIXED_OVERHEAD_BYTES
    )
    sketch_bytes_per_latency_stream_per_day: int = (
        sketch_bytes_per_sketch * _HOURLY_WINDOWS_PER_DAY
    )

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

    # --- savings -------------------------------------------------------------
    savings: int = max(0, raw_total - sketch_total)
    savings_fraction: float = savings / raw_total if raw_total > 0 else 0.0

    caveats: tuple[str, ...] = (
        "All figures are estimates. Actual savings depend on your workload, "
        "event shape, and cardinality.",
        "Raw telemetry compression (e.g. gzip \u223c3\u20135\u00d7) is NOT applied to the raw "
        "figure. Divide the raw total by your compression ratio for a fairer "
        "comparison.",
        f"The model assumes {int(_LATENCY_STREAM_FRACTION * 100)} % latency/quantile streams "
        f"and {int((1.0 - _LATENCY_STREAM_FRACTION) * 100)} % event/counter streams. "
        "Adjust --streams if your split differs significantly.",
        "Sketch bucket counts follow \u2308\u202f2\u202f/\u202f\u03b5\u202f\u2309 (DDSketch worst-case range ratio). "
        "Your actual sketch implementation may differ.",
        "Memory (hot-path) savings will differ from storage (cold-path) savings; "
        "this calculator estimates storage.",
        "Sketch accuracy (\u03b5) is the relative error guarantee on any quantile query "
        "(e.g. 0.01 = at most 1 % relative error at p50, p95, p99, etc.).",
    )

    return CostEstimateResult(
        config=config,
        raw_total_bytes=raw_total,
        sketch_total_bytes=sketch_total,
        savings_bytes=savings,
        savings_fraction=savings_fraction,
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
    """Return a human-readable IEC byte count (B, KiB, MiB, …)."""
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
            "All calculations are offline — no server connection is required."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
examples:
  sketchlog-cost-estimate --events-per-day 1000000 --avg-event-bytes 512 \\
      --retention-days 30 --sketch-accuracy 0.01 --streams 50 --namespaces 5

  sketchlog-cost-estimate --events-per-day 5000000 --avg-event-bytes 256 \\
      --retention-days 90 --sketch-accuracy 0.005 --streams 200 --namespaces 10 \\
      --json
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
        help="Number of SketchLog streams across all namespaces.",
    )
    parser.add_argument(
        "--namespaces",
        type=int,
        required=True,
        metavar="N",
        help="Number of SketchLog namespaces.",
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
