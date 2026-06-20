import os
from typing import Dict, List, Optional, Any, Annotated
from collections import OrderedDict
from fastapi import FastAPI, HTTPException, status, Request, Response
from pydantic import BaseModel, Field, model_validator
from starlette.middleware.base import BaseHTTPMiddleware

from sketchlog import StreamLog

app = FastAPI(
    title="SketchLog Server",
    description="Standalone network service for SketchLog event streaming and metrics aggregation.",
    version="1.0.1",
)

# Configuration
MAX_STREAMS = int(os.environ.get("SKETCHLOG_MAX_STREAMS", "1000"))
if MAX_STREAMS < 1:
    raise ValueError("SKETCHLOG_MAX_STREAMS must be >= 1")

MAX_BATCH_SIZE = int(os.environ.get("SKETCHLOG_MAX_BATCH_SIZE", "10000"))
if MAX_BATCH_SIZE < 1:
    raise ValueError("SKETCHLOG_MAX_BATCH_SIZE must be >= 1")

MAX_REQUEST_BYTES = int(os.environ.get("SKETCHLOG_MAX_REQUEST_BYTES", "1048576"))

class LimitUploadSize(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Any) -> Response:
        if request.method in ["POST", "PUT", "PATCH"]:
            content_length = request.headers.get("content-length")
            if content_length and int(content_length) > MAX_REQUEST_BYTES:
                return Response(status_code=413, content=b"Request body too large")
        from typing import cast
        return cast(Response, await call_next(request))

app.add_middleware(LimitUploadSize)

# State
class StreamRegistry:
    def __init__(self, max_size: int):
        self.max_size = max_size
        self._streams: OrderedDict[str, StreamLog] = OrderedDict()

    def get_or_create(self, stream_id: str) -> StreamLog:
        if stream_id in self._streams:
            self._streams.move_to_end(stream_id)
            return self._streams[stream_id]

        if len(self._streams) >= self.max_size:
            # Evict oldest
            self._streams.popitem(last=False)

        stream = StreamLog()
        self._streams[stream_id] = stream
        return stream

    def get(self, stream_id: str) -> Optional[StreamLog]:
        if stream_id in self._streams:
            self._streams.move_to_end(stream_id)
            return self._streams[stream_id]
        return None

    def delete(self, stream_id: str) -> bool:
        if stream_id in self._streams:
            del self._streams[stream_id]
            return True
        return False

registry = StreamRegistry(max_size=MAX_STREAMS)

# Models
# C++ extensions accept uint64_t but typically bounded positive integers max at 2^63-1 for safe signed limits in generic protocols.
ValidEventCount = Annotated[int, Field(gt=0, lt=9223372036854775808)]
ValidName = Annotated[str, Field(min_length=1, max_length=255)]

class EventBatch(BaseModel):
    latencies: Optional[List[float]] = Field(default_factory=list, description="Array of latency values to ingest.")
    uniques: Optional[List[ValidName]] = Field(default_factory=list, description="Array of distinct string items for cardinality tracking.")
    events: Optional[Dict[ValidName, ValidEventCount]] = Field(default_factory=dict, description="Dictionary mapping event names to their positive occurrence counts.")

    @model_validator(mode='after')
    def check_batch_size(self) -> 'EventBatch':
        total_items = (len(self.latencies) if self.latencies else 0) + \
                      (len(self.uniques) if self.uniques else 0) + \
                      (len(self.events) if self.events else 0)
        if total_items > MAX_BATCH_SIZE:
            raise ValueError(f"Batch size exceeds maximum limit of {MAX_BATCH_SIZE} items")
        return self

class MetricsResponse(BaseModel):
    stream_id: str
    p50: float
    p90: float
    p99: float
    p99_9: float
    unique_count: int
    total_events: int
    memory_footprint_bytes: int

class EventCountResponse(BaseModel):
    stream_id: str
    event_name: str
    count: int

# Endpoints
@app.get("/health", status_code=status.HTTP_200_OK)
async def health_check() -> Dict[str, str]:
    return {"status": "ok"}

@app.get("/ready", status_code=status.HTTP_200_OK)
async def readiness_check() -> Dict[str, str]:
    return {"status": "ready"}

@app.post("/v1/streams/{stream_id:path}/events", status_code=status.HTTP_202_ACCEPTED)
async def ingest_events(stream_id: str, batch: EventBatch) -> Dict[str, str]:
    if len(stream_id) > 255:
        raise HTTPException(status_code=422, detail="Stream ID too long")
    stream = registry.get_or_create(stream_id)

    if batch.latencies:
        stream.add_batch(batch.latencies)

    if batch.uniques:
        for unique_item in batch.uniques:
            stream.add_unique(unique_item)

    if batch.events:
        for event_name, count in batch.events.items():
            stream.add_event(event_name, count=count)

    return {"status": "accepted"}

@app.get("/v1/streams/{stream_id:path}/metrics", response_model=MetricsResponse)
async def get_metrics(stream_id: str) -> MetricsResponse:
    stream = registry.get(stream_id)
    if not stream:
        raise HTTPException(status_code=404, detail=f"Stream '{stream_id}' not found.")

    return MetricsResponse(
        stream_id=stream_id,
        p50=stream.percentile(0.50),
        p90=stream.percentile(0.90),
        p99=stream.percentile(0.99),
        p99_9=stream.percentile(0.999),
        unique_count=stream.unique_count(),
        total_events=stream.total_events,
        memory_footprint_bytes=stream.memory_bytes()
    )

@app.get("/v1/streams/{stream_id:path}/events/{event_name:path}", response_model=EventCountResponse)
async def get_event_count(stream_id: str, event_name: str) -> EventCountResponse:
    stream = registry.get(stream_id)
    if not stream:
        raise HTTPException(status_code=404, detail=f"Stream '{stream_id}' not found.")

    return EventCountResponse(
        stream_id=stream_id,
        event_name=event_name,
        count=stream.event_count(event_name)
    )

@app.delete("/v1/streams/{stream_id:path}", status_code=status.HTTP_204_NO_CONTENT)
async def reset_stream(stream_id: str) -> None:
    success = registry.delete(stream_id)
    if not success:
        raise HTTPException(status_code=404, detail=f"Stream '{stream_id}' not found.")

def main() -> None:
    import uvicorn
    host = os.environ.get("SKETCHLOG_HOST", "0.0.0.0")
    port = int(os.environ.get("SKETCHLOG_PORT", "8000"))
    uvicorn.run("sketchlog.server:app", host=host, port=port, reload=False)

if __name__ == "__main__":
    main()
