"""Fail CI when reproducible benchmark guarantees regress."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def results(path: str) -> dict[str, dict[str, Any]]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return {entry["name"]: entry for entry in payload["results"]}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--throughput", required=True)
    parser.add_argument("--accuracy", required=True)
    parser.add_argument("--items", type=int, required=True)
    args = parser.parse_args()

    throughput = results(args.throughput)
    accuracy = results(args.accuracy)
    failures: list[str] = []

    for name in ("python_scalar_add", "cpp_scalar_add"):
        if name not in throughput:
            failures.append(f"missing required throughput result {name}")
            continue
        result = throughput[name]
        if result["samples"] < 5:
            failures.append(f"{name} has fewer than 5 measured samples")
        p95_rate = args.items / result["p95"]
        if p95_rate < 250_000:
            failures.append(
                f"{name} p95 throughput {p95_rate:.0f}/s is below 250000/s")

    limits = {
        "latency_p99_error_percent": 1.0,
        "zipf_unique_count_error_percent": 10.0,
        "merge_skew_memory_kb": 130.0,
    }
    for name, maximum in limits.items():
        if name not in accuracy:
            failures.append(f"missing required accuracy result {name}")
        elif accuracy[name]["mean"] > maximum:
            failures.append(
                f"{name}={accuracy[name]['mean']:.4f} exceeds {maximum}")

    if failures:
        raise SystemExit("Benchmark regression:\n- " + "\n- ".join(failures))
    print("All benchmark regression gates passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
