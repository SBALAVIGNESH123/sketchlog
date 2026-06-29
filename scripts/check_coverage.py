"""Enforce subsystem coverage floors for high-risk runtime paths."""

from __future__ import annotations

import json
from pathlib import Path

FLOORS = {
    "python/sketchlog/server.py": 64.0,
    "python/sketchlog/cluster.py": 43.0,
    "python/sketchlog/storage.py": 80.0,
    "python/sketchlog/concurrent.py": 57.0,
}


def main() -> int:
    report = json.loads(Path("coverage.json").read_text(encoding="utf-8"))
    normalized = {
        name.replace("\\", "/"): values
        for name, values in report["files"].items()
    }
    failures = []
    for filename, floor in FLOORS.items():
        measured = normalized[filename]["summary"]["percent_covered"]
        if measured < floor:
            failures.append(f"{filename}: {measured:.2f}% < {floor:.2f}%")
    if failures:
        raise SystemExit("Critical coverage regression:\n- " + "\n- ".join(failures))
    print("Critical subsystem coverage floors passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
