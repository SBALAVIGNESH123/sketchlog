import math
from typing import Dict, Any
from sketchlog.facade import StreamLog

class SmartSLOEngine:
    """
    Smart SLO Engine — SLOs That Write Themselves

    Given a stream of data, SketchLog automatically derives what your SLOs should be
    based on your actual traffic patterns, and continuously monitors burn rate.
    """

    @staticmethod
    def evaluate(
        current_stream: StreamLog,
        historical_stream: StreamLog,
        target_percentile: float = 0.995,
        budget_percent: float = 0.005
    ) -> Dict[str, Any]:
        """
        Evaluate the current stream against an auto-derived SLO from the historical stream.

        Uses the standard Google SRE definition of Burn Rate:
        Burn Rate = Error Rate / Budget Error Rate

        Args:
            current_stream: The active stream to evaluate
            historical_stream: The baseline stream to derive the target from
            target_percentile: The percentile to use as the latency target (e.g., 0.995)
            budget_percent: The allowed error rate (e.g., 0.005)

        Returns:
            Dictionary containing SLO health metrics
        """
        if not (0 < target_percentile < 1):
            raise ValueError("target_percentile must be in (0, 1)")

        if not (0 < budget_percent < 1):
            raise ValueError("budget_percent must be in (0, 1)")

        # Calculate latency counts
        historical_latency_count = historical_stream.latency_count
        current_latency_count = current_stream.latency_count

        # If historical stream has no latencies, we can't derive an SLO
        if historical_latency_count == 0:
            raise ValueError("Baseline stream has no latency events to derive target.")

        # Auto-derive the SLO target from historical baseline
        target_latency = historical_stream.percentile(target_percentile)

        # Count current errors (requests slower than the auto-derived target)
        current_errors = current_stream.count_greater_than(target_latency)

        # Calculate current error rate
        if current_latency_count > 0:
            current_error_rate = current_errors / current_latency_count
        else:
            current_error_rate = 0.0

        # Calculate burn rate (how fast we are burning the error budget)
        burn_rate = current_error_rate / budget_percent

        return {
            "target_percentile": target_percentile,
            "target_latency": target_latency,
            "budget_percent": budget_percent,
            "current_events": current_latency_count,
            "current_errors": current_errors,
            "current_error_rate": current_error_rate,
            "burn_rate": burn_rate,
            "is_alerting": burn_rate > 1.0
        }

    @staticmethod
    def recommend(
        historical_stream: StreamLog,
        target_percentile: float = 0.995,
        budget_percent: float = 0.005,
    ) -> Dict[str, float]:
        """Derive a latency objective from a non-empty historical sketch."""
        if not (0 < target_percentile < 1):
            raise ValueError("target_percentile must be in (0, 1)")
        if not (0 < budget_percent < 1):
            raise ValueError("budget_percent must be in (0, 1)")
        if historical_stream.latency_count == 0:
            raise ValueError("Baseline stream has no latency events to derive target.")
        return {
            "target_percentile": target_percentile,
            "target_latency": historical_stream.percentile(target_percentile),
            "budget_percent": budget_percent,
        }
