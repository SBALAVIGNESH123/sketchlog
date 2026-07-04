from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Optional

from sketchlog.diff import SketchDiff
from sketchlog.facade import StreamLog
from sketchlog.slo import SmartSLOEngine


class CanaryVerdict(str, Enum):
    SAFE = "safe"
    WARNING = "warning"
    ROLLBACK_RECOMMENDED = "rollback_recommended"


@dataclass(frozen=True)
class CanaryThresholds:
    warning_p99_shift_percent: float = 10.0
    rollback_p99_shift_percent: float = 35.0
    warning_error_rate_increase_percent: float = 25.0
    rollback_error_rate_increase_percent: float = 100.0
    warning_slo_burn_rate: float = 1.0
    rollback_slo_burn_rate: float = 4.0
    warning_ks_statistic: float = 0.12
    rollback_ks_statistic: float = 0.30
    warning_wasserstein_ratio: float = 0.20
    rollback_wasserstein_ratio: float = 0.60
    warning_anomaly_score: float = 0.20
    rollback_anomaly_score: float = 0.45


@dataclass(frozen=True)
class CanaryAnalysisConfig:
    target_percentile: float = 0.995
    budget_percent: float = 0.005
    min_latency_count: int = 1
    error_event_name: Optional[str] = None
    thresholds: CanaryThresholds = CanaryThresholds()


def _percent_change(baseline: float, candidate: float) -> float:
    if baseline == 0.0:
        return 0.0 if candidate == 0.0 else 100.0
    return ((candidate - baseline) / abs(baseline)) * 100.0


def _rate(count: int, denominator: int) -> float:
    return 0.0 if denominator <= 0 else count / denominator


def _bounded_score(value: float, warning: float, rollback: float) -> float:
    if rollback <= warning or value <= warning:
        return 0.0
    return min(1.0, (value - warning) / (rollback - warning))


def _worst_verdict(current: CanaryVerdict, candidate: CanaryVerdict) -> CanaryVerdict:
    order = {
        CanaryVerdict.SAFE: 0,
        CanaryVerdict.WARNING: 1,
        CanaryVerdict.ROLLBACK_RECOMMENDED: 2,
    }
    return candidate if order[candidate] > order[current] else current


def _metric_verdict(value: float, warning: float, rollback: float) -> CanaryVerdict:
    if value >= rollback:
        return CanaryVerdict.ROLLBACK_RECOMMENDED
    if value >= warning:
        return CanaryVerdict.WARNING
    return CanaryVerdict.SAFE


