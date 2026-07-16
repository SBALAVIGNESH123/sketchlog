"""Smoke-test SketchLog against a real OmniKV Python/native bridge."""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import tempfile
from pathlib import Path
from typing import Any

from sketchlog.facade import StreamLog
from sketchlog.storage import OmniKVEmbeddedStorage


FIXTURE_NAMESPACE = "proof"
FIXTURE_STREAM = "checkout-latency"
FIXTURE_NODE = "omnikv-proof-node"
FIXTURE_STREAM_KEY = f"{FIXTURE_NAMESPACE}/{FIXTURE_STREAM}"


def _build_log() -> StreamLog:
    log = StreamLog()
    for latency in (12.0, 18.0, 22.0, 31.0, 45.0, 65.0, 89.0, 144.0):
        log.add_latency(latency)
    for user in ("user-a", "user-b", "user-c"):
        log.add_unique(user)
    log.add_event("ok", 42)
    log.add_event("error", 2)
    return log


async def _run_smoke(data_dir: Path, module_name: str, namespace: str) -> dict[str, Any]:
    storage = OmniKVEmbeddedStorage(
        data_dir=data_dir,
        namespace=namespace,
        module_name=module_name,
    )
    await storage.initialize()
    try:
        await storage.save(FIXTURE_NAMESPACE, FIXTURE_STREAM, _build_log())
    finally:
        await storage.close()

    reopened = OmniKVEmbeddedStorage(
        data_dir=data_dir,
        namespace=namespace,
        module_name=module_name,
    )
    await reopened.initialize()
    try:
        loaded = await reopened.load(FIXTURE_NAMESPACE, FIXTURE_STREAM)
        if loaded is None:
            raise AssertionError("saved stream did not survive OmniKV reopen")
        if loaded.total_events != 52:
            raise AssertionError(
                f"unexpected total_events after reopen: {loaded.total_events}")
        if loaded.unique_count() != 3:
            raise AssertionError(
                f"unexpected unique_count after reopen: {loaded.unique_count()}")

        deleted = await reopened.delete_with_tombstone(
            FIXTURE_NAMESPACE,
            FIXTURE_STREAM,
            FIXTURE_NODE,
            FIXTURE_STREAM_KEY,
            123.0,
        )
        if not deleted:
            raise AssertionError("delete_with_tombstone did not delete saved state")
        tombstones = await reopened.load_tombstones(FIXTURE_NODE)
        if tombstones.get(FIXTURE_STREAM_KEY) != 123.0:
            raise AssertionError(f"missing tombstone after delete: {tombstones}")
    finally:
        await reopened.close()

    final = OmniKVEmbeddedStorage(
        data_dir=data_dir,
        namespace=namespace,
        module_name=module_name,
    )
    await final.initialize()
    try:
        resurrected = await final.load(FIXTURE_NAMESPACE, FIXTURE_STREAM)
        if resurrected is not None:
            raise AssertionError("deleted stream resurrected after second reopen")
        tombstones = await final.load_tombstones(FIXTURE_NODE)
        if tombstones.get(FIXTURE_STREAM_KEY) != 123.0:
            raise AssertionError(f"tombstone did not survive second reopen: {tombstones}")
    finally:
        await final.close()

    return {
        "backend": "omnikv",
        "bridge_module": module_name,
        "embedded_namespace": namespace,
        "stream_namespace": FIXTURE_NAMESPACE,
        "stream_id": FIXTURE_STREAM,
        "total_events_after_reopen": 52,
        "unique_count_after_reopen": 3,
        "tombstone_version_after_reopen": 123.0,
        "deleted_stream_resurrected": False,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Verify SketchLog's OmniKV storage backend with a real installed "
            "OmniKV Python/native bridge."
        )
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        help="OmniKV data directory. Defaults to a temporary directory.",
    )
    parser.add_argument(
        "--module",
        default="omnikv",
        help="Python module exposing the OmniKV bridge contract.",
    )
    parser.add_argument(
        "--namespace",
        default="sketchlog",
        help="Embedded OmniKV namespace for SketchLog keys.",
    )
    parser.add_argument(
        "--keep-data",
        action="store_true",
        help="Keep the temporary data directory after a successful smoke.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    created_temp_dir = False
    if args.data_dir is None:
        data_dir = Path(tempfile.mkdtemp(prefix="sketchlog-omnikv-"))
        created_temp_dir = True
    else:
        data_dir = args.data_dir
        data_dir.mkdir(parents=True, exist_ok=True)

    try:
        summary = asyncio.run(_run_smoke(data_dir, args.module, args.namespace))
        summary["data_dir"] = str(data_dir)
        print(json.dumps(summary, indent=2, sort_keys=True))
        print("PASS SketchLog OmniKV bridge smoke")
        return 0
    finally:
        if created_temp_dir and not args.keep_data:
            shutil.rmtree(data_dir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
