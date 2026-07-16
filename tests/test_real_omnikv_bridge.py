import importlib.util
import sys
from pathlib import Path

import pytest


pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("omnikv") is None,
    reason="real OmniKV Python bridge is not installed",
)


def test_real_omnikv_bridge_smoke(tmp_path: Path) -> None:
    scripts_dir = Path(__file__).resolve().parents[1] / "scripts"
    sys.path.insert(0, str(scripts_dir))
    try:
        from omnikv_bridge_smoke import _run_smoke

        import asyncio

        summary = asyncio.run(_run_smoke(tmp_path / "omnikv", "omnikv", "sketchlog"))
    finally:
        sys.path.remove(str(scripts_dir))

    assert summary["backend"] == "omnikv"
    assert summary["bridge_module"] == "omnikv"
    assert summary["total_events_after_reopen"] == 52
    assert summary["unique_count_after_reopen"] == 3
    assert summary["tombstone_version_after_reopen"] == 123.0
    assert summary["deleted_stream_resurrected"] is False
