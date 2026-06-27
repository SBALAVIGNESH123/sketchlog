import pytest
import pytest_asyncio
import asyncio
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
