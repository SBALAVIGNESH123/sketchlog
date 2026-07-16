import importlib.util
import sys
from pathlib import Path

import pytest


pytestmark = [
    pytest.mark.omnikv_real,
    pytest.mark.skipif(
        importlib.util.find_spec("omnikv") is None,
        reason="real OmniKV Python bridge is not installed",
    ),
    pytest.mark.skipif(
        importlib.util.find_spec("uvicorn") is None,
        reason="uvicorn is required for the SketchLog OmniKV E2E smoke",
    ),
]


def test_real_omnikv_end_to_end_storage_smoke(tmp_path: Path) -> None:
    scripts_dir = Path(__file__).resolve().parents[1] / "scripts"
    sys.path.insert(0, str(scripts_dir))
    try:
        from omnikv_e2e_smoke import run_smoke

        summary = run_smoke(tmp_path / "omnikv", "omnikv", "sketchlog")
    finally:
        sys.path.remove(str(scripts_dir))

    assert summary["backend"] == "omnikv"
    assert summary["bridge_module"] == "omnikv"
    assert summary["events_after_restart"] == summary["events_before_restart"]
    assert summary["unique_count"] == 4
    assert summary["p99"] >= summary["p50"]
    assert summary["stream_key"] == '["proof", "checkout-latency"]'
    assert summary["tombstone_version_after_reopen"] > 0
    assert summary["deleted_stream_resurrected"] is False
