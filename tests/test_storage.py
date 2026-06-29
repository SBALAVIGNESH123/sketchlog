import pytest
import pytest_asyncio
import asyncio
import zlib
from sketchlog.storage import SQLAlchemyStorage
from sketchlog.facade import StreamLog
from sketchlog.concurrent import ThreadSafeStreamLog
import os

from typing import AsyncGenerator

@pytest_asyncio.fixture
async def storage() -> AsyncGenerator[SQLAlchemyStorage, None]:
    # Use an in-memory SQLite database for testing
    backend = SQLAlchemyStorage("sqlite+aiosqlite:///:memory:")
    await backend.initialize()
    yield backend
    await backend.close()

@pytest.mark.asyncio
async def test_storage_save_load(storage: SQLAlchemyStorage) -> None:
    # Create a stream and add some data
    log = StreamLog()
    log.add_latency(100.0)
    log.add_latency(200.0)
    log.add_latency(300.0)

    # Save to storage
    await storage.save("test_ns", "test_stream", log)

    # Load from storage
    loaded_log = await storage.load("test_ns", "test_stream")

    assert loaded_log is not None
    assert isinstance(loaded_log, ThreadSafeStreamLog)

    # Check if data matches
    assert loaded_log.total_events == 3
    # Wait, we need to assert actual percentiles to be sure
    assert abs(loaded_log.p50() - 200.0) < 2.5

@pytest.mark.asyncio
async def test_storage_not_found(storage: SQLAlchemyStorage) -> None:
    loaded_log = await storage.load("test_ns", "nonexistent")
    assert loaded_log is None

@pytest.mark.asyncio
async def test_storage_delete(storage: SQLAlchemyStorage) -> None:
    log = StreamLog()
    log.add_latency(50.0)
    await storage.save("test_ns", "del_stream", log)

    loaded = await storage.load("test_ns", "del_stream")
    assert loaded is not None

    await storage.delete("test_ns", "del_stream")

    loaded_after = await storage.load("test_ns", "del_stream")
    assert loaded_after is None


@pytest.mark.asyncio
async def test_mesh_tombstones_survive_storage_restart(storage: SQLAlchemyStorage) -> None:
    await storage.save_tombstone("node-a", '["default", "gone"]', 42.5)
    await storage.save_tombstone("node-a", '["default", "gone"]', 41.0)
    await storage.save_tombstone("node-a", '["default", "gone"]', 43.0)
    assert await storage.load_tombstones("node-a") == {
        '["default", "gone"]': 43.0
    }


@pytest.mark.asyncio
async def test_stream_delete_and_mesh_tombstone_commit_together(
        storage: SQLAlchemyStorage) -> None:
    log = StreamLog()
    log.add_latency(12.0)
    await storage.save("default", "atomic-delete", log)

    deleted = await storage.delete_with_tombstone(
        "default",
        "atomic-delete",
        "node-a",
        '["default", "atomic-delete"]',
        99.0,
    )

    assert deleted is True
    assert await storage.load("default", "atomic-delete") is None
    tombstones = await storage.load_tombstones("node-a")
    assert tombstones['["default", "atomic-delete"]'] == 99.0

    deleted_again = await storage.delete_with_tombstone(
        "default",
        "atomic-delete",
        "node-a",
        '["default", "atomic-delete"]',
        100.0,
    )
    assert deleted_again is False
    tombstones = await storage.load_tombstones("node-a")
    assert tombstones['["default", "atomic-delete"]'] == 100.0


@pytest.mark.asyncio
async def test_storage_healthcheck(storage: SQLAlchemyStorage) -> None:
    assert await storage.healthcheck() is True


def test_storage_rejects_decompression_bombs(monkeypatch) -> None:
    import sketchlog.storage as storage_module

    backend = SQLAlchemyStorage("sqlite+aiosqlite:///:memory:")
    monkeypatch.setattr(storage_module, "MAX_SERIALIZED_STATE_BYTES", 64)
    payload = zlib.compress(b'{"value":"' + b"x" * 128 + b'"}')

    with pytest.raises(ValueError, match="Decompressed state exceeds"):
        backend._decompress_and_deserialize(payload)