class CanaryAnalyzer:
    """Deployment canary analysis built from existing SketchLog primitives.

    The analyzer combines distribution diffing, SLO burn-rate evaluation,
    anomaly scoring, and optional event-rate deltas into one release-risk
    workflow. It does not duplicate those underlying primitives.
    """

    @staticmethod
    def analyze(
        baseline_stream: StreamLog,
        candidate_stream: StreamLog,
        config: Optional[CanaryAnalysisConfig] = None,
    ) -> Dict[str, Any]:
        cfg = config or CanaryAnalysisConfig()
        if cfg.min_latency_count < 1:
            raise ValueError("min_latency_count must be >= 1")

        baseline_count = baseline_stream.latency_count
        candidate_count = candidate_stream.latency_count
        if baseline_count < cfg.min_latency_count:
            raise ValueError("Baseline stream does not have enough latency data for canary analysis.")
        if candidate_count < cfg.min_latency_count:
            raise ValueError("Candidate stream does not have enough latency data for canary analysis.")

        baseline_p50 = baseline_stream.p50()
        baseline_p95 = baseline_stream.p95()
        baseline_p99 = baseline_stream.p99()
        candidate_p50 = candidate_stream.p50()
        candidate_p95 = candidate_stream.p95()
        candidate_p99 = candidate_stream.p99()

        p50_shift = _percent_change(baseline_p50, candidate_p50)
        p95_shift = _percent_change(baseline_p95, candidate_p95)
        p99_shift = _percent_change(baseline_p99, candidate_p99)

        diff = SketchDiff(baseline_stream, candidate_stream)
        ks = diff.ks_statistic
        wasserstein = diff.wasserstein_distance
        baseline_scale = max(abs(baseline_p99), 1.0)
        wasserstein_ratio = wasserstein / baseline_scale
        anomaly_score = candidate_stream.anomaly_score(baseline_stream)

        slo = SmartSLOEngine.evaluate(
            current_stream=candidate_stream,
            historical_stream=baseline_stream,
            target_percentile=cfg.target_percentile,
            budget_percent=cfg.budget_percent,
        )
        burn_rate = float(slo["burn_rate"])

        baseline_error_count = 0
        candidate_error_count = 0
        baseline_error_rate = 0.0
        candidate_error_rate = 0.0
        error_rate_increase_percent = 0.0
        if cfg.error_event_name:
            baseline_error_count = baseline_stream.event_count(cfg.error_event_name)
            candidate_error_count = candidate_stream.event_count(cfg.error_event_name)
            baseline_denominator = max(baseline_stream.total_events, baseline_count)
            candidate_denominator = max(candidate_stream.total_events, candidate_count)
            baseline_error_rate = _rate(baseline_error_count, baseline_denominator)
            candidate_error_rate = _rate(candidate_error_count, candidate_denominator)
            error_rate_increase_percent = _percent_change(baseline_error_rate, candidate_error_rate)

        thresholds = cfg.thresholds
        verdict = CanaryVerdict.SAFE
        reasons = []

        p99_verdict = _metric_verdict(
            p99_shift,
            thresholds.warning_p99_shift_percent,
            thresholds.rollback_p99_shift_percent,
        )
        if p99_verdict != CanaryVerdict.SAFE:
            reasons.append(f"p99 latency increased by {p99_shift:.1f}%")
            verdict = _worst_verdict(verdict, p99_verdict)

        ks_verdict = _metric_verdict(ks, thresholds.warning_ks_statistic, thresholds.rollback_ks_statistic)
        if ks_verdict != CanaryVerdict.SAFE:
            reasons.append(f"distribution KS statistic is {ks:.3f}")
            verdict = _worst_verdict(verdict, ks_verdict)

        wasserstein_verdict = _metric_verdict(
            wasserstein_ratio,
            thresholds.warning_wasserstein_ratio,
            thresholds.rollback_wasserstein_ratio,
        )
        if wasserstein_verdict != CanaryVerdict.SAFE:
            reasons.append(f"normalized Wasserstein distance is {wasserstein_ratio:.3f}")
            verdict = _worst_verdict(verdict, wasserstein_verdict)

        # SLO and anomaly signals are valuable canary risk indicators, but they
        # must not turn an unchanged candidate into a warning just because the
        # candidate and baseline share the same historical tail. Gate these
        # secondary signals on an actual positive p99 regression. Direct
        # distribution metrics and error-rate deltas remain evaluated
        # independently below.
        has_latency_regression = p99_shift >= thresholds.warning_p99_shift_percent

        slo_verdict = _metric_verdict(burn_rate, thresholds.warning_slo_burn_rate, thresholds.rollback_slo_burn_rate)
        if has_latency_regression and slo_verdict != CanaryVerdict.SAFE:
            reasons.append(f"SLO burn rate is {burn_rate:.2f}x")
            verdict = _worst_verdict(verdict, slo_verdict)

        anomaly_verdict = _metric_verdict(
            anomaly_score,
            thresholds.warning_anomaly_score,
            thresholds.rollback_anomaly_score,
        )
        if has_latency_regression and anomaly_verdict != CanaryVerdict.SAFE:
            reasons.append(f"anomaly score is {anomaly_score:.3f}")
            verdict = _worst_verdict(verdict, anomaly_verdict)

        if cfg.error_event_name:
            error_verdict = _metric_verdict(
                error_rate_increase_percent,
                thresholds.warning_error_rate_increase_percent,
                thresholds.rollback_error_rate_increase_percent,
            )
            if error_verdict != CanaryVerdict.SAFE:
                reasons.append(
                    f"event rate for {cfg.error_event_name!r} increased by "
                    f"{error_rate_increase_percent:.1f}%"
                )
                verdict = _worst_verdict(verdict, error_verdict)

        if not reasons:
            reasons.append("candidate stayed within configured canary guardrails")

        confidence = max(
            _bounded_score(p99_shift, thresholds.warning_p99_shift_percent, thresholds.rollback_p99_shift_percent),
            _bounded_score(ks, thresholds.warning_ks_statistic, thresholds.rollback_ks_statistic),
            _bounded_score(wasserstein_ratio, thresholds.warning_wasserstein_ratio, thresholds.rollback_wasserstein_ratio),
            _bounded_score(burn_rate, thresholds.warning_slo_burn_rate, thresholds.rollback_slo_burn_rate),
            _bounded_score(anomaly_score, thresholds.warning_anomaly_score, thresholds.rollback_anomaly_score),
            _bounded_score(
                error_rate_increase_percent,
                thresholds.warning_error_rate_increase_percent,
                thresholds.rollback_error_rate_increase_percent,
            ) if cfg.error_event_name else 0.0,
        )

        return {
            "verdict": verdict.value,
            "confidence": confidence,
            "reasons": reasons,
            "latency": {
                "baseline": {"p50": baseline_p50, "p95": baseline_p95, "p99": baseline_p99, "count": baseline_count},
                "candidate": {"p50": candidate_p50, "p95": candidate_p95, "p99": candidate_p99, "count": candidate_count},
                "shift_percent": {"p50": p50_shift, "p95": p95_shift, "p99": p99_shift},
            },
            "distribution": {
                "ks_statistic": ks,
                "wasserstein_distance": wasserstein,
                "wasserstein_ratio": wasserstein_ratio,
            },
            "slo": slo,
            "anomaly": {
                "score": anomaly_score,
                "threshold": thresholds.warning_anomaly_score,
                "is_anomalous": anomaly_score >= thresholds.warning_anomaly_score,
            },
            "events": {
                "event_name": cfg.error_event_name,
                "baseline_count": baseline_error_count,
                "candidate_count": candidate_error_count,
                "baseline_rate": baseline_error_rate,
                "candidate_rate": candidate_error_rate,
                "rate_increase_percent": error_rate_increase_percent,
            },
            "thresholds": {
                "warning_p99_shift_percent": thresholds.warning_p99_shift_percent,
                "rollback_p99_shift_percent": thresholds.rollback_p99_shift_percent,
                "warning_error_rate_increase_percent": thresholds.warning_error_rate_increase_percent,
                "rollback_error_rate_increase_percent": thresholds.rollback_error_rate_increase_percent,
                "warning_slo_burn_rate": thresholds.warning_slo_burn_rate,
                "rollback_slo_burn_rate": thresholds.rollback_slo_burn_rate,
                "warning_ks_statistic": thresholds.warning_ks_statistic,
                "rollback_ks_statistic": thresholds.rollback_ks_statistic,
                "warning_wasserstein_ratio": thresholds.warning_wasserstein_ratio,
                "rollback_wasserstein_ratio": thresholds.rollback_wasserstein_ratio,
                "warning_anomaly_score": thresholds.warning_anomaly_score,
                "rollback_anomaly_score": thresholds.rollback_anomaly_score,
            },
        }
