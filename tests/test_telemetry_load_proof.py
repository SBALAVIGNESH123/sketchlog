import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


def _load_telemetry_load_proof():
    scripts_dir = Path(__file__).resolve().parents[1] / "scripts"
    sys.path.insert(0, str(scripts_dir))
    try:
        import telemetry_load_proof

        return telemetry_load_proof
    finally:
        sys.path.remove(str(scripts_dir))


def _args(tmp_path: Path, *backends: str) -> SimpleNamespace:
    return SimpleNamespace(
        backend=list(backends),
        events=64,
        seed=20260417,
        batch_size=16,
        fixture_output=None,
        work_dir=tmp_path / "proof-root",
        json_output=None,
        json_only=False,
        continue_on_error=False,
        allow_missing_optional=True,
        keep_data=False,
        postgres_compose_file=tmp_path / "compose.yml",
        postgres_server_url="http://127.0.0.1:4180",
        postgres_start=True,
        postgres_stop=True,
        omnikv_data_dir=None,
        omnikv_module="omnikv",
        omnikv_namespace="sketchlog",
    )


def test_fixture_generation_is_deterministic_and_realistic() -> None:
    proof = _load_telemetry_load_proof()

    first = proof.generate_telemetry_events(32, seed=7)
    second = proof.generate_telemetry_events(32, seed=7)

    assert [event.to_json() for event in first] == [
        event.to_json() for event in second
    ]
    assert first[0].timestamp.endswith("Z")
    assert first[0].latency_ms > 0
    assert {"env", "region", "tenant", "service", "route"}.issubset(
        first[0].labels
    )
    assert {event.service for event in first}
    assert {event.status for event in first}


def test_fixture_summary_reports_required_launch_evidence() -> None:
    proof = _load_telemetry_load_proof()
    events = proof.generate_telemetry_events(128, seed=11)

    summary = proof.fixture_summary(events, seed=11)

    assert summary["event_count"] == 128
    assert summary["raw_jsonl_bytes"] > 128
    assert summary["exact"]["p50_ms"] <= summary["exact"]["p95_ms"]
    assert summary["exact"]["p95_ms"] <= summary["exact"]["p99_ms"]
    assert summary["exact"]["unique_users"] > 1
    assert summary["top_items"]
    assert "label_keys" in summary


def test_batching_preserves_expected_sketch_event_count() -> None:
    proof = _load_telemetry_load_proof()
    events = proof.generate_telemetry_events(31, seed=19)
    expected = proof.fixture_summary(events)["exact"]["sketch_total_events"]

    observed = 0
    batches = list(proof.iter_batches(events, batch_size=7))
    for batch in batches:
        observed += len(batch["latencies"])
        observed += sum(batch["events"].values())

    assert len(batches) == 5
    assert observed == expected


def test_jsonl_fixture_writer_matches_summary_bytes(tmp_path: Path) -> None:
    proof = _load_telemetry_load_proof()
    events = proof.generate_telemetry_events(12, seed=23)
    output = tmp_path / "telemetry.jsonl"

    written = proof.write_jsonl_fixture(events, output)

    assert written == proof.fixture_summary(events)["raw_jsonl_bytes"]
    assert output.read_text(encoding="utf-8").count("\n") == 12


def test_memory_telemetry_load_proof_runs_end_to_end(tmp_path: Path) -> None:
    proof = _load_telemetry_load_proof()
    events = proof.generate_telemetry_events(96, seed=31)

    summary = proof.run_memory_load_proof(
        tmp_path / "memory-proof",
        events,
        batch_size=24,
    )

    assert summary["backend"] == "memory"
    assert summary["status"] == "pass"
    assert summary["restart_behavior"] == "ephemeral_expected_missing"
    assert summary["fixture"]["event_count"] == 96
    assert summary["before_restart"]["sql"]["p95_ms"] > 0
    assert summary["before_restart"]["top_items"]
    assert "raw_to_compact_ratio" in summary["before_restart"]["storage_model"]
    assert isinstance(
        summary["before_restart"]["storage_model"]["compact_smaller_than_raw"],
        bool,
    )


def test_postgres_missing_dependency_uses_structured_skip(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    proof = _load_telemetry_load_proof()
    monkeypatch.setattr(proof.shutil, "which", lambda _: None)

    report = proof.run_selected_backends(_args(tmp_path, "postgres"))

    assert report["status"] == "pass"
    assert report["skipped"] == 1
    assert report["results"][0]["backend"] == "postgres"
    assert report["results"][0]["status"] == "skipped"


def test_unexpected_backend_errors_become_structured_failures(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    proof = _load_telemetry_load_proof()

    def boom(*_args: object, **_kwargs: object) -> dict[str, object]:
        raise RuntimeError("load proof exploded")

    monkeypatch.setattr(proof, "run_memory_load_proof", boom)
    args = _args(tmp_path, "memory")
    args.allow_missing_optional = False

    report = proof.run_selected_backends(args)

    assert report["status"] == "failed"
    assert report["failed"] == 1
    assert report["results"][0]["backend"] == "memory"
    assert "load proof exploded" in report["results"][0]["error"]
