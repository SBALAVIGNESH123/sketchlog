import os
from typing import Dict, List, Optional, Any, Annotated, Callable, Awaitable
from collections import OrderedDict
from fastapi import FastAPI, HTTPException, status, Request, Response
from pydantic import BaseModel, Field, model_validator
from starlette.middleware.base import BaseHTTPMiddleware
import time
import psutil
import structlog
from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST
from asgi_correlation_id import CorrelationIdMiddleware, correlation_id

from sketchlog import StreamLog

if not structlog.is_configured():
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ]
    )
logger = structlog.get_logger("sketchlog.server")

HTTP_REQUESTS_TOTAL = Counter("sketchlog_http_requests_total", "Total HTTP requests", ["method", "status"])
HTTP_REQUEST_DURATION = Histogram("sketchlog_http_request_duration_seconds", "HTTP request duration", ["method", "path"])
ACTIVE_STREAMS = Gauge("sketchlog_active_streams", "Number of active streams in registry")
EVENTS_INGESTED_TOTAL = Counter("sketchlog_events_ingested_total", "Total events ingested")
STREAM_EVICTIONS_TOTAL = Counter("sketchlog_stream_evictions_total", "Total streams evicted from registry")
REJECTIONS_TOTAL = Counter("sketchlog_rejections_total", "Total rejected operations", ["reason"])

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
if MAX_REQUEST_BYTES < 1:
    raise ValueError("SKETCHLOG_MAX_REQUEST_BYTES must be >= 1")

class LimitUploadSize(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Any) -> Response:
        if request.method in ["POST", "PUT", "PATCH"]:
            content_length = request.headers.get("content-length")
            if content_length:
                try:
                    if int(content_length) > MAX_REQUEST_BYTES:
                        REJECTIONS_TOTAL.labels(reason="payload_too_large").inc()
                        return Response(status_code=413, content=b"Request body too large")
                except ValueError:
                    REJECTIONS_TOTAL.labels(reason="invalid_content_length").inc()
                    return Response(status_code=400, content=b"Invalid Content-Length")

            body = bytearray()
            more_body = True
            receive = request.receive

            while more_body:
                message = await receive()
                if message["type"] == "http.request":
                    chunk = message.get("body", b"")
                    body.extend(chunk)
                    if len(body) > MAX_REQUEST_BYTES:
                        REJECTIONS_TOTAL.labels(reason="payload_too_large").inc()
                        return Response(status_code=413, content=b"Request body too large")
                    more_body = message.get("more_body", False)
                elif message["type"] == "http.disconnect":
                    more_body = False

            body_bytes = bytes(body)

            from typing import MutableMapping
            async def limited_receive() -> MutableMapping[str, Any]:
                nonlocal body_bytes
                if body_bytes is not None:
                    msg = {"type": "http.request", "body": body_bytes, "more_body": False}
                    body_bytes = None
                    return msg
                return {"type": "http.request", "body": b"", "more_body": False}

            request._receive = limited_receive

        from typing import cast
        return cast(Response, await call_next(request))

app.add_middleware(LimitUploadSize)

