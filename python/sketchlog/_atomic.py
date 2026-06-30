"""Small crash-safe filesystem helpers used by public checkpoint APIs."""

from __future__ import annotations

import json
import os
import stat
import tempfile
import time
from pathlib import Path
from typing import Any

MAX_SERIALIZED_STATE_BYTES = 32 * 1024 * 1024


def atomic_write_json(path: str, data: Any) -> None:
    """Write JSON without ever exposing a partially-written destination."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    mode = stat.S_IMODE(destination.stat().st_mode) if destination.exists() else 0o600
    temporary: str | None = None

    try:
        fd, temporary = tempfile.mkstemp(
            prefix=f".{destination.name}.",
            suffix=".tmp",
            dir=str(destination.parent),
        )
        try:
            os.chmod(temporary, mode)
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(data, handle, allow_nan=False, separators=(",", ":"))
                handle.flush()
                os.fsync(handle.fileno())
            if os.path.getsize(temporary) > MAX_SERIALIZED_STATE_BYTES:
                raise ValueError(
                    "Serialized checkpoint exceeds the 32 MiB limit")
        except BaseException:
            try:
                os.close(fd)
            except OSError:
                pass
            raise

        # Windows denies replacement during a reader's brief non-delete-share
        # window. Retry that transient condition without ever truncating the
        # valid destination.
        for attempt in range(100):
            try:
                os.replace(temporary, destination)
                break
            except PermissionError:
                if os.name != "nt" or attempt == 99:
                    raise
                time.sleep(0.01)
        temporary = None

        if os.name != "nt":
            directory_fd = os.open(destination.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass


def read_json_checkpoint(path: str) -> Any:
    """Read across Windows atomic-replace sharing windows."""
    for attempt in range(100):
        try:
            with open(path, "rb") as handle:
                payload = handle.read(MAX_SERIALIZED_STATE_BYTES + 1)
            if len(payload) > MAX_SERIALIZED_STATE_BYTES:
                raise ValueError("Serialized checkpoint exceeds the 32 MiB limit")
            return json.loads(payload)
        except PermissionError:
            if os.name != "nt" or attempt == 99:
                raise
            time.sleep(0.01)
    raise RuntimeError("unreachable")
