import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


def _load_storage_proof():
    scripts_dir = Path(__file__).resolve().parents[1] / "scripts"
    sys.path.insert(0, str(scripts_dir))
    try:
        import storage_proof

        return storage_proof
    finally:
        sys.path.remove(str(scripts_dir))


def _args(tmp_path: Path, *backends: str) -> SimpleNamespace:
    return SimpleNamespace(
        backend=list(backends),
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


def test_memory_storage_proof_documents_ephemeral_restart(tmp_path: Path) -> None:
    storage_proof = _load_storage_proof()

    summary = storage_proof.run_memory_proof(tmp_path / "proof")

    assert summary["backend"] == "memory"
    assert summary["status"] == "pass"
    assert summary["events_before_restart"] >= 12
    assert summary["events_after_restart"] is None
    assert summary["restart_behavior"] == "ephemeral_expected_missing"
    assert summary["delete_verified"] is True
    assert summary["tombstone"]["durable"] is False


def test_storage_proof_metadata_is_share_safe() -> None:
    storage_proof = _load_storage_proof()

    metadata = storage_proof.environment_metadata()

    assert "repo_root" not in metadata
    assert metadata["repo"] == "sketchlog"
    assert "\\" not in metadata["repo"]
    assert "/" not in metadata["repo"]


def test_postgres_missing_dependency_uses_canonical_backend_id(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    storage_proof = _load_storage_proof()
    monkeypatch.setattr(storage_proof.shutil, "which", lambda _: None)

    report = storage_proof.run_selected_backends(_args(tmp_path, "postgres"))

    assert report["status"] == "pass"
    assert report["skipped"] == 1
    assert report["results"][0]["backend"] == "postgres"
    assert report["results"][0]["status"] == "skipped"


def test_postgres_docker_probe_timeout_is_actionable(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    storage_proof = _load_storage_proof()
    monkeypatch.setattr(storage_proof.shutil, "which", lambda _: "docker")

    def timeout_run(*_args: object, **_kwargs: object) -> object:
        raise storage_proof.subprocess.TimeoutExpired(
            cmd="docker info", timeout=15)

    monkeypatch.setattr(storage_proof.subprocess, "run", timeout_run)

    with pytest.raises(storage_proof.BackendUnavailable, match="timed out"):
        storage_proof.run_postgres_proof(
            tmp_path / "compose.yml",
            "http://127.0.0.1:4180",
            start=True,
            stop=True,
        )


def test_unexpected_backend_errors_become_structured_failures(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    storage_proof = _load_storage_proof()

    def boom(_work_dir: Path) -> dict[str, object]:
        raise RuntimeError("connection exploded")

    monkeypatch.setattr(storage_proof, "run_memory_proof", boom)
    args = _args(tmp_path, "memory")
    args.allow_missing_optional = False

    report = storage_proof.run_selected_backends(args)

    assert report["status"] == "failed"
    assert report["failed"] == 1
    assert report["results"][0]["backend"] == "memory"
    assert report["results"][0]["status"] == "failed"
    assert "connection exploded" in report["results"][0]["error"]
