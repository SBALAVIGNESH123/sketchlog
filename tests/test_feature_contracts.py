"""Executable contracts for the public feature guides."""

from copy import deepcopy

from fastapi.testclient import TestClient

from sketchlog import StreamLog
from sketchlog.server import app


def _ingest(client: TestClient, stream: str, values: list[float]) -> None:
    response = client.post(
        f"/v1/streams/{stream}/events", json={"latencies": values})
    assert response.status_code == 202, response.text


def test_documented_diff_anomaly_slo_and_sql_workflows():
    baseline = "contract-baseline"
    current = "contract-current"
    with TestClient(app) as client:
        _ingest(client, baseline, [10, 11, 12, 13, 14])
        _ingest(client, current, [90, 100, 110, 120, 130])

        anomaly = client.get(
            f"/v1/streams/{current}/anomaly",
            params={"baseline_stream_id": baseline, "sensitivity": 0.2},
        )
        assert anomaly.status_code == 200
        assert anomaly.json()["is_anomalous"] is True
        assert anomaly.json()["model"] == "approximate_two_sample_ks"

        for parameter in ("baseline_stream_id", "baseline"):
            diff = client.get(
                f"/v1/streams/{current}/diff",
                params={parameter: baseline},
            )
            assert diff.status_code == 200
            assert 0 <= diff.json()["ks_statistic"] <= 1
            assert "wasserstein_distance" in diff.json()

        recommendation = client.get(
            f"/v1/streams/{baseline}/slo/recommend",
            params={"target_percentile": 0.99, "budget_percent": 0.005},
        )
        assert recommendation.status_code == 200
        assert recommendation.json()["budget_percent"] == 0.005

        evaluation = client.post(
            f"/v1/streams/{current}/slo/evaluate",
            json={
                "baseline_stream_id": baseline,
                "target_percentile": 0.99,
                "budget_percent": 0.005,
            },
        )
        assert evaluation.status_code == 200
        assert evaluation.json()["is_alerting"] is True

        query = client.post(
            "/v1/query",
            json={
                "query": (
                    f'SELECT p99(latency) AS tail FROM "default/{current}"')
            },
        )
        assert query.status_code == 200
        assert query.json()["results"][0]["metric"] == "tail"
        assert query.json()["results"][0]["value"] > 100


def test_wasm_precision_safe_merge_contract():
    source = StreamLog(deterministic=True)
    source.add_batch([10, 20, 30])
    source.add_event("ok", 2)
    state = deepcopy(source.to_dict())
    state["deterministic"] = False
    state["total"] = str(state["total"])
    state["latency"]["count"] = str(state["latency"]["count"])
    state["latency"]["zero_count"] = str(state["latency"]["zero_count"])
    state["events"]["total"] = str(state["events"]["total"])
    for bucket_group in ("positive", "negative"):
        state["latency"][bucket_group] = {
            key: str(value)
            for key, value in state["latency"][bucket_group].items()
        }
    state["events"]["table"] = [
        [str(value) for value in row] for row in state["events"]["table"]
    ]

    with TestClient(app) as client:
        response = client.post(
            "/v1/namespaces/frontend/streams/wasm-contract/merge",
            json={"state": state},
        )
        assert response.status_code == 202, response.text
        metrics = client.get(
            "/v1/namespaces/frontend/streams/wasm-contract/metrics")
        assert metrics.status_code == 200
        assert metrics.json()["total_events"] == 5
