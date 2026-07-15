import asyncio
import base64
import importlib
import json
import time
import zlib
from abc import ABC, abstractmethod
from contextlib import asynccontextmanager
from pathlib import Path
from types import ModuleType
from typing import (
    Any,
    AsyncIterator,
    Dict,
    List,
    Optional,
    Protocol,
    Union,
    cast,
    runtime_checkable,
)
import structlog

logger = structlog.get_logger("sketchlog.storage")

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy import String, LargeBinary, Float, select, delete, text

from sketchlog.facade import StreamLog
from sketchlog.concurrent import ThreadSafeStreamLog
from sketchlog._atomic import MAX_SERIALIZED_STATE_BYTES


STATE_KEY_PREFIX = "sketchlog/v1/streams"
TOMBSTONE_KEY_PREFIX = "sketchlog/v1/tombstones"
STATE_ENVELOPE_VERSION = 1


class Base(DeclarativeBase):
    pass

class SketchState(Base):
    __tablename__ = 'sketchlog_streams'
    namespace: Mapped[str] = mapped_column(String(255), primary_key=True)
    stream_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    payload: Mapped[bytes] = mapped_column(LargeBinary)
    last_updated: Mapped[float] = mapped_column(Float)


class MeshTombstone(Base):
    __tablename__ = "sketchlog_mesh_tombstones"
    node_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    stream_key: Mapped[str] = mapped_column(String(1024), primary_key=True)
    version: Mapped[float] = mapped_column(Float)


class StorageConfigurationError(RuntimeError):
    """Raised when an optional storage backend is requested but unavailable."""


@runtime_checkable
class _OmniKVEmbeddedClient(Protocol):
    """Minimal Python-side contract expected from OmniKV's embedded bridge."""

    def put(self, key: str, value: str) -> Any:
        ...

    def get(self, key: str) -> Optional[str]:
        ...

    def delete(self, key: str) -> Any:
        ...

    def scan_prefix(
            self, prefix: str, limit: Optional[int] = None) -> List[Any]:
        ...


def _encode_key_part(value: str) -> str:
    """Encode arbitrary user-controlled key material into stable path segments."""
    return base64.urlsafe_b64encode(value.encode("utf-8")).decode("ascii").rstrip("=")


def _state_key(namespace: str, stream_id: str) -> str:
    return (
        f"{STATE_KEY_PREFIX}/"
        f"{_encode_key_part(namespace)}/{_encode_key_part(stream_id)}"
    )


def _tombstone_prefix(node_id: str) -> str:
    return f"{TOMBSTONE_KEY_PREFIX}/{_encode_key_part(node_id)}/"


def _tombstone_key(node_id: str, stream_key: str) -> str:
    return f"{_tombstone_prefix(node_id)}{_encode_key_part(stream_key)}"


def _tombstone_envelope(stream_key: str, version: float) -> str:
    return json.dumps(
        {"schema": 1, "stream_key": stream_key, "version": version},
        separators=(",", ":"),
        sort_keys=True,
    )


