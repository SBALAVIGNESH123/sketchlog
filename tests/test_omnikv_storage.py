import sys
from pathlib import Path
from types import ModuleType
from typing import Any, Dict, List, Optional, Tuple

import pytest

from sketchlog.concurrent import ThreadSafeStreamLog
from sketchlog.facade import StreamLog
from sketchlog.storage import (
    OmniKVEmbeddedStorage,
    StorageBackend,
    StorageConfigurationError,
    _parse_state_envelope,
    _state_envelope,
    _state_key,
    _tombstone_key,
)


class _FakeOmniKVClient:
    _stores: Dict[Tuple[str, str], Dict[str, str]] = {}

    def __init__(self, data_dir: str, namespace: str) -> None:
        self.data_dir = str(Path(data_dir))
        self.namespace = namespace
        self.closed = False
        self.synced = False
        self.operations: List[str] = []
        self.store = self._stores.setdefault((self.data_dir, namespace), {})

    def put(self, key: str, value: str) -> int:
        self.operations.append(f"put:{key}")
        self.store[key] = value
        return len(self.store)

    def get(self, key: str) -> Optional[str]:
        self.operations.append(f"get:{key}")
        return self.store.get(key)

    def delete(self, key: str) -> int:
        self.operations.append(f"delete:{key}")
        self.store.pop(key, None)
        return len(self.store)

    def scan_prefix(
            self, prefix: str, limit: Optional[int] = None) -> List[Dict[str, str]]:
        rows = [
            {"key": key, "value": value}
            for key, value in sorted(self.store.items())
            if key.startswith(prefix)
        ]
        return rows if limit is None else rows[:limit]

    def sync(self) -> None:
        self.operations.append("sync")
        self.synced = True

    def close(self) -> None:
        self.operations.append("close")
        self.closed = True

    def stats(self) -> Dict[str, int]:
        return {"total_records": len(self.store)}


def _install_fake_omnikv_module(name: str) -> None:
    module = ModuleType(name)

    def open_embedded(data_dir: str, namespace: str = "sketchlog") -> _FakeOmniKVClient:
        return _FakeOmniKVClient(data_dir, namespace)

    module.open_embedded = open_embedded  # type: ignore[attr-defined]
    sys.modules[name] = module


class _ObjectRow:
    def __init__(self, key: str, value: str) -> None:
        self.key = key
        self.value = value


class _NoStatsClient(_FakeOmniKVClient):
    stats = None


class _SyncFailingClient(_FakeOmniKVClient):
    def sync(self) -> None:
        super().sync()
        raise RuntimeError("sync failed")


class _MissingScanClient:
    def put(self, key: str, value: str) -> None:
        return None

    def get(self, key: str) -> Optional[str]:
        return None

    def delete(self, key: str) -> None:
        return None


@pytest.fixture
def fake_omnikv_module() -> str:
    name = "_sketchlog_fake_omnikv"
    _FakeOmniKVClient._stores.clear()
    _install_fake_omnikv_module(name)
    yield name
    sys.modules.pop(name, None)
    _FakeOmniKVClient._stores.clear()


@pytest.mark.asyncio
async def test_omnikv_storage_save_load_survives_reopen(
        tmp_path: Path, fake_omnikv_module: str) -> None:
    storage = OmniKVEmbeddedStorage(
        tmp_path / "omnikv", module_name=fake_omnikv_module)
    await storage.initialize()

    log = StreamLog()
    log.add_batch([10.0, 20.0, 30.0])
    log.add_event("error", 2)
    log.add_unique("user-1")
    await storage.save("prod", "api.latency", log)
    await storage.close()

    reopened = OmniKVEmbeddedStorage(
        tmp_path / "omnikv", module_name=fake_omnikv_module)
    await reopened.initialize()
    loaded = await reopened.load("prod", "api.latency")

    assert isinstance(loaded, ThreadSafeStreamLog)
    assert loaded.total_events == 5
    assert abs(loaded.p50() - 20.0) < 1.0
    assert loaded.event_count("error") == 2
    assert loaded.unique_count() == 1
    await reopened.close()


@pytest.mark.asyncio
async def test_omnikv_storage_preserves_sketchlog_namespace_isolation(
        tmp_path: Path, fake_omnikv_module: str) -> None:
    storage = OmniKVEmbeddedStorage(
        tmp_path / "omnikv", module_name=fake_omnikv_module)
    await storage.initialize()

    prod = StreamLog()
    prod.add_latency(100.0)
    staging = StreamLog()
    staging.add_latency(5.0)

    await storage.save("prod", "checkout", prod)
    await storage.save("staging", "checkout", staging)

    loaded_prod = await storage.load("prod", "checkout")
    loaded_staging = await storage.load("staging", "checkout")

    assert loaded_prod is not None
    assert loaded_staging is not None
    assert loaded_prod.p50() > loaded_staging.p50()
    await storage.close()