@app.middleware("http")
async def observe_requests(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
    structlog.contextvars.bind_contextvars(request_id=correlation_id.get())
    start_time = time.perf_counter()
    response = None

    try:
        response = await call_next(request)
    except Exception as e:
        logger.exception("unhandled_exception", method=request.method, path=request.url.path, exc_info=True)
        raise e
    finally:
        duration = time.perf_counter() - start_time

        stream_id = ""
        if "streams/" in request.url.path:
            parts = request.url.path.split("streams/")
            if len(parts) > 1:
                stream_id = parts[1].split("/")[0]

        status_code = response.status_code if response else 500

        path_label = request.url.path
        if "/streams/" in path_label:
            parts = path_label.split("/")
            if len(parts) >= 4 and parts[1] == "v1" and parts[2] == "streams":
                parts[3] = "{stream_id}"
                path_label = "/".join(parts)

        HTTP_REQUESTS_TOTAL.labels(method=request.method, status=status_code).inc()
        HTTP_REQUEST_DURATION.labels(method=request.method, path=path_label).observe(duration)

        if status_code >= 400 and status_code != 404:
            logger.warning("http_request_failed", method=request.method, path=request.url.path, status=status_code)

    if response is None:
        return Response(status_code=500)
    return response

app.add_middleware(CorrelationIdMiddleware)

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
            evicted_id, _ = self._streams.popitem(last=False)
            STREAM_EVICTIONS_TOTAL.inc()
            logger.info("stream_evicted", stream_id=evicted_id)

        stream = StreamLog()
        self._streams[stream_id] = stream
        ACTIVE_STREAMS.set(len(self._streams))
        return stream

    def get(self, stream_id: str) -> Optional[StreamLog]:
        if stream_id in self._streams:
            self._streams.move_to_end(stream_id)
            return self._streams[stream_id]
        return None

    def peek(self, stream_id: str) -> Optional[StreamLog]:
        return self._streams.get(stream_id)

    def delete(self, stream_id: str) -> bool:
        if stream_id in self._streams:
            del self._streams[stream_id]
            ACTIVE_STREAMS.set(len(self._streams))
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
    try:
        threshold = float(os.environ.get("SKETCHLOG_MEMORY_THRESHOLD", "90"))
    except ValueError:
        logger.warning("Invalid SKETCHLOG_MEMORY_THRESHOLD, defaulting to 90.0")
        threshold = 90.0

    try:
        mem_percent = psutil.Process().memory_percent()
        if mem_percent > threshold:
            raise HTTPException(status_code=503, detail="Service degraded: Memory usage critical")
    except Exception as e:
        if isinstance(e, HTTPException):
            raise e
        logger.warning("readiness_check_failed", exc_info=True)
        raise HTTPException(status_code=503, detail="Service degraded: Memory check failed")
    return {"status": "ready"}

@app.get("/metrics")
async def get_prometheus_metrics() -> Response:
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)

# Test endpoint for SDK retry validation
_flake_counter = 0

@app.api_route("/test/flake", methods=["GET", "POST"])
async def flake_endpoint() -> Dict[str, str]:
    global _flake_counter
    _flake_counter += 1
    if _flake_counter <= 2:
        raise HTTPException(status_code=503, detail="Simulated flake")
    _flake_counter = 0
    return {"status": "success"}

@app.post("/v1/streams/{stream_id:path}/events", status_code=status.HTTP_202_ACCEPTED)
async def ingest_events(stream_id: str, batch: EventBatch) -> Dict[str, str]:
    if len(stream_id) > 255:
        raise HTTPException(status_code=422, detail="Stream ID too long")

    # 1. Preflight counter capacity limit
    new_events = (len(batch.latencies) if batch.latencies else 0)
    if batch.events:
        new_events += sum(batch.events.values())

    current_total = 0
    existing_stream = registry.peek(stream_id)
    if existing_stream:
        current_total = existing_stream.total_events

    # INT64_MAX
    if current_total + new_events > 9223372036854775807:
        raise HTTPException(status_code=422, detail="Total stream event capacity exceeded")

    stream = registry.get_or_create(stream_id)

    if batch.latencies:
        stream.add_batch(batch.latencies)

    if batch.uniques:
        for unique_item in batch.uniques:
            stream.add_unique(unique_item)

    if batch.events:
        for event_name, count in batch.events.items():
            stream.add_event(event_name, count=count)

    EVENTS_INGESTED_TOTAL.inc(new_events)
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

@app.get("/v1/streams/{stream_id:path}/events", response_model=EventCountResponse)
async def get_event_count(stream_id: str, name: str) -> EventCountResponse:
    stream = registry.get(stream_id)
    if not stream:
        raise HTTPException(status_code=404, detail=f"Stream '{stream_id}' not found.")

    return EventCountResponse(
        stream_id=stream_id,
        event_name=name,
        count=stream.event_count(name)
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
