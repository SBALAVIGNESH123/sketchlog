import sys
from pathlib import Path


def test_memory_storage_proof_documents_ephemeral_restart(tmp_path: Path) -> None:
    scripts_dir = Path(__file__).resolve().parents[1] / "scripts"
    sys.path.insert(0, str(scripts_dir))
    try:
        from storage_proof import run_memory_proof

        summary = run_memory_proof(tmp_path / "proof")
    finally:
        sys.path.remove(str(scripts_dir))

    assert summary["backend"] == "memory"
    assert summary["status"] == "pass"
    assert summary["events_before_restart"] >= 12
    assert summary["events_after_restart"] is None
    assert summary["restart_behavior"] == "ephemeral_expected_missing"
    assert summary["delete_verified"] is True
    assert summary["tombstone"]["durable"] is False