@pytest.mark.asyncio
async def test_omnikv_storage_delete_and_tombstones(
        tmp_path: Path, fake_omnikv_module: str) -> None:
    storage = OmniKVEmbeddedStorage(
        tmp_path / "omnikv", module_name=fake_omnikv_module)
    await storage.initialize()

    log = StreamLog()
    log.add_latency(42.0)
    await storage.save("default", "gone", log)

    deleted = await storage.delete_with_tombstone(
        "default",
        "gone",
        "node-a",
        '["default","gone"]',
        12.0,
    )

    assert deleted is True
    assert await storage.load("default", "gone") is None
    assert await storage.load_tombstones("node-a") == {
        '["default","gone"]': 12.0
    }

    await storage.save_tombstone("node-a", '["default","gone"]', 11.0)
    await storage.save_tombstone("node-a", '["default","gone"]', 13.0)
    assert await storage.load_tombstones("node-a") == {
        '["default","gone"]': 13.0
    }
    await storage.close()


@pytest.mark.asyncio
async def test_omnikv_close_closes_native_client_when_sync_fails(
        tmp_path: Path) -> None:
    module_name = "_sketchlog_fake_omnikv_sync_fails"
    module = ModuleType(module_name)

    def open_embedded(
            data_dir: str, namespace: str = "sketchlog"
    ) -> _SyncFailingClient:
        return _SyncFailingClient(data_dir, namespace)

    module.open_embedded = open_embedded  # type: ignore[attr-defined]
    sys.modules[module_name] = module
    try:
        storage = OmniKVEmbeddedStorage(tmp_path / "db", module_name=module_name)
        await storage.initialize()
        client = storage._require_client()

        with pytest.raises(RuntimeError, match="sync failed"):
            await storage.close()

        assert client.closed is True
        assert storage._client is None
        assert client.operations[-2:] == ["sync", "close"]
    finally:
        sys.modules.pop(module_name, None)


@pytest.mark.asyncio
async def test_omnikv_storage_healthcheck_and_missing_bridge(
        tmp_path: Path, fake_omnikv_module: str) -> None:
    storage = OmniKVEmbeddedStorage(
        tmp_path / "omnikv", module_name=fake_omnikv_module)
    await storage.initialize()
    assert await storage.healthcheck() is True
    await storage.close()

    missing = OmniKVEmbeddedStorage(
        tmp_path / "missing", module_name="_missing_omnikv_bridge")
    with pytest.raises(StorageConfigurationError, match="Unable to import"):
        await missing.initialize()


@pytest.mark.asyncio
async def test_omnikv_storage_rejects_bad_namespace_and_missing_methods(
        tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="OmniKV namespace"):
        OmniKVEmbeddedStorage(tmp_path, namespace="bad namespace")

    module_name = "_sketchlog_fake_omnikv_missing_scan"
    module = ModuleType(module_name)
    module.open_embedded = lambda data_dir, namespace="sketchlog": _MissingScanClient()  # type: ignore[attr-defined]
    sys.modules[module_name] = module
    try:
        storage = OmniKVEmbeddedStorage(tmp_path / "db", module_name=module_name)
        with pytest.raises(StorageConfigurationError, match="scan_prefix"):
            await storage.initialize()
    finally:
        sys.modules.pop(module_name, None)


@pytest.mark.asyncio
async def test_omnikv_storage_supports_embedded_class_bridge_shapes(
        tmp_path: Path) -> None:
    module_name = "_sketchlog_fake_omnikv_class_bridge"
    module = ModuleType(module_name)

    class EmbeddedOmniKv:
        @staticmethod
        def open(data_dir: str, namespace: str = "sketchlog") -> _FakeOmniKVClient:
            return _FakeOmniKVClient(data_dir, namespace)

    module.EmbeddedOmniKv = EmbeddedOmniKv  # type: ignore[attr-defined]
    sys.modules[module_name] = module
    try:
        storage = OmniKVEmbeddedStorage(tmp_path / "open", module_name=module_name)
        await storage.initialize()
        assert await storage.healthcheck() is True
        await storage.close()
    finally:
        sys.modules.pop(module_name, None)


