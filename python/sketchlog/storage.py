import time
import json
import zlib
from abc import ABC, abstractmethod
from typing import Optional, List, Any

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
    async def load(self, namespace: str, stream_id: str) -> Optional[ThreadSafeStreamLog]:
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

    async def initialize(self) -> None:
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def save(self, namespace: str, stream_id: str, log: Union[StreamLog, ThreadSafeStreamLog]) -> None:
        # Avoid blocking the event loop for large serialization/compression
        dict_data = log.to_dict()
        json_bytes = json.dumps(dict_data).encode('utf-8')
        compressed_payload = zlib.compress(json_bytes)

        last_updated = getattr(log, 'last_updated', time.time())
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

    async def load(self, namespace: str, stream_id: str) -> Optional[ThreadSafeStreamLog]:
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
                json_bytes = zlib.decompress(state.payload)
                dict_data = json.loads(json_bytes.decode('utf-8'))
                log = StreamLog.from_dict(dict_data)

                # Wrap it in ThreadSafeStreamLog
                ts_log = ThreadSafeStreamLog(deterministic=False)
                ts_log._log = log
                ts_log.last_updated = state.last_updated

                return ts_log
            except Exception as e:
                import structlog
                logger = structlog.get_logger("sketchlog.storage")
                logger.error("storage_load_failed", namespace=namespace, stream_id=stream_id, error=str(e))
                return None

    async def delete(self, namespace: str, stream_id: str) -> bool:
        async with self.engine.begin() as conn:
            result = await conn.execute(
                delete(SketchState).where(
                    SketchState.namespace == namespace,
                    SketchState.stream_id == stream_id
                )
            )
            return result.rowcount > 0

    async def close(self) -> None:
        await self.engine.dispose()
