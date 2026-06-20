import json
import os
import sys

# Ensure sketchlog is in path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "python")))

from sketchlog import StreamLog

def generate():
    log = StreamLog(deterministic=True)
    log.add_latency(10.5)
    log.add_latency(100.0)
    log.add_event("login")
    log.add_event("login")
    log.add_event("logout")
    log.add_unique("user123")
    log.add_unique("user456")

    fixture_path = os.path.join(os.path.dirname(__file__), "..", "tests", "fixtures", "v1_snapshot.json")
    with open(fixture_path, "w") as f:
        json.dump(log.to_dict(), f, separators=(",", ":"))

    print(f"Generated {fixture_path}")

if __name__ == "__main__":
    generate()
