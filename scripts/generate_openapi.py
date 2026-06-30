"""Generate or verify the canonical public OpenAPI contract."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))

from sketchlog.server import app  # noqa: E402


def rendered_contract() -> str:
    return json.dumps(
        app.openapi(), indent=2, sort_keys=True, ensure_ascii=False
    ) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--check", action="store_true",
        help="fail if protocol/openapi.yaml is not current")
    args = parser.parse_args()
    destination = ROOT / "protocol" / "openapi.yaml"
    generated = rendered_contract()

    if args.check:
        existing = destination.read_text(encoding="utf-8")
        if existing != generated:
            print(
                "protocol/openapi.yaml is stale; run "
                "`python scripts/generate_openapi.py`",
                file=sys.stderr,
            )
            return 1
        return 0

    destination.write_text(generated, encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
