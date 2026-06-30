import time
import json
import zlib
import asyncio
from abc import ABC, abstractmethod
from typing import Optional, List, Any, Dict, Union
import structlog

logger = structlog.get_logger("sketchlog.storage")

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy import String, LargeBinary, Float, select, delete, text

from sketchlog.facade import StreamLog
from sketchlog.concurrent import ThreadSafeStreamLog
from sketchlog._atomic import MAX_SERIALIZED_STATE_BYTES

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
        dict_data = log.to_dict()
        json_bytes = json.dumps(dict_data).encode('utf-8')
        if len(json_bytes) > MAX_SERIALIZED_STATE_BYTES:
            raise ValueError("Serialized state exceeds the 32 MiB limit")
        return zlib.compress(json_bytes)

    def _decompress_and_deserialize(self, payload: bytes) -> Dict[str, Any]:
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
        res: Dict[str, Any] = json.loads(json_bytes.decode('utf-8'))
        return res

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
