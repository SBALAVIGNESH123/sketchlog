import time
import json
import zlib
import asyncio
from collections import defaultdict
from abc import ABC, abstractmethod
from typing import Optional, List, Any, Dict, Tuple, DefaultDict, Union

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy import String, LargeBinary, Float, select, delete

from sketchlog.facade import StreamLog
from sketchlog.concurrent import ThreadSafeStreamLog
from typing import Union

class Base(DeclarativeBase):
    pass

class SketchState(Base):
    __tablename__ = 'sketchlog_streams'
    namespace: Mapped[str] = mapped_column(String(255), primary_key=True)
    stream_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    payload: Mapped[bytes] = mapped_column(LargeBinary)
    last_updated: Mapped[float] = mapped_column(Float)

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

class SQLAlchemyStorage(StorageBackend):
    def __init__(self, db_uri: str):
        self.engine = create_async_engine(db_uri, echo=False)
        self.async_session = async_sessionmaker(self.engine, expire_on_commit=False, class_=AsyncSession)
        self._locks: DefaultDict[Tuple[str, str], asyncio.Lock] = defaultdict(asyncio.Lock)

    async def initialize(self) -> None:
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    def _serialize_and_compress(self, log: Union[StreamLog, ThreadSafeStreamLog]) -> bytes:
        dict_data = log.to_dict()
        json_bytes = json.dumps(dict_data).encode('utf-8')
        return zlib.compress(json_bytes)

    def _decompress_and_deserialize(self, payload: bytes) -> Dict[str, Any]:
        json_bytes = zlib.decompress(payload)
        res: Dict[str, Any] = json.loads(json_bytes.decode('utf-8'))
        return res

    async def save(self, namespace: str, stream_id: str, log: Union[StreamLog, ThreadSafeStreamLog]) -> None:
        # Avoid blocking the event loop for large serialization/compression
        compressed_payload = await asyncio.to_thread(self._serialize_and_compress, log)

        last_updated = getattr(log, 'last_updated', time.time())
        lock_key = (namespace, stream_id)
        async with self._locks[lock_key]:
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
                import structlog
                logger = structlog.get_logger("sketchlog.storage")
                logger.error("storage_load_failed", namespace=namespace, stream_id=stream_id, error=str(e))
                return None

    async def delete(self, namespace: str, stream_id: str) -> bool:
        lock_key = (namespace, stream_id)
        async with self._locks[lock_key]:
            async with self.engine.begin() as conn:
                result = await conn.execute(
                    delete(SketchState).where(
                        SketchState.namespace == namespace,
                        SketchState.stream_id == stream_id
                    )
                )
                if lock_key in self._locks:
                    del self._locks[lock_key]
                return result.rowcount > 0

    async def close(self) -> None:
        await self.engine.dispose()