@pytest.mark.asyncio
async def test_omnikv_storage_supports_open_dir_and_default_embedded_fallback(
        tmp_path: Path) -> None:
    module_name = "_sketchlog_fake_omnikv_open_dir"
    module = ModuleType(module_name)

    class EmbeddedOmniKv:
        @staticmethod
        def open(data_dir: str, namespace: str = "sketchlog") -> _FakeOmniKVClient:
            raise TypeError("older bridge shape")

        @staticmethod
        def open_dir(data_dir: str) -> _FakeOmniKVClient:
            return _FakeOmniKVClient(data_dir, "unscoped")

    module.EmbeddedOmniKv = EmbeddedOmniKv  # type: ignore[attr-defined]
    sys.modules[module_name] = module
    try:
        storage = OmniKVEmbeddedStorage(tmp_path / "open-dir", module_name=module_name)
        await storage.initialize()
        assert await storage.healthcheck() is True
        await storage.close()
    finally:
        sys.modules.pop(module_name, None)

    package = ModuleType("omnikv")
    package.__path__ = []  # type: ignore[attr-defined]
    embedded = ModuleType("omnikv.embedded")
    embedded.open_embedded = (  # type: ignore[attr-defined]
        lambda data_dir, namespace="sketchlog": _FakeOmniKVClient(data_dir, namespace)
    )
    sys.modules["omnikv"] = package
    sys.modules["omnikv.embedded"] = embedded
    try:
        storage = OmniKVEmbeddedStorage(tmp_path / "default", module_name="omnikv")
        await storage.initialize()
        assert await storage.healthcheck() is True
        await storage.close()
    finally:
        sys.modules.pop("omnikv.embedded", None)
        sys.modules.pop("omnikv", None)


@pytest.mark.asyncio
async def test_omnikv_storage_fails_when_bridge_has_no_factory(
        tmp_path: Path) -> None:
    module_name = "_sketchlog_fake_omnikv_no_factory"
    sys.modules[module_name] = ModuleType(module_name)
    try:
        storage = OmniKVEmbeddedStorage(tmp_path / "db", module_name=module_name)
        with pytest.raises(StorageConfigurationError, match="open_embedded"):
            await storage.initialize()
    finally:
        sys.modules.pop(module_name, None)


@pytest.mark.asyncio
async def test_omnikv_storage_stale_and_corrupt_state_paths(
        tmp_path: Path, fake_omnikv_module: str) -> None:
    storage = OmniKVEmbeddedStorage(
        tmp_path / "omnikv", module_name=fake_omnikv_module)
    await storage.initialize()
    client = storage._require_client()

    fresh = ThreadSafeStreamLog()
    fresh.add_latency(100.0)
    fresh.last_updated = 200.0
    await storage.save("prod", "latency", fresh)

    stale = ThreadSafeStreamLog()
    stale.add_latency(1.0)
    stale.last_updated = 100.0
    await storage.save("prod", "latency", stale)
    loaded = await storage.load("prod", "latency")
    assert loaded is not None
    assert loaded.p50() > 50.0

    client.put(_state_key("prod", "latency"), "not-json")
    replacement = ThreadSafeStreamLog()
    replacement.add_latency(2.0)
    replacement.last_updated = 300.0
    await storage.save("prod", "latency", replacement)
    loaded_replacement = await storage.load("prod", "latency")
    assert loaded_replacement is not None
    assert loaded_replacement.p50() < 3.0

    client.put(_state_key("prod", "broken"), "not-json")
    assert await storage.load("prod", "broken") is None
    assert await storage.delete("prod", "missing") is False
    await storage.close()


@pytest.mark.asyncio
async def test_omnikv_storage_healthcheck_fallback_and_failure(
        tmp_path: Path) -> None:
    module_name = "_sketchlog_fake_omnikv_no_stats"
    module = ModuleType(module_name)
    module.open_embedded = lambda data_dir, namespace="sketchlog": _NoStatsClient(data_dir, namespace)  # type: ignore[attr-defined]
    sys.modules[module_name] = module
    try:
        storage = OmniKVEmbeddedStorage(tmp_path / "db", module_name=module_name)
        await storage.initialize()
        assert await storage.healthcheck() is True
        await storage.close()
        assert await storage.healthcheck() is False
        await storage.close()
    finally:
        sys.modules.pop(module_name, None)


