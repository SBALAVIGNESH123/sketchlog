import os
import json
from sketchlog import StreamLog

def test_v1_snapshot_compatibility():
    """Ensure we can always read v1 snapshots from previous versions."""
    fixture_path = os.path.join(os.path.dirname(__file__), "fixtures", "v1_snapshot.json")

    with open(fixture_path, "r") as f:
        data = json.load(f)

    log = StreamLog.from_dict(data)

    # Assert values from the generator script
    assert log.p99() >= 100.0
    assert log.event_count("login") >= 2
    assert log.event_count("logout") >= 1
    assert log.unique_count() == 2
