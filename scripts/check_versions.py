"""Fail when release-coupled artifact versions drift."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def match(path: str, pattern: str) -> str:
    text = (ROOT / path).read_text(encoding="utf-8")
    found = re.search(pattern, text, re.MULTILINE)
    if not found:
        raise SystemExit(f"Could not find version in {path}")
    return found.group(1)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag")
    args = parser.parse_args()

    versions = {
        "pyproject.toml": match(
            "pyproject.toml", r'^version\s*=\s*"([^"]+)"'),
        "python/sketchlog/__init__.py": match(
            "python/sketchlog/__init__.py", r'__version__\s*=\s*"([^"]+)"'),
        "charts/sketchlog appVersion": match(
            "charts/sketchlog/Chart.yaml", r'^appVersion:\s*"?([^"\s]+)"?'),
        "charts/sketchlog chart": match(
            "charts/sketchlog/Chart.yaml", r'^version:\s*"?([^"\s]+)"?'),
        "clients/typescript": json.loads(
            (ROOT / "clients/typescript/package.json").read_text())["version"],
        "frontend/react-sketchlog": json.loads(
            (ROOT / "frontend/react-sketchlog/package.json").read_text())["version"],
        "wasm": json.loads(
            (ROOT / "wasm/package.json").read_text())["version"],
        "protocol/openapi.yaml": json.loads(
            (ROOT / "protocol/openapi.yaml").read_text())["info"]["version"],
    }
    expected = versions["pyproject.toml"]
    mismatches = {
        artifact: version for artifact, version in versions.items()
        if version != expected
    }
    if mismatches:
        details = ", ".join(
            f"{artifact}={version}" for artifact, version in mismatches.items())
        raise SystemExit(f"Release version drift (expected {expected}): {details}")
    if args.tag and args.tag not in (f"v{expected}", f"test-v{expected}"):
        raise SystemExit(
            f"Release tag {args.tag!r} does not match version {expected}")
    print(f"All coupled artifacts use version {expected}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
