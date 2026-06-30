"""Small crash-safe filesystem helpers used by public checkpoint APIs."""

from __future__ import annotations

import json
import os
import stat
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

MAX_SERIALIZED_STATE_BYTES = 32 * 1024 * 1024
_WINDOWS_IO_RETRY_SECONDS = 5.0
_WINDOWS_IO_RETRY_INTERVAL_SECONDS = 0.01
_CHECKPOINT_LOCKS = tuple(threading.RLock() for _ in range(64))


def _checkpoint_lock(path: str | Path) -> threading.RLock:
    """Return a bounded process-local lock shared by equivalent paths."""
    canonical = os.path.normcase(os.path.abspath(os.fspath(path)))
    return _CHECKPOINT_LOCKS[hash(canonical) % len(_CHECKPOINT_LOCKS)]


def atomic_write_json(path: str, data: Any) -> None:
    """Write JSON without ever exposing a partially-written destination."""
    destination = Path(path)
    with _checkpoint_lock(destination):
        _atomic_write_json_locked(destination, data)


def _atomic_write_json_locked(destination: Path, data: Any) -> None:
    """Write a checkpoint while its same-path process lock is held."""
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
        retry_deadline = time.monotonic() + _WINDOWS_IO_RETRY_SECONDS
        while True:
            try:
                os.replace(temporary, destination)
                break
            except PermissionError:
                if os.name != "nt" or time.monotonic() >= retry_deadline:
                    raise
                time.sleep(_WINDOWS_IO_RETRY_INTERVAL_SECONDS)
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
    with _checkpoint_lock(path):
        retry_deadline = time.monotonic() + _WINDOWS_IO_RETRY_SECONDS
        while True:
            try:
                with open(path, "rb") as handle:
                    payload = handle.read(MAX_SERIALIZED_STATE_BYTES + 1)
                if len(payload) > MAX_SERIALIZED_STATE_BYTES:
                    raise ValueError(
                        "Serialized checkpoint exceeds the 32 MiB limit")
                return json.loads(payload)
            except PermissionError:
                if os.name != "nt" or time.monotonic() >= retry_deadline:
                    raise
                time.sleep(_WINDOWS_IO_RETRY_INTERVAL_SECONDS)