def _tombstone_version(value: Optional[str]) -> float:
    if value is None:
        return float("-inf")
    try:
        return float(json.loads(value)["version"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return float("-inf")


def _row_key(row: Any) -> str:
    if hasattr(row, "key"):
        return str(row.key)
    if isinstance(row, dict):
        return str(row["key"])
    return str(row[0])


def _row_value(row: Any) -> str:
    if hasattr(row, "value"):
        return str(row.value)
    if isinstance(row, dict):
        return str(row["value"])
    return str(row[1])


def _serialize_and_compress_stream(
        log: Union[StreamLog, ThreadSafeStreamLog]) -> bytes:
    dict_data = log.to_dict()
    json_bytes = json.dumps(dict_data).encode("utf-8")
    if len(json_bytes) > MAX_SERIALIZED_STATE_BYTES:
        raise ValueError("Serialized state exceeds the 32 MiB limit")
    return zlib.compress(json_bytes)


def _decompress_and_deserialize_stream(payload: bytes) -> Dict[str, Any]:
    if len(payload) > MAX_SERIALIZED_STATE_BYTES:
        raise ValueError("Compressed state exceeds the 32 MiB limit")
    decompressor = zlib.decompressobj()
    json_bytes = decompressor.decompress(
        payload, MAX_SERIALIZED_STATE_BYTES + 1)
    if (
        len(json_bytes) > MAX_SERIALIZED_STATE_BYTES
        or decompressor.unconsumed_tail
    ):
        raise ValueError("Decompressed state exceeds the 32 MiB limit")
    json_bytes += decompressor.flush()
    if len(json_bytes) > MAX_SERIALIZED_STATE_BYTES:
        raise ValueError("Decompressed state exceeds the 32 MiB limit")
    if not decompressor.eof or decompressor.unused_data:
        raise ValueError("Compressed state is truncated or has trailing data")
    res: Dict[str, Any] = json.loads(json_bytes.decode("utf-8"))
    return res


def _state_envelope(
        log: Union[StreamLog, ThreadSafeStreamLog], last_updated: float) -> str:
    payload = _serialize_and_compress_stream(log)
    return json.dumps({
        "schema": STATE_ENVELOPE_VERSION,
        "payload_encoding": "zlib+base64",
        "last_updated": last_updated,
        "payload": base64.b64encode(payload).decode("ascii"),
    }, separators=(",", ":"), sort_keys=True)


def _parse_state_envelope(value: str) -> tuple[Dict[str, Any], float]:
    try:
        envelope = json.loads(value)
        if not isinstance(envelope, dict):
            raise ValueError("state envelope must be a JSON object")
        if envelope.get("schema") != STATE_ENVELOPE_VERSION:
            raise ValueError("unsupported state envelope schema")
        if envelope.get("payload_encoding") != "zlib+base64":
            raise ValueError("unsupported state payload encoding")
        last_updated = float(envelope["last_updated"])
        raw_payload = envelope["payload"]
        if not isinstance(raw_payload, str):
            raise ValueError("state payload must be a string")
        payload = base64.b64decode(raw_payload.encode("ascii"), validate=True)
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError(f"Malformed storage state envelope: {exc}") from exc
    return _decompress_and_deserialize_stream(payload), last_updated


class StorageBackend(ABC):
    @abstractmethod
    async def initialize(self) -> None:
        pass

    @abstractmethod
    async def save(self, namespace: str, stream_id: str, log: Union[StreamLog, ThreadSafeStreamLog]) -> None:
        pass

    @abstractmethod
    async def load(self, namespace: str, stream_id: str, deterministic: bool = False) -> Optional[ThreadSafeStreamLog]:
        pass

    @abstractmethod
    async def delete(self, namespace: str, stream_id: str) -> bool:
        pass

    @abstractmethod
    async def close(self) -> None:
        pass

    async def healthcheck(self) -> bool:
        return True

    async def save_tombstone(
        self, node_id: str, stream_key: str, version: float
    ) -> None:
        return None

    async def load_tombstones(self, node_id: str) -> Dict[str, float]:
        return {}

    async def delete_with_tombstone(
        self, namespace: str, stream_id: str, node_id: str,
        stream_key: str, version: float
    ) -> bool:
        """Delete state and persist its mesh tombstone.

        Backends should override this to make both mutations atomic.
        """
        deleted = await self.delete(namespace, stream_id)
        await self.save_tombstone(node_id, stream_key, version)
        return deleted

class SQLAlchemyStorage(StorageBackend):
    def __init__(self, db_uri: str):
        self.engine = create_async_engine(db_uri, echo=False)
        self.async_session = async_sessionmaker(self.engine, expire_on_commit=False, class_=AsyncSession)
        # Fixed lock striping bounds memory and keeps save/delete serialized for
        # a key without deleting a lock while another coroutine is waiting on it.
        self._locks = tuple(asyncio.Lock() for _ in range(64))

    def _lock_for(self, namespace: str, stream_id: str) -> asyncio.Lock:
        return self._locks[hash((namespace, stream_id)) % len(self._locks)]

    async def initialize(self) -> None:
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    def _serialize_and_compress(self, log: Union[StreamLog, ThreadSafeStreamLog]) -> bytes:
        return _serialize_and_compress_stream(log)

    def _decompress_and_deserialize(self, payload: bytes) -> Dict[str, Any]:
        return _decompress_and_deserialize_stream(payload)

    async def save(self, namespace: str, stream_id: str, log: Union[StreamLog, ThreadSafeStreamLog]) -> None:
        # Avoid blocking the event loop for large serialization/compression
        compressed_payload = await asyncio.to_thread(self._serialize_and_compress, log)

        last_updated = getattr(log, 'last_updated', time.time())
        async with self._lock_for(namespace, stream_id):
            async with self.async_session() as session:
                result = await session.execute(
                    select(SketchState).where(
                        SketchState.namespace == namespace,
                        SketchState.stream_id == stream_id
                    )
                )
                state = result.scalars().first()
                if state is None:
                    state = SketchState(
                        namespace=namespace,
                        stream_id=stream_id,
                        payload=compressed_payload,
                        last_updated=last_updated
                    )
                    session.add(state)
                elif state.last_updated < last_updated:
                    state.payload = compressed_payload
                    state.last_updated = last_updated
                await session.commit()

    async def load(self, namespace: str, stream_id: str, deterministic: bool = False) -> Optional[ThreadSafeStreamLog]:
        async with self.async_session() as session:
            stmt = select(SketchState).where(
                SketchState.namespace == namespace,
                SketchState.stream_id == stream_id
            )
            result = await session.execute(stmt)
            state = result.scalar_one_or_none()
            if state is None:
                return None

            try:
                dict_data = await asyncio.to_thread(self._decompress_and_deserialize, state.payload)
                log = StreamLog.from_dict(dict_data)

                # Wrap it in ThreadSafeStreamLog
                ts_log = ThreadSafeStreamLog(deterministic=deterministic)
                ts_log._log = log
                ts_log.last_updated = state.last_updated

                return ts_log
            except Exception as e:
                logger.error("storage_load_failed", namespace=namespace, stream_id=stream_id, error=str(e))
                return None

    async def delete(self, namespace: str, stream_id: str) -> bool:
        async with self._lock_for(namespace, stream_id):
            async with self.engine.begin() as conn:
                result = await conn.execute(
                    delete(SketchState).where(
                        SketchState.namespace == namespace,
                        SketchState.stream_id == stream_id
                    )
                )
                success = result.rowcount > 0
        return success

    async def close(self) -> None:
        await self.engine.dispose()

    async def healthcheck(self) -> bool:
        try:
            async def check() -> None:
                async with self.engine.connect() as connection:
                    await connection.execute(text("SELECT 1"))

            await asyncio.wait_for(check(), timeout=5.0)
            return True
        except Exception as exc:
            logger.error("storage_healthcheck_failed", error=str(exc))
            return False

    async def save_tombstone(
        self, node_id: str, stream_key: str, version: float
    ) -> None:
        async with self._lock_for(node_id, stream_key):
            async with self.async_session() as session:
                result = await session.execute(
                    select(MeshTombstone).where(
                        MeshTombstone.node_id == node_id,
                        MeshTombstone.stream_key == stream_key,
                    )
                )
                tombstone = result.scalar_one_or_none()
                if tombstone is None:
                    session.add(MeshTombstone(
                        node_id=node_id,
                        stream_key=stream_key,
                        version=version,
                    ))
                elif version > tombstone.version:
                    tombstone.version = version
                await session.commit()

    async def delete_with_tombstone(
        self, namespace: str, stream_id: str, node_id: str,
        stream_key: str, version: float
    ) -> bool:
        """Atomically delete durable state and upsert its mesh tombstone."""
        async with self._lock_for(namespace, stream_id):
            async with self.async_session() as session:
                async with session.begin():
                    state = await session.get(
                        SketchState, (namespace, stream_id))
                    deleted = state is not None
                    if state is not None:
                        await session.delete(state)
                    tombstone_result = await session.execute(
                        select(MeshTombstone).where(
                            MeshTombstone.node_id == node_id,
                            MeshTombstone.stream_key == stream_key,
                        )
                    )
                    tombstone = tombstone_result.scalar_one_or_none()
                    if tombstone is None:
                        session.add(MeshTombstone(
                            node_id=node_id,
                            stream_key=stream_key,
                            version=version,
                        ))
                    elif version > tombstone.version:
                        tombstone.version = version
                return deleted

    async def load_tombstones(self, node_id: str) -> Dict[str, float]:
        async with self.async_session() as session:
            result = await session.execute(
                select(MeshTombstone).where(MeshTombstone.node_id == node_id))
            return {
                row.stream_key: row.version for row in result.scalars().all()
            }


class OmniKVEmbeddedStorage(StorageBackend):
    """SketchLog storage backend backed by OmniKV's stable embedded API.

    The real OmniKV bridge is intentionally optional. Existing deployments keep
    using in-memory or SQLAlchemy storage unless this backend is explicitly
    configured. At runtime the backend imports a Python/native module that
    exposes a small `open_embedded(data_dir, namespace=...)` contract over
    OmniKV's Rust `EmbeddedOmniKv` facade.
    """

    def __init__(
        self,
        data_dir: Union[str, Path],
        namespace: str = "sketchlog",
        module_name: str = "omnikv",
    ) -> None:
        if not namespace or not all(
                ch.isascii() and (ch.isalnum() or ch in "-_.:")
                for ch in namespace):
            raise ValueError(
                "OmniKV namespace must contain only ASCII letters, digits, "
                "dash, underscore, dot, or colon")
        self.data_dir = Path(data_dir)
        self.namespace = namespace
        self.module_name = module_name
        self._client: Optional[_OmniKVEmbeddedClient] = None
        self._locks = tuple(asyncio.Lock() for _ in range(64))

    def _lock_index_for(self, key_a: str, key_b: str) -> int:
        return hash((key_a, key_b)) % len(self._locks)

    def _lock_for(self, key_a: str, key_b: str) -> asyncio.Lock:
        return self._locks[self._lock_index_for(key_a, key_b)]

    @asynccontextmanager
    async def _lock_pair_for(
        self,
        first_key_a: str,
        first_key_b: str,
        second_key_a: str,
        second_key_b: str,
    ) -> AsyncIterator[None]:
        first_index = self._lock_index_for(first_key_a, first_key_b)
        second_index = self._lock_index_for(second_key_a, second_key_b)
        if first_index == second_index:
            async with self._locks[first_index]:
                yield
            return

        lower_index, higher_index = sorted((first_index, second_index))
        async with self._locks[lower_index]:
            async with self._locks[higher_index]:
                yield

    async def initialize(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._client = await asyncio.to_thread(self._open_client)

    def _open_client(self) -> _OmniKVEmbeddedClient:
        module = self._import_omnikv_module()
        client = self._construct_client(module)
        for method in ("put", "get", "delete", "scan_prefix"):
            if not callable(getattr(client, method, None)):
                raise StorageConfigurationError(
                    "OmniKV embedded bridge is missing required method "
                    f"{method!r}")
        return client

    def _import_omnikv_module(self) -> ModuleType:
        try:
            module = importlib.import_module(self.module_name)
            if (
                self.module_name == "omnikv"
                and not hasattr(module, "open_embedded")
                and not hasattr(module, "EmbeddedOmniKv")
            ):
                try:
                    return importlib.import_module("omnikv.embedded")
                except ImportError:
                    return module
            return module
        except ImportError as first_error:
            if self.module_name != "omnikv":
                raise StorageConfigurationError(
                    f"Unable to import OmniKV embedded bridge "
                    f"{self.module_name!r}") from first_error
            try:
                return importlib.import_module("omnikv.embedded")
            except ImportError as second_error:
                raise StorageConfigurationError(
                    "OmniKV storage backend requires an installed OmniKV "
                    "Python/native bridge exposing open_embedded(data_dir, "
                    "namespace=...). Install the OmniKV bridge or choose "
                    "SKETCHLOG_STORAGE_BACKEND=sqlalchemy.") from second_error

    def _construct_client(self, module: ModuleType) -> _OmniKVEmbeddedClient:
        open_embedded = getattr(module, "open_embedded", None)
        if callable(open_embedded):
            return cast(
                _OmniKVEmbeddedClient,
                open_embedded(str(self.data_dir), namespace=self.namespace),
            )

        embedded_cls = getattr(module, "EmbeddedOmniKv", None)
        if embedded_cls is not None:
            open_fn = getattr(embedded_cls, "open", None)
            if callable(open_fn):
                try:
                    return cast(
                        _OmniKVEmbeddedClient,
                        open_fn(str(self.data_dir), namespace=self.namespace),
                    )
                except TypeError:
                    pass
            open_dir = getattr(embedded_cls, "open_dir", None)
            if callable(open_dir):
                client = open_dir(str(self.data_dir))
                scoped = getattr(client, "scoped", None)
                if callable(scoped):
                    return cast(_OmniKVEmbeddedClient, scoped(self.namespace))
                return cast(_OmniKVEmbeddedClient, client)

        raise StorageConfigurationError(
            "OmniKV embedded bridge must expose open_embedded(...) or "
            "EmbeddedOmniKv.open/open_dir.")

    def _require_client(self) -> _OmniKVEmbeddedClient:
        if self._client is None:
            raise RuntimeError("OmniKVEmbeddedStorage is not initialized")
        return self._client

    async def _sync_client(self, client: _OmniKVEmbeddedClient) -> None:
        sync = getattr(client, "sync", None)
        if callable(sync):
            await asyncio.to_thread(sync)

    async def _put_tombstone_if_newer(
        self,
        client: _OmniKVEmbeddedClient,
        key: str,
        stream_key: str,
        version: float,
    ) -> bool:
        current = _tombstone_version(await asyncio.to_thread(client.get, key))
        if current >= version:
            return False
        await asyncio.to_thread(
            client.put,
            key,
            _tombstone_envelope(stream_key, version),
        )
        return True

    async def save(
            self, namespace: str, stream_id: str,
            log: Union[StreamLog, ThreadSafeStreamLog]) -> None:
        client = self._require_client()
        key = _state_key(namespace, stream_id)
        last_updated = float(getattr(log, "last_updated", time.time()))
        value = await asyncio.to_thread(_state_envelope, log, last_updated)

        async with self._lock_for(namespace, stream_id):
            existing = await asyncio.to_thread(client.get, key)
            if existing is not None:
                try:
                    _, existing_updated = await asyncio.to_thread(
                        _parse_state_envelope, existing)
                except ValueError:
                    logger.warning(
                        "omnikv_state_envelope_invalid_replacing",
                        namespace=namespace,
                        stream_id=stream_id,
                    )
                else:
                    if existing_updated >= last_updated:
                        return
            await asyncio.to_thread(client.put, key, value)

    async def load(
            self, namespace: str, stream_id: str,
            deterministic: bool = False) -> Optional[ThreadSafeStreamLog]:
        client = self._require_client()
        value = await asyncio.to_thread(
            client.get, _state_key(namespace, stream_id))
        if value is None:
            return None

        try:
            dict_data, last_updated = await asyncio.to_thread(
                _parse_state_envelope, value)
            log = StreamLog.from_dict(dict_data)
            ts_log = ThreadSafeStreamLog(deterministic=deterministic)
            ts_log._log = log
            ts_log.last_updated = last_updated
            return ts_log
        except Exception as exc:
            logger.error(
                "omnikv_storage_load_failed",
                namespace=namespace,
                stream_id=stream_id,
                error=str(exc),
            )
            return None

    async def delete(self, namespace: str, stream_id: str) -> bool:
        client = self._require_client()
        key = _state_key(namespace, stream_id)
        async with self._lock_for(namespace, stream_id):
            existing = await asyncio.to_thread(client.get, key)
            if existing is None:
                return False
            await asyncio.to_thread(client.delete, key)
            return True

    async def close(self) -> None:
        client = self._client
        if client is None:
            return
        sync_error: Optional[BaseException] = None
        try:
            await self._sync_client(client)
        except BaseException as exc:
            sync_error = exc
        close = getattr(client, "close", None)
        if callable(close):
            await asyncio.to_thread(close)
            self._client = None
        elif sync_error is None:
            self._client = None
        if sync_error is not None:
            raise sync_error

    async def healthcheck(self) -> bool:
        try:
            client = self._require_client()
            stats = getattr(client, "stats", None)
            if callable(stats):
                await asyncio.wait_for(asyncio.to_thread(stats), timeout=5.0)
            else:
                await asyncio.wait_for(
                    asyncio.to_thread(
                        client.scan_prefix, "sketchlog/v1/", 1),
                    timeout=5.0,
                )
            return True
        except Exception as exc:
            logger.error("omnikv_storage_healthcheck_failed", error=str(exc))
            return False

    async def save_tombstone(
            self, node_id: str, stream_key: str, version: float) -> None:
        client = self._require_client()
        key = _tombstone_key(node_id, stream_key)
        async with self._lock_for(node_id, stream_key):
            updated = await self._put_tombstone_if_newer(
                client, key, stream_key, version)
            if updated:
                await self._sync_client(client)

    async def load_tombstones(self, node_id: str) -> Dict[str, float]:
        client = self._require_client()
        prefix = _tombstone_prefix(node_id)
        rows = await asyncio.to_thread(client.scan_prefix, prefix, None)
        tombstones: Dict[str, float] = {}
        for row in rows:
            try:
                payload = json.loads(_row_value(row))
                stream_key = str(payload["stream_key"])
                version = float(payload["version"])
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                logger.warning(
                    "omnikv_tombstone_invalid_skipped",
                    node_id=node_id,
                    key=_row_key(row),
                )
                continue
            tombstones[stream_key] = max(version, tombstones.get(stream_key, version))
        return tombstones

    async def delete_with_tombstone(
        self, namespace: str, stream_id: str, node_id: str,
        stream_key: str, version: float
    ) -> bool:
        """Durably persist the mesh tombstone before deleting state.

        The embedded bridge contract does not require cross-key transactions, so
        the recoverable ordering is tombstone-first: after a crash, stale state
        may still be retried/deleted, but a missing tombstone cannot resurrect a
        locally deleted mesh stream.
        """
        client = self._require_client()
        state_key = _state_key(namespace, stream_id)
        tombstone_key = _tombstone_key(node_id, stream_key)
        async with self._lock_pair_for(namespace, stream_id, node_id, stream_key):
            tombstone_updated = await self._put_tombstone_if_newer(
                client, tombstone_key, stream_key, version)
            if tombstone_updated:
                await self._sync_client(client)

            existing_state = await asyncio.to_thread(client.get, state_key)
            deleted = existing_state is not None
            if deleted:
                await asyncio.to_thread(client.delete, state_key)
                await self._sync_client(client)
            return deleted