@pytest.mark.asyncio
async def test_omnikv_tombstone_invalid_rows_and_existing_versions(
        tmp_path: Path, fake_omnikv_module: str) -> None:
    storage = OmniKVEmbeddedStorage(
        tmp_path / "omnikv", module_name=fake_omnikv_module)
    await storage.initialize()
    client = storage._require_client()

    await storage.save_tombstone("node-a", "stream-a", 5.0)
    await storage.save_tombstone("node-a", "stream-a", 4.0)
    assert await storage.load_tombstones("node-a") == {"stream-a": 5.0}

    client.put("sketchlog/v1/tombstones/bm9kZS1h/bad-object", "not-json")
    client.put("sketchlog/v1/tombstones/bm9kZS1h/bad-tuple", "{}")
    client.scan_prefix = lambda prefix, limit=None: [  # type: ignore[method-assign]
        _ObjectRow("bad-object", "not-json"),
        ("bad-tuple", "{}"),
    ]
    assert await storage.load_tombstones("node-a") == {}
    await storage.close()


@pytest.mark.asyncio
async def test_omnikv_delete_with_tombstone_syncs_tombstone_before_state_delete(
        tmp_path: Path, fake_omnikv_module: str) -> None:
    storage = OmniKVEmbeddedStorage(
        tmp_path / "omnikv", module_name=fake_omnikv_module)
    await storage.initialize()
    client = storage._require_client()

    log = StreamLog()
    log.add_latency(99.0)
    await storage.save("default", "checkout", log)
    client.operations.clear()

    deleted = await storage.delete_with_tombstone(
        "default", "checkout", "node-a", "stream-a", 20.0)

    state_key = _state_key("default", "checkout")
    tombstone_key = _tombstone_key("node-a", "stream-a")
    assert deleted is True
    tombstone_write_index = client.operations.index(f"put:{tombstone_key}")
    state_delete_index = client.operations.index(f"delete:{state_key}")
    assert tombstone_write_index < state_delete_index
    assert client.operations.index("sync") < state_delete_index
    assert client.operations[-1] == "sync"
    await storage.close()


@pytest.mark.asyncio
async def test_omnikv_delete_with_tombstone_without_state_and_existing_tombstone(
        tmp_path: Path, fake_omnikv_module: str) -> None:
    storage = OmniKVEmbeddedStorage(
        tmp_path / "omnikv", module_name=fake_omnikv_module)
    await storage.initialize()

    await storage.save_tombstone("node-a", "stream-a", 10.0)
    deleted = await storage.delete_with_tombstone(
        "default", "missing", "node-a", "stream-a", 9.0)

    assert deleted is False
    assert await storage.load_tombstones("node-a") == {"stream-a": 10.0}
    await storage.close()


def test_state_envelope_rejects_malformed_payloads() -> None:
    log = StreamLog()
    log.add_latency(1.0)
    valid = _state_envelope(log, 1.0)
    parsed, updated = _parse_state_envelope(valid)
    assert parsed["total"] == 1
    assert updated == 1.0

    malformed = [
        "[]",
        '{"schema":999,"payload_encoding":"zlib+base64","last_updated":1,"payload":"x"}',
        '{"schema":1,"payload_encoding":"plain","last_updated":1,"payload":"x"}',
        '{"schema":1,"payload_encoding":"zlib+base64","last_updated":1,"payload":{}}',
        '{"schema":1,"payload_encoding":"zlib+base64","last_updated":1,"payload":"@@@"}',
    ]
    for raw in malformed:
        with pytest.raises(ValueError, match="Malformed storage state envelope"):
            _parse_state_envelope(raw)


@pytest.mark.asyncio
async def test_base_storage_backend_default_hooks() -> None:
    class MinimalStorage(StorageBackend):
        async def initialize(self) -> None:
            return None

        async def save(self, namespace, stream_id, log) -> None:
            return None

        async def load(self, namespace, stream_id, deterministic=False):
            return None

        async def delete(self, namespace, stream_id) -> bool:
            return False

        async def close(self) -> None:
            return None

    storage = MinimalStorage()
    assert await storage.healthcheck() is True
    assert await storage.load_tombstones("node") == {}
    assert await storage.delete_with_tombstone(
        "default", "missing", "node", "stream", 1.0) is False


def test_server_configures_omnikv_backend_from_environment(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
        fake_omnikv_module: str) -> None:
    from sketchlog import server

    monkeypatch.setenv("SKETCHLOG_STORAGE_BACKEND", "omnikv")
    monkeypatch.setenv("SKETCHLOG_OMNIKV_DATA_DIR", str(tmp_path / "omnikv"))
    monkeypatch.setenv("SKETCHLOG_OMNIKV_MODULE", fake_omnikv_module)
    monkeypatch.delenv("SKETCHLOG_DB_URI", raising=False)

    backend = server._configure_storage_backend()

    assert isinstance(backend, OmniKVEmbeddedStorage)
    assert backend.data_dir == tmp_path / "omnikv"
