from fastapi.testclient import TestClient
import pytest

from sketchlog import StreamLog
from sketchlog.canary import CanaryAnalysisConfig, CanaryAnalyzer, CanaryThresholds
from sketchlog.server import app, registry


client = TestClient(app)


@pytest.fixture(autouse=True)
def cleanup():
    registry._namespaces.clear()
    yield
    registry._namespaces.clear()


def _stream(values):
    stream = StreamLog()
    stream.add_batch(values)
    return stream


def test_canary_safe_when_candidate_matches_baseline():
    baseline = _stream([100, 110, 120, 130, 140, 150, 160, 170])
    candidate = _stream([100, 110, 120, 130, 140, 150, 160, 170])

    result = CanaryAnalyzer.analyze(
        baseline,
        candidate,
        CanaryAnalysisConfig(target_percentile=0.95, budget_percent=0.10),
    )

    assert result["verdict"] == "safe"
    assert result["latency"]["shift_percent"]["p99"] == pytest.approx(0.0)
    assert result["reasons"] == ["candidate stayed within configured canary guardrails"]


def test_canary_warning_for_moderate_tail_regression():
    baseline = _stream([100] * 40 + [120] * 5)
    candidate = _stream([100] * 40 + [145] * 5)

    result = CanaryAnalyzer.analyze(
        baseline,
        candidate,
        CanaryAnalysisConfig(
            target_percentile=0.95,
            budget_percent=0.20,
            thresholds=CanaryThresholds(
                warning_p99_shift_percent=10.0,
                rollback_p99_shift_percent=80.0,
                warning_slo_burn_rate=10.0,
                rollback_slo_burn_rate=20.0,
                warning_ks_statistic=0.30,
                rollback_ks_statistic=0.90,
                warning_wasserstein_ratio=0.10,
                rollback_wasserstein_ratio=0.80,
                warning_anomaly_score=0.30,
                rollback_anomaly_score=0.90,
            ),
        ),
    )

    assert result["verdict"] == "warning"
    assert result["latency"]["shift_percent"]["p99"] > 10.0


def test_canary_rollback_for_large_latency_and_error_regression():
    baseline = _stream([100] * 80 + [120] * 20)
    candidate = _stream([100] * 20 + [300] * 80)
    baseline.add_event("checkout_error", 1)
    candidate.add_event("checkout_error", 20)

    result = CanaryAnalyzer.analyze(
        baseline,
        candidate,
        CanaryAnalysisConfig(
            target_percentile=0.95,
            budget_percent=0.05,
            error_event_name="checkout_error",
        ),
    )

    assert result["verdict"] == "rollback_recommended"
    assert result["events"]["candidate_count"] == 20
    assert any("p99 latency" in reason or "SLO burn" in reason for reason in result["reasons"])


def test_canary_endpoint_is_namespace_aware():
    client.post(
        "/v1/namespaces/prod/streams/stable.checkout/events",
        json={"latencies": [100, 105, 110, 115, 120]},
    )
    client.post(
        "/v1/namespaces/prod/streams/canary.checkout/events",
        json={"latencies": [100, 105, 110, 115, 121]},
    )

    response = client.post(
        "/v1/namespaces/prod/streams/canary.checkout/canary/analyze",
        json={
            "baseline_stream_id": "stable.checkout",
            "target_percentile": 0.95,
            "budget_percent": 0.20,
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["namespace"] == "prod"
    assert data["stream_id"] == "canary.checkout"
    assert data["baseline_stream_id"] == "stable.checkout"
    assert data["verdict"] in {"safe", "warning", "rollback_recommended"}


def test_canary_endpoint_rejects_missing_baseline():
    client.post("/v1/streams/canary/events", json={"latencies": [100, 110]})

    response = client.post(
        "/v1/streams/canary/canary/analyze",
        json={"baseline_stream_id": "missing"},
    )

    assert response.status_code == 404
