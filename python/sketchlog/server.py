import os
import json
import logging
import hmac
import math
from typing import Dict, List, Optional, Any, Annotated, Callable, Awaitable, AsyncGenerator, Tuple, cast
from collections import OrderedDict
from fastapi import FastAPI, HTTPException, status, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, Field, field_validator, model_validator
import asyncio
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send
import time
import psutil
import structlog
from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST
from asgi_correlation_id import CorrelationIdMiddleware, correlation_id

from sketchlog import StreamLog, __version__
from sketchlog.concurrent import ThreadSafeStreamLog
from sketchlog.drift import DriftSketch
from sketchlog.alerts import AlertEngine
from sketchlog.cluster import ClusterManager, MAX_MESH_PAYLOAD_BYTES
from sketchlog.slo import SmartSLOEngine
from sketchlog.diff import SketchDiff
from sketchlog.sql import SQLParser, execute_stream_query
from prometheus_client import CollectorRegistry

_OS_NAME = os.name

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

from contextlib import asynccontextmanager

# Initialize global alerting engine
global_drift_sketch = DriftSketch(window="1m")
alert_engine = AlertEngine(global_drift_sketch, poll_interval=10.0)

# Prometheus Metrics
REGISTRY = CollectorRegistry()
HTTP_REQUESTS_TOTAL = Counter("sketchlog_http_requests_total", "Total HTTP requests", ["method", "status", "path"], registry=REGISTRY)
HTTP_REQUEST_DURATION = Histogram("sketchlog_http_request_duration_seconds", "HTTP request duration", ["method", "path"], registry=REGISTRY)
ACTIVE_STREAMS = Gauge("sketchlog_active_streams", "Number of active streams in registry", registry=REGISTRY)
EVENTS_INGESTED_TOTAL = Counter("sketchlog_events_ingested_total", "Total events ingested", registry=REGISTRY)
STREAM_EVICTIONS_TOTAL = Counter("sketchlog_stream_evictions_total", "Total streams evicted from registry", registry=REGISTRY)
REJECTIONS_TOTAL = Counter("sketchlog_rejections_total", "Total rejected operations", ["reason"], registry=REGISTRY)
STORAGE_FAILURES_TOTAL = Counter(
    "sketchlog_storage_failures_total",
    "Failed durable storage operations",
    ["operation"],
    registry=REGISTRY,
)
MEMORY_CURRENT_BYTES = Gauge(
    "sketchlog_memory_current_bytes", "Process/cgroup memory currently used",
    registry=REGISTRY)
MEMORY_LIMIT_BYTES = Gauge(
    "sketchlog_memory_limit_bytes", "Effective process/cgroup memory limit",
    registry=REGISTRY)
MEMORY_USAGE_RATIO = Gauge(
    "sketchlog_memory_usage_ratio", "Memory usage divided by effective limit",
    registry=REGISTRY)
READINESS_STATUS = Gauge(
    "sketchlog_readiness_status", "Readiness state by bounded cause",
    ["cause"], registry=REGISTRY)
READINESS_CAUSES = (
    "ready", "configuration", "memory", "memory_check", "storage")

# Alerting Metrics
ALERTS_FIRED = Counter("sketchlog_alerts_fired_total", "Total alerts fired", registry=REGISTRY)
WEBHOOK_FAILURES = Counter("sketchlog_webhook_deliveries_failed_total", "Total failed webhook deliveries", registry=REGISTRY)

alert_engine.on_alert_fired = lambda: ALERTS_FIRED.inc()
alert_engine.on_webhook_failed = lambda: WEBHOOK_FAILURES.inc()

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    if PEERS or ADVERTISED_ADDRESS:
        logger.warning("Mesh Clustering is enabled. deterministic=True is forced for all streams, "
                       "bypassing the C++ high-performance path. This incurs a significant (~46x) performance penalty.")
    alert_engine.start()

    flush_task = None
    if storage_backend:
        await storage_backend.initialize()
        if PEERS or ADVERTISED_ADDRESS:
            cluster_manager.restore_local_tombstones(
                await storage_backend.load_tombstones(NODE_ID))

        async def flush_db_loop() -> None:
            if not storage_backend:
                return
            try:
                while True:
                    await asyncio.sleep(60)
                    for ns, sid, stream in registry.snapshot_items():
                        try:
                            await storage_backend.save(ns, sid, stream)
                        except Exception as e:
                            logger.error("flush_db_stream_error", namespace=ns, stream_id=sid, error=str(e))
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.error("flush_db_loop_error", error=str(e))

        flush_task = asyncio.create_task(flush_db_loop())
    cluster_manager.start()

    yield

    if flush_task:
        flush_task.cancel()
        try:
            await flush_task
        except asyncio.CancelledError:
            pass

    if storage_backend:
        logger.info("shutting_down_storage", msg="Flushing all streams to DB...")
        for ns, sid, stream in registry.snapshot_items():
            try:
                await storage_backend.save(ns, sid, stream)
            except Exception as e:
                logger.error("flush_on_shutdown_error", namespace=ns, stream_id=sid, error=str(e))
        await storage_backend.close()

    cluster_manager.stop()
    alert_engine.stop()

app = FastAPI(
    title="SketchLog Server",
    description="Standalone network service for SketchLog event streaming and metrics aggregation.",
    version=__version__,
    lifespan=lifespan,
)


@app.exception_handler(RequestValidationError)
async def safe_validation_error(
    _request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Return validation details without reflecting unsafe/non-JSON inputs."""
    safe_errors = []
    for error in exc.errors():
        safe_errors.append({
            key: value for key, value in error.items()
            if key not in ("input", "ctx")
        })
    return JSONResponse(status_code=422, content={"detail": safe_errors})

NAMESPACE_MEMORY_QUOTA_MB = int(os.environ.get("SKETCHLOG_NAMESPACE_QUOTA_MB", "50"))
STREAM_UPPER_BOUND_BYTES = 130 * 1024 # Approx 130KB max per stream
MAX_STREAMS_PER_NAMESPACE = (NAMESPACE_MEMORY_QUOTA_MB * 1024 * 1024) // STREAM_UPPER_BOUND_BYTES
if MAX_STREAMS_PER_NAMESPACE < 1:
    MAX_STREAMS_PER_NAMESPACE = 1

MAX_STREAMS = int(os.environ.get("SKETCHLOG_MAX_STREAMS", "1000"))
if MAX_STREAMS < 1:
    raise ValueError("SKETCHLOG_MAX_STREAMS must be >= 1")

MAX_BATCH_SIZE = int(os.environ.get("SKETCHLOG_MAX_BATCH_SIZE", "10000"))
if MAX_BATCH_SIZE < 1:
    raise ValueError("SKETCHLOG_MAX_BATCH_SIZE must be >= 1")

MAX_REQUEST_BYTES = int(os.environ.get("SKETCHLOG_MAX_REQUEST_BYTES", "1048576"))
if MAX_REQUEST_BYTES < 1:
    raise ValueError("SKETCHLOG_MAX_REQUEST_BYTES must be >= 1")

ANOMALY_SENSITIVITY = float(
    os.environ.get("SKETCHLOG_ANOMALY_SENSITIVITY", "0.2"))
if not 0.0 < ANOMALY_SENSITIVITY <= 1.0:
    raise ValueError("SKETCHLOG_ANOMALY_SENSITIVITY must be in (0, 1]")

NODE_ID = os.environ.get("SKETCHLOG_NODE_ID", f"node-{os.getpid()}")
PEERS = [p.strip() for p in os.environ.get("SKETCHLOG_PEERS", "").split(",") if p.strip()]
CLUSTER_SECRET = os.environ.get("SKETCHLOG_CLUSTER_SECRET")
AUTH_TOKEN = os.environ.get("SKETCHLOG_AUTH_TOKEN")
NAMESPACE_TOKENS_RAW = os.environ.get("SKETCHLOG_NAMESPACE_TOKENS", "")
NAMESPACE_TOKENS: Dict[str, frozenset[str]] = {}
if NAMESPACE_TOKENS_RAW:
    try:
        parsed_namespace_tokens = json.loads(NAMESPACE_TOKENS_RAW)
        if not isinstance(parsed_namespace_tokens, dict):
            raise ValueError("must be a JSON object")
        for token, namespaces in parsed_namespace_tokens.items():
            if (not isinstance(token, str) or not token
                    or not isinstance(namespaces, list)
                    or not namespaces
                    or any(not isinstance(ns, str) or not ns for ns in namespaces)):
                raise ValueError(
                    "each non-empty token must map to a non-empty namespace list")
            NAMESPACE_TOKENS[token] = frozenset(namespaces)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError(f"Invalid SKETCHLOG_NAMESPACE_TOKENS: {exc}") from exc
ADVERTISED_ADDRESS = os.environ.get("SKETCHLOG_ADVERTISED_ADDRESS")
PEER_ALLOWLIST = [
    p.strip()
    for p in os.environ.get("SKETCHLOG_PEER_ALLOWLIST", ",".join(PEERS)).split(",")
    if p.strip()
]
if (PEERS or ADVERTISED_ADDRESS) and not CLUSTER_SECRET:
    raise ValueError(
        "SKETCHLOG_CLUSTER_SECRET is required whenever Sketch Mesh is enabled")

class _RequestBodyTooLarge(Exception):
    pass


class LimitUploadSize:
    """Count request bytes while the endpoint consumes them; never re-buffer."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(
            self, scope: Scope, receive: Receive, send: Send) -> None:
        if (scope["type"] == "http"
                and scope.get("method") in ("POST", "PUT", "PATCH")):
            path = str(scope.get("path", ""))
            request_limit = (
                MAX_MESH_PAYLOAD_BYTES
                if path in (
                    "/mesh/gossip/digest", "/mesh/gossip/sync")
                else MAX_REQUEST_BYTES
            )
            headers = dict(scope.get("headers", []))
            raw_content_length = headers.get(b"content-length")
            if raw_content_length is not None:
                try:
                    content_length = int(
                        raw_content_length.decode("ascii", errors="strict"))
                except (UnicodeDecodeError, ValueError):
                    REJECTIONS_TOTAL.labels(reason="invalid_content_length").inc()
                    response = Response(
                        status_code=400,
                        content=b"Invalid Content-Length")
                    await response(scope, receive, send)
                    return
                if content_length < 0:
                    REJECTIONS_TOTAL.labels(
                        reason="invalid_content_length").inc()
                    response = Response(
                        status_code=400,
                        content=b"Invalid Content-Length")
                    await response(scope, receive, send)
                    return
                if content_length > request_limit:
                    REJECTIONS_TOTAL.labels(reason="payload_too_large").inc()
                    response = Response(
                        status_code=413,
                        content=b"Request body too large")
                    await response(scope, receive, send)
                    return

            received = 0
            too_large = False
            original_receive = receive
            pending_response: List[Message] = []

            async def limited_receive() -> Message:
                nonlocal received, too_large
                message = await original_receive()
                if message["type"] == "http.request":
                    received += len(message.get("body", b""))
                    if received > request_limit:
                        too_large = True
                        REJECTIONS_TOTAL.labels(reason="payload_too_large").inc()
                        raise _RequestBodyTooLarge
                return message

            async def pending_send(message: Message) -> None:
                pending_response.append(message)

            try:
                await self.app(scope, limited_receive, pending_send)
            except _RequestBodyTooLarge:
                too_large = True
            if too_large:
                response = Response(
                    status_code=413, content=b"Request body too large")
                await response(scope, original_receive, send)
            else:
                for message in pending_response:
                    await send(message)
            return

        await self.app(scope, receive, send)

app.add_middleware(LimitUploadSize)

@app.middleware("http")
async def require_auth(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
    if (AUTH_TOKEN or NAMESPACE_TOKENS) and request.url.path.startswith("/v1/"):
        token = request.headers.get("X-SketchLog-Auth-Token")
        if not token:
            return Response(status_code=401, content=b'{"detail":"Unauthorized"}', headers={"Content-Type": "application/json"})
        is_admin = bool(
            AUTH_TOKEN and hmac.compare_digest(token.encode(), AUTH_TOKEN.encode()))
        allowed = next(
            (namespaces for candidate, namespaces in NAMESPACE_TOKENS.items()
             if hmac.compare_digest(token.encode(), candidate.encode())),
            None,
        )
        if not is_admin and allowed is None:
            return Response(status_code=401, content=b'{"detail":"Unauthorized"}', headers={"Content-Type": "application/json"})

        if not is_admin:
            assert allowed is not None
            requested: set[str] = set()
            path = request.url.path
            marker = "/v1/namespaces/"
            if path == "/v1/namespaces/aggregate":
                requested.update(
                    ns.strip() for ns in request.query_params.get(
                        "namespaces", "").split(",") if ns.strip())
            elif path.startswith(marker):
                remainder = path[len(marker):]
                requested.add(remainder.split("/", 1)[0])
            elif path.startswith("/v1/streams/"):
                requested.add("default")
            if requested and not requested.issubset(allowed):
                return Response(status_code=403, content=b'{"detail":"Forbidden namespace"}', headers={"Content-Type": "application/json"})
        request.state.is_admin = is_admin
        request.state.allowed_namespaces = allowed
    return await call_next(request)

@app.get("/v1/streams/{stream_id:path}/diff")
@app.get("/v1/namespaces/{namespace}/streams/{stream_id:path}/diff")
async def diff_streams(
    stream_id: str,
    baseline_stream_id: Optional[str] = None,
    baseline: Optional[str] = None,
    namespace: str = "default",
) -> Dict[str, Any]:
    """
    Sketch Diffing — Visual & Programmatic Distribution Comparison.
    Compare any two time windows, deployments, or regions side-by-side.
    """
    baseline_id = baseline_stream_id or baseline
    if not baseline_id:
        raise HTTPException(
            status_code=422,
            detail="baseline_stream_id (or compatibility alias baseline) is required",
        )
    current_stream = await registry.get(namespace, stream_id)
    if not current_stream:
        has_curr = cluster_manager.has_peer_data(namespace, stream_id) if (PEERS or ADVERTISED_ADDRESS) else False
        if not has_curr:
            raise HTTPException(status_code=404, detail=f"Current stream '{stream_id}' not found in namespace '{namespace}'.")

    baseline_stream = await registry.get(namespace, baseline_id)
    if not baseline_stream:
        has_base = cluster_manager.has_peer_data(namespace, baseline_id) if (PEERS or ADVERTISED_ADDRESS) else False
        if not has_base:
            raise HTTPException(status_code=404, detail=f"Baseline stream '{baseline_id}' not found in namespace '{namespace}'.")

    if PEERS or ADVERTISED_ADDRESS:
        curr = cluster_manager.get_merged_stream(namespace, stream_id, current_stream)
        base = cluster_manager.get_merged_stream(namespace, baseline_id, baseline_stream)
    else:
        assert current_stream is not None
        assert baseline_stream is not None
        from typing import cast
        curr = cast(StreamLog, current_stream.get_snapshot() if hasattr(current_stream, 'get_snapshot') else current_stream)
        base = cast(StreamLog, baseline_stream.get_snapshot() if hasattr(baseline_stream, 'get_snapshot') else baseline_stream)

    if curr.latency_count == 0 or base.latency_count == 0:
        raise HTTPException(status_code=400, detail="One or both streams have no latency data to compare.")

    diff = SketchDiff(curr, base)
    return diff.to_dict()

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

        status_code = response.status_code if response else 500
        route = request.scope.get("route")
        path_label = getattr(route, "path", "unmatched")
        if not isinstance(path_label, str) or not path_label:
            path_label = "unmatched"

        HTTP_REQUESTS_TOTAL.labels(
            method=request.method, status=status_code, path=path_label).inc()
        HTTP_REQUEST_DURATION.labels(method=request.method, path=path_label).observe(duration)

        if status_code >= 400 and status_code != 404:
            logger.warning("http_request_failed", method=request.method, path=request.url.path, status=status_code)

    if response is None:
        return Response(status_code=500)
    return response

app.add_middleware(CorrelationIdMiddleware)

# State
class StreamRegistry:
    def __init__(
        self,
        max_streams_per_namespace: int,
        storage: Optional[Any] = None,
        max_streams: int = MAX_STREAMS,
    ):
        self.max_streams_per_ns = max_streams_per_namespace
        self.max_streams = max_streams
        self.storage = storage
        from typing import DefaultDict
        from collections import defaultdict
        self._namespaces: DefaultDict[str, OrderedDict[str, ThreadSafeStreamLog]] = defaultdict(OrderedDict)
        from threading import Lock
        self._lock = Lock()
        self._operation_lock = asyncio.Lock()

    def _count_locked(self) -> int:
        return sum(len(streams) for streams in self._namespaces.values())

    async def _evict_if_needed(self, namespace: str) -> None:
        """Persist one victim before atomically removing it from memory."""
        with self._lock:
            namespace_streams = self._namespaces.get(namespace)
            victim: Optional[Tuple[str, str, ThreadSafeStreamLog]] = None
            if namespace_streams and len(namespace_streams) >= self.max_streams_per_ns:
                stream_id, stream = next(iter(namespace_streams.items()))
                victim = (namespace, stream_id, stream)
            elif self._count_locked() >= self.max_streams:
                for victim_namespace, streams in self._namespaces.items():
                    if streams:
                        stream_id, stream = next(iter(streams.items()))
                        victim = (victim_namespace, stream_id, stream)
                        break

        if victim is None:
            return
        victim_namespace, victim_id, victim_stream = victim
        if self.storage:
            # Backpressure is intentional: no state is removed until the
            # durable save commits, and failures leave the live state intact.
            try:
                await self.storage.save(
                    victim_namespace, victim_id, victim_stream)
            except Exception:
                STORAGE_FAILURES_TOTAL.labels(operation="eviction_save").inc()
                logger.exception(
                    "eviction_save_failed",
                    namespace=victim_namespace,
                    stream_id=victim_id,
                )
                raise

        with self._lock:
            victim_streams = self._namespaces.get(victim_namespace)
            if not victim_streams or victim_streams.get(victim_id) is not victim_stream:
                return
            del victim_streams[victim_id]
            if not victim_streams:
                del self._namespaces[victim_namespace]
            STREAM_EVICTIONS_TOTAL.inc()
            ACTIVE_STREAMS.set(self._count_locked())
        logger.info(
            "stream_evicted", namespace=victim_namespace, stream_id=victim_id)

    def snapshot_items(self) -> List[Tuple[str, str, ThreadSafeStreamLog]]:
        """Returns list of (namespace, stream_id, stream)"""
        with self._lock:
            items = []
            for ns, streams in self._namespaces.items():
                for sid, stream in streams.items():
                    items.append((ns, sid, stream))
            return items

    async def get_or_create(self, namespace: str, stream_id: str) -> ThreadSafeStreamLog:
        async with self._operation_lock:
            with self._lock:
                ns_streams = self._namespaces.get(namespace)
                if ns_streams and stream_id in ns_streams:
                    ns_streams.move_to_end(stream_id)
                    return ns_streams[stream_id]

            stream = None
            if self.storage:
                stream = await self.storage.load(
                    namespace, stream_id,
                    deterministic=bool(PEERS or ADVERTISED_ADDRESS))

            await self._evict_if_needed(namespace)
            with self._lock:
                ns_streams = self._namespaces[namespace]
                if stream_id in ns_streams:
                    ns_streams.move_to_end(stream_id)
                    return ns_streams[stream_id]
                if stream is None:
                    stream = ThreadSafeStreamLog(
                        deterministic=bool(PEERS or ADVERTISED_ADDRESS))
                ns_streams[stream_id] = stream
                ACTIVE_STREAMS.set(self._count_locked())
                return stream

    async def get(self, namespace: str, stream_id: str) -> Optional[ThreadSafeStreamLog]:
        with self._lock:
            ns_streams = self._namespaces.get(namespace)
            if ns_streams and stream_id in ns_streams:
                ns_streams.move_to_end(stream_id)
                return ns_streams[stream_id]

        if self.storage:
            async with self._operation_lock:
                with self._lock:
                    ns_streams = self._namespaces.get(namespace)
                    if ns_streams and stream_id in ns_streams:
                        ns_streams.move_to_end(stream_id)
                        return ns_streams[stream_id]
                stream = await self.storage.load(
                    namespace, stream_id,
                    deterministic=bool(PEERS or ADVERTISED_ADDRESS))
                if stream:
                    await self._evict_if_needed(namespace)
                    with self._lock:
                        ns_streams = self._namespaces[namespace]
                        existing = ns_streams.get(stream_id)
                        if existing is not None:
                            return existing
                        ns_streams[stream_id] = stream
                        ACTIVE_STREAMS.set(self._count_locked())
                    return cast(ThreadSafeStreamLog, stream)
        return None

    def peek(self, namespace: str, stream_id: str) -> Optional[ThreadSafeStreamLog]:
        with self._lock:
            ns_streams = self._namespaces.get(namespace)
            if ns_streams:
                return ns_streams.get(stream_id)
            return None

    async def delete(
            self, namespace: str, stream_id: str,
            delete_storage: bool = True) -> bool:
        async with self._operation_lock:
            found = False
            if self.storage and delete_storage:
                found = await self.storage.delete(namespace, stream_id)
            with self._lock:
                ns_streams = self._namespaces.get(namespace)
                if ns_streams and stream_id in ns_streams:
                    del ns_streams[stream_id]
                    if not ns_streams:
                        del self._namespaces[namespace]
                    ACTIVE_STREAMS.set(self._count_locked())
                    found = True
            return found

    def get_namespace(self, namespace: str) -> List[ThreadSafeStreamLog]:
        with self._lock:
            ns_streams = self._namespaces.get(namespace)
            if ns_streams:
                return list(ns_streams.values())
            return []

SYNC_INTERVAL = float(os.environ.get("SKETCHLOG_SYNC_INTERVAL", "5.0"))
DB_URI = os.environ.get("SKETCHLOG_DB_URI")
storage_backend = None
if DB_URI:
    try:
        from sketchlog.storage import SQLAlchemyStorage
        from sqlalchemy.engine.url import make_url
        storage_backend = SQLAlchemyStorage(DB_URI)
        redacted = make_url(DB_URI).render_as_string(hide_password=True)
        logger.info("storage_backend_configured", uri=redacted)
    except Exception as e:
        logger.error("storage_backend_failed", error=str(e))
        import sys
        sys.exit(1)

registry = StreamRegistry(
    max_streams_per_namespace=MAX_STREAMS_PER_NAMESPACE,
    storage=storage_backend,
    max_streams=MAX_STREAMS,
)
cluster_manager = ClusterManager(
    node_id=NODE_ID,
    peers=PEERS,
    registry=registry,
    cluster_secret=CLUSTER_SECRET,
    advertised_address=ADVERTISED_ADDRESS,
    sync_interval=SYNC_INTERVAL,
    peer_allowlist=PEER_ALLOWLIST,
)

# Models
# C++ extensions accept uint64_t but typically bounded positive integers max at 2^63-1 for safe signed limits in generic protocols.
ValidEventCount = Annotated[int, Field(gt=0, lt=9223372036854775808)]
ValidName = Annotated[str, Field(min_length=1, max_length=255)]

class EventBatch(BaseModel):
    latencies: Optional[List[float]] = Field(default_factory=list, description="Array of latency values to ingest.")
    uniques: Optional[List[ValidName]] = Field(default_factory=list, description="Array of distinct string items for cardinality tracking.")
    events: Optional[Dict[ValidName, ValidEventCount]] = Field(default_factory=dict, description="Dictionary mapping event names to their positive occurrence counts.")

    @field_validator("latencies", mode="before")
    @classmethod
    def validate_finite_latencies(cls, value: Any) -> Any:
        if value is None or not isinstance(value, list):
            return value
        for latency in value:
            if (isinstance(latency, bool)
                    or not isinstance(latency, (int, float))
                    or not math.isfinite(float(latency))):
                raise ValueError("latencies must contain only finite numbers")
        return value

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

class SLOEvaluationRequest(BaseModel):
    baseline_stream_id: str
    target_percentile: float = 0.995
    budget_percent: float = 0.005

class SLOResponse(BaseModel):
    stream_id: str
    baseline_stream_id: str
    target_percentile: float
    target_latency: float
    budget_percent: float
    current_events: int
    current_errors: int
    current_error_rate: float
    burn_rate: float
    is_alerting: bool


class MergeRequest(BaseModel):
    state: Dict[str, Any]


class SQLQueryRequest(BaseModel):
    query: Annotated[str, Field(min_length=1, max_length=4096)]

# Endpoints
@app.get("/health", status_code=status.HTTP_200_OK)
async def health_check() -> Dict[str, str]:
    return {"status": "ok"}


def _read_cgroup_number(path: str) -> Optional[float]:
    try:
        with open(path, encoding="ascii") as handle:
            value = handle.read().strip()
        return None if value == "max" else float(value)
    except (OSError, ValueError):
        return None


def _effective_memory_usage() -> Tuple[float, float]:
    process_bytes = float(psutil.Process().memory_info().rss)
    explicit_limit = float(
        int(os.environ.get("SKETCHLOG_MEMORY_LIMIT_BYTES", "0")))
    if explicit_limit > 0:
        return process_bytes, explicit_limit

    if _OS_NAME != "nt":
        for current_path, limit_path in (
            ("/sys/fs/cgroup/memory.current", "/sys/fs/cgroup/memory.max"),
            (
                "/sys/fs/cgroup/memory/memory.usage_in_bytes",
                "/sys/fs/cgroup/memory/memory.limit_in_bytes",
            ),
        ):
            current = _read_cgroup_number(current_path)
            limit = _read_cgroup_number(limit_path)
            if current is not None and limit is not None and limit > 0:
                # Some cgroup v1 hosts report an effectively unlimited huge
                # sentinel. In that case use host memory as the real fallback.
                if limit < 1 << 60:
                    return current, limit
    return process_bytes, float(psutil.virtual_memory().total)


def _set_readiness_cause(active_cause: str) -> None:
    for cause in READINESS_CAUSES:
        READINESS_STATUS.labels(cause=cause).set(
            1 if cause == active_cause else 0)


@app.get("/ready", status_code=status.HTTP_200_OK)
async def readiness_check() -> Dict[str, str]:
    try:
        threshold = float(os.environ.get("SKETCHLOG_MEMORY_THRESHOLD", "90"))
    except ValueError:
        _set_readiness_cause("configuration")
        raise HTTPException(
            status_code=503, detail="Service degraded: Invalid memory threshold")
    if not 0 < threshold <= 100:
        _set_readiness_cause("configuration")
        raise HTTPException(
            status_code=503, detail="Service degraded: Invalid memory threshold")

    try:
        current_bytes, limit_bytes = _effective_memory_usage()
        usage_ratio = current_bytes / limit_bytes
        MEMORY_CURRENT_BYTES.set(current_bytes)
        MEMORY_LIMIT_BYTES.set(limit_bytes)
        MEMORY_USAGE_RATIO.set(usage_ratio)
        if usage_ratio * 100 > threshold:
            _set_readiness_cause("memory")
            raise HTTPException(status_code=503, detail="Service degraded: Memory usage critical")
    except Exception as e:
        if isinstance(e, HTTPException):
            raise e
        logger.warning("readiness_check_failed", exc_info=True)
        _set_readiness_cause("memory_check")
        raise HTTPException(status_code=503, detail="Service degraded: Memory check failed")

    if storage_backend and not await storage_backend.healthcheck():
        _set_readiness_cause("storage")
        raise HTTPException(
            status_code=503, detail="Service degraded: Storage unavailable")
    _set_readiness_cause("ready")
    return {"status": "ready"}

@app.get("/metrics")
async def get_prometheus_metrics() -> Response:
    return Response(content=generate_latest(REGISTRY), media_type=CONTENT_TYPE_LATEST)

def check_mesh_auth(request: Request) -> None:
    if not PEERS and not ADVERTISED_ADDRESS:
        raise HTTPException(status_code=400, detail="Mesh clustering is not enabled on this node")
    if not CLUSTER_SECRET:
        logger.error("Mesh is enabled but SKETCHLOG_CLUSTER_SECRET is missing. Rejecting request to prevent unauthenticated cluster manipulation.")
        raise HTTPException(status_code=401, detail="Cluster secret missing from server configuration")

    token = request.headers.get("X-SketchLog-Cluster-Token")
    if not token or not hmac.compare_digest(
            token.encode(), CLUSTER_SECRET.encode()):
        raise HTTPException(status_code=401, detail="Unauthorized cluster node")

@app.post("/mesh/ping", include_in_schema=False)
async def mesh_ping(request: Request) -> Dict[str, Any]:
    check_mesh_auth(request)
    body_bytes = await request.body()
    try:
        data = json.loads(body_bytes)
        return cluster_manager.handle_ping(data)
    except Exception as e:
        logger.error("mesh_ping_failed", error=str(e))
        raise HTTPException(status_code=400, detail="Invalid ping payload")

@app.post("/mesh/gossip/digest", include_in_schema=False)
async def mesh_gossip_digest(request: Request) -> Dict[str, Any]:
    check_mesh_auth(request)
    body_bytes = await request.body()
    try:
        data = json.loads(body_bytes)
        return cluster_manager.handle_gossip_digest(data)
    except Exception as e:
        logger.error("mesh_gossip_digest_failed", error=str(e))
        raise HTTPException(status_code=400, detail="Invalid digest payload")

@app.post("/mesh/gossip/sync", include_in_schema=False)
async def mesh_gossip_sync(request: Request) -> Dict[str, str]:
    check_mesh_auth(request)
    try:
        body_bytes = await request.body()
        data = json.loads(body_bytes)
        cluster_manager.handle_gossip_sync(data)
        return {"status": "ok"}
    except (KeyError, ValueError) as e:
        logger.error("mesh_gossip_sync_failed", error=str(e))
        raise HTTPException(status_code=400, detail="Invalid sync payload")

@app.post("/v1/namespaces/{namespace}/streams/{stream_id:path}/events", status_code=status.HTTP_202_ACCEPTED)
@app.post("/v1/streams/{stream_id:path}/events", status_code=status.HTTP_202_ACCEPTED)
async def ingest_events(stream_id: str, batch: EventBatch, namespace: str = "default") -> Dict[str, str]:
    if len(stream_id) > 255:
        raise HTTPException(status_code=422, detail="Stream ID too long")
    if len(namespace) > 255:
        raise HTTPException(status_code=422, detail="Namespace too long")

    # 1. Preflight counter capacity limit
    new_events = (len(batch.latencies) if batch.latencies else 0)
    if batch.events:
        new_events += sum(batch.events.values())

    current_total = 0
    existing_stream = registry.peek(namespace, stream_id)
    if not existing_stream and registry.storage:
        existing_stream = await registry.storage.load(namespace, stream_id, deterministic=bool(PEERS or ADVERTISED_ADDRESS))
    if existing_stream:
        current_total = existing_stream.total_events

    # INT64_MAX
    if current_total + new_events > 9223372036854775807:
        raise HTTPException(status_code=422, detail="Total stream event capacity exceeded")

    stream = await registry.get_or_create(namespace, stream_id)

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


@app.post(
    "/v1/namespaces/{namespace}/streams/{stream_id:path}/merge",
    status_code=status.HTTP_202_ACCEPTED,
)
@app.post(
    "/v1/streams/{stream_id:path}/merge",
    status_code=status.HTTP_202_ACCEPTED,
)
async def merge_stream(
    stream_id: str, request: MergeRequest, namespace: str = "default"
) -> Dict[str, str]:
    """Merge a validated serialized sketch, including precision-safe WASM state."""
    state = json.loads(json.dumps(request.state))
    try:
        latency = state["latency"]
        events = state["events"]
        for owner, key in (
            (state, "total"),
            (latency, "zero_count"),
            (latency, "count"),
            (events, "total"),
        ):
            value = owner[key]
            if isinstance(value, str):
                if not value.isdigit():
                    raise ValueError(f"{key} must be a decimal integer")
                owner[key] = int(value)
        for bucket_group in ("positive", "negative"):
            for key, value in latency[bucket_group].items():
                if isinstance(value, str):
                    if not value.isdigit():
                        raise ValueError("bucket counts must be decimal integers")
                    latency[bucket_group][key] = int(value)
        for row in events["table"]:
            for index, value in enumerate(row):
                if isinstance(value, str):
                    if not value.isdigit():
                        raise ValueError("event table counts must be decimal integers")
                    row[index] = int(value)
        incoming = StreamLog.from_dict(state)
    except (KeyError, TypeError, ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=422, detail=f"Invalid sketch state: {exc}")

    stream = await registry.get_or_create(namespace, stream_id)
    try:
        stream.merge(incoming)
    except (ValueError, OverflowError, RuntimeError) as exc:
        raise HTTPException(status_code=422, detail=f"Incompatible sketch state: {exc}")
    EVENTS_INGESTED_TOTAL.inc(incoming.total_events)
    return {"status": "accepted"}

@app.get("/v1/namespaces/{namespace}/streams/{stream_id:path}/metrics", response_model=MetricsResponse)
@app.get("/v1/streams/{stream_id:path}/metrics", response_model=MetricsResponse)
async def get_metrics(stream_id: str, namespace: str = "default") -> MetricsResponse:
    local_stream = await registry.get(namespace, stream_id)

    if not PEERS and not ADVERTISED_ADDRESS:
        if not local_stream:
            raise HTTPException(status_code=404, detail=f"Stream '{stream_id}' not found in namespace '{namespace}'.")
        stream_to_report = local_stream.get_snapshot()
    else:
        # Clustered mode: fetch merged stats across all peers
        # Check if the stream actually exists locally or remotely.
        stream_to_report = cluster_manager.get_merged_stream(namespace, stream_id, local_stream)
        has_remote = cluster_manager.has_peer_data(namespace, stream_id)

        if not local_stream and not has_remote:
            raise HTTPException(status_code=404, detail=f"Stream '{stream_id}' not found in namespace '{namespace}'.")

    return MetricsResponse(
        stream_id=stream_id,
        p50=stream_to_report.percentile(0.50),
        p90=stream_to_report.percentile(0.90),
        p99=stream_to_report.percentile(0.99),
        p99_9=stream_to_report.percentile(0.999),
        unique_count=stream_to_report.unique_count(),
        total_events=stream_to_report.total_events,
        memory_footprint_bytes=stream_to_report.memory_bytes()
    )

@app.websocket("/v1/namespaces/{namespace}/streams/{stream_id:path}/ws")
@app.websocket("/v1/streams/{stream_id:path}/ws")
async def stream_ws(websocket: WebSocket, stream_id: str, namespace: str = "default") -> None:
    if AUTH_TOKEN or NAMESPACE_TOKENS:
        # Browsers cannot attach arbitrary WebSocket headers. An HttpOnly,
        # Secure, SameSite cookie set by the deployment's auth gateway is the
        # supported browser credential; non-browser SDKs use the header.
        token = (
            websocket.headers.get("X-SketchLog-Auth-Token")
            or websocket.cookies.get("sketchlog_auth")
        )
        is_admin = bool(
            token and AUTH_TOKEN
            and hmac.compare_digest(token.encode(), AUTH_TOKEN.encode()))
        allowed = next(
            (namespaces for candidate, namespaces in NAMESPACE_TOKENS.items()
             if token and hmac.compare_digest(token.encode(), candidate.encode())),
            None,
        )
        if not is_admin and (allowed is None or namespace not in allowed):
            await websocket.close(code=1008)
            return

    await websocket.accept()
    try:
        while True:
            local_stream = await registry.get(namespace, stream_id)
            if not PEERS and not ADVERTISED_ADDRESS:
                stream_to_report = local_stream.get_snapshot() if local_stream else None
            else:
                stream_to_report = cluster_manager.get_merged_stream(namespace, stream_id, local_stream) if (local_stream or cluster_manager.has_peer_data(namespace, stream_id)) else None

            if stream_to_report:
                state = stream_to_report.to_dict()
                latency = state["latency"]
                events = state["events"]
                for owner, key in (
                    (state, "total"),
                    (latency, "zero_count"),
                    (latency, "count"),
                    (events, "total"),
                ):
                    owner[key] = str(owner[key])
                for bucket_group in ("positive", "negative"):
                    latency[bucket_group] = {
                        key: str(value)
                        for key, value in latency[bucket_group].items()
                    }
                events["table"] = [
                    [str(value) for value in row]
                    for row in events["table"]
                ]
                state["metrics"] = {
                    "p50": stream_to_report.p50(),
                    "p95": stream_to_report.p95(),
                    "p99": stream_to_report.p99(),
                    "p99_9": stream_to_report.p999(),
                    "unique_count": str(stream_to_report.unique_count()),
                    "total_events": str(stream_to_report.total_events),
                    "memory_footprint_bytes": str(
                        stream_to_report.memory_bytes()),
                }
                await websocket.send_json(state)
            else:
                await websocket.send_json({"error": "Stream not found"})

            await asyncio.sleep(1.0)
    except WebSocketDisconnect:
        pass

@app.get("/v1/namespaces/{namespace}/streams/{stream_id:path}/events", response_model=EventCountResponse)
@app.get("/v1/streams/{stream_id:path}/events", response_model=EventCountResponse)
async def get_event_count(stream_id: str, name: str, namespace: str = "default") -> EventCountResponse:
    stream = await registry.get(namespace, stream_id)
    if not stream:
        raise HTTPException(status_code=404, detail=f"Stream '{stream_id}' not found in namespace '{namespace}'.")

    return EventCountResponse(
        stream_id=stream_id,
        event_name=name,
        count=stream.event_count(name)
    )

@app.post("/v1/namespaces/{namespace}/streams/{stream_id:path}/slo/evaluate", response_model=SLOResponse)
@app.post("/v1/streams/{stream_id:path}/slo/evaluate", response_model=SLOResponse)
async def evaluate_slo(stream_id: str, req: SLOEvaluationRequest, namespace: str = "default") -> SLOResponse:
    current_stream = await registry.get(namespace, stream_id)
    if not current_stream:
        has_curr = cluster_manager.has_peer_data(namespace, stream_id) if (PEERS or ADVERTISED_ADDRESS) else False
        if not has_curr:
            raise HTTPException(status_code=404, detail=f"Current stream '{stream_id}' not found in namespace '{namespace}'.")

    baseline_stream = await registry.get(namespace, req.baseline_stream_id)
    if not baseline_stream:
        has_base = cluster_manager.has_peer_data(namespace, req.baseline_stream_id) if (PEERS or ADVERTISED_ADDRESS) else False
        if not has_base:
            raise HTTPException(status_code=404, detail=f"Baseline stream '{req.baseline_stream_id}' not found in namespace '{namespace}'.")

    if PEERS or ADVERTISED_ADDRESS:
        curr = cluster_manager.get_merged_stream(namespace, stream_id, current_stream)
        base = cluster_manager.get_merged_stream(namespace, req.baseline_stream_id, baseline_stream)
    else:
        assert current_stream is not None
        assert baseline_stream is not None
        curr = current_stream.get_snapshot()
        base = baseline_stream.get_snapshot()

    try:
        metrics = SmartSLOEngine.evaluate(
            current_stream=curr,
            historical_stream=base,
            target_percentile=req.target_percentile,
            budget_percent=req.budget_percent
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return SLOResponse(
        stream_id=stream_id,
        baseline_stream_id=req.baseline_stream_id,
        **metrics
    )


@app.get("/v1/namespaces/{namespace}/streams/{stream_id:path}/slo/recommend")
@app.get("/v1/streams/{stream_id:path}/slo/recommend")
async def recommend_slo(
    stream_id: str,
    namespace: str = "default",
    target_percentile: float = 0.995,
    budget_percent: float = 0.005,
) -> Dict[str, float]:
    stream = await registry.get(namespace, stream_id)
    if not stream:
        raise HTTPException(status_code=404, detail="Baseline stream not found")
    try:
        return SmartSLOEngine.recommend(
            stream.get_snapshot(), target_percentile, budget_percent)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/v1/namespaces/{namespace}/streams/{stream_id:path}/anomaly")
@app.get("/v1/streams/{stream_id:path}/anomaly")
async def detect_anomaly(
    stream_id: str,
    baseline_stream_id: str,
    namespace: str = "default",
    sensitivity: float = ANOMALY_SENSITIVITY,
) -> Dict[str, Any]:
    if not 0.0 < sensitivity <= 1.0:
        raise HTTPException(status_code=422, detail="sensitivity must be in (0, 1]")
    current = await registry.get(namespace, stream_id)
    baseline = await registry.get(namespace, baseline_stream_id)
    if not current or not baseline:
        raise HTTPException(status_code=404, detail="Current or baseline stream not found")
    score = current.get_snapshot().anomaly_score(baseline.get_snapshot())
    return {
        "stream_id": stream_id,
        "baseline_stream_id": baseline_stream_id,
        "model": "approximate_two_sample_ks",
        "anomaly_score": score,
        "sensitivity": sensitivity,
        "is_anomalous": score >= sensitivity,
    }


@app.post("/v1/query")
async def query_stream(
    query_request: SQLQueryRequest, request: Request
) -> Dict[str, Any]:
    started = time.perf_counter()
    try:
        plan = SQLParser(query_request.query).parse()
        source = plan["from"].strip()
        if ((source.startswith('"') and source.endswith('"'))
                or (source.startswith("'") and source.endswith("'"))):
            source = source[1:-1]
        if "/" in source:
            namespace, stream_id = source.split("/", 1)
        else:
            namespace, stream_id = "default", source
        if not namespace or not stream_id:
            raise ValueError("FROM must identify [namespace/]stream")

        allowed = getattr(request.state, "allowed_namespaces", None)
        is_admin = getattr(request.state, "is_admin", not (AUTH_TOKEN or NAMESPACE_TOKENS))
        if not is_admin and (allowed is None or namespace not in allowed):
            raise HTTPException(status_code=403, detail="Forbidden namespace")

        stream = await registry.get(namespace, stream_id)
        if not stream:
            raise HTTPException(status_code=404, detail="Query stream not found")
        values = execute_stream_query(plan, stream.get_snapshot())
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    results = [
        {"stream": f"{namespace}/{stream_id}", "metric": metric, "value": value}
        for metric, value in values.items()
    ]
    return {
        "query": query_request.query,
        "results": results,
        "execution_time_ms": (time.perf_counter() - started) * 1000,
    }

@app.delete("/v1/namespaces/{namespace}/streams/{stream_id:path}", status_code=status.HTTP_204_NO_CONTENT)
@app.delete("/v1/streams/{stream_id:path}", status_code=status.HTTP_204_NO_CONTENT)
async def reset_stream(stream_id: str, namespace: str = "default") -> None:
    existing = await registry.get(namespace, stream_id)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"Stream '{stream_id}' not found in namespace '{namespace}'.")

    if PEERS or ADVERTISED_ADDRESS:
        try:
            stream_key, version, previous = cluster_manager.begin_deletion(
                namespace, stream_id)
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        if storage_backend:
            try:
                await storage_backend.delete_with_tombstone(
                    namespace, stream_id, NODE_ID, stream_key, version)
            except Exception:
                cluster_manager.rollback_deletion(
                    stream_key, version, previous)
                raise
            # Durable deletion and its tombstone have committed. Do not roll
            # the in-memory tombstone back if resident removal is interrupted.
            await registry.delete(
                namespace, stream_id, delete_storage=False)
        else:
            try:
                await registry.delete(namespace, stream_id)
            except Exception:
                cluster_manager.rollback_deletion(
                    stream_key, version, previous)
                raise
    else:
        await registry.delete(namespace, stream_id)

@app.get("/v1/namespaces/aggregate", response_model=MetricsResponse)
async def aggregate_streams(namespaces: str, stream_id: str) -> MetricsResponse:
    ns_list = list(dict.fromkeys(ns.strip() for ns in namespaces.split(",") if ns.strip()))
    if not ns_list:
        raise HTTPException(status_code=400, detail="No namespaces provided.")

    merged = StreamLog(deterministic=bool(PEERS or ADVERTISED_ADDRESS))
    found_any = False

    for ns in ns_list:
        local_stream = await registry.get(ns, stream_id)
        if local_stream:
            merged.merge(local_stream.get_snapshot())
            found_any = True

        if PEERS or ADVERTISED_ADDRESS:
            has_remote = cluster_manager.has_peer_data(ns, stream_id)
            if has_remote:
                remote_merged = cluster_manager.get_merged_stream(ns, stream_id, None)
                merged.merge(remote_merged)
                found_any = True

    if not found_any:
        raise HTTPException(status_code=404, detail=f"Stream '{stream_id}' not found in any of the specified namespaces.")

    return MetricsResponse(
        stream_id=stream_id,
        p50=merged.percentile(0.50),
        p90=merged.percentile(0.90),
        p99=merged.percentile(0.99),
        p99_9=merged.percentile(0.999),
        unique_count=merged.unique_count(),
        total_events=merged.total_events,
        memory_footprint_bytes=merged.memory_bytes()
    )

def main() -> None:
    import argparse
    import uvicorn
    parser = argparse.ArgumentParser(description="Run the SketchLog HTTP server")
    parser.add_argument(
        "--host", default=os.environ.get("SKETCHLOG_HOST", "127.0.0.1"))
    parser.add_argument(
        "--port", type=int, default=int(os.environ.get("SKETCHLOG_PORT", "8000")))
    parser.add_argument(
        "--tls-cert", default=os.environ.get("SKETCHLOG_TLS_CERT"))
    parser.add_argument(
        "--tls-key", default=os.environ.get("SKETCHLOG_TLS_KEY"))
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("--port must be in [1, 65535]")
    kwargs: Dict[str, Any] = {}
    if bool(args.tls_cert) != bool(args.tls_key):
        raise ValueError("Both SKETCHLOG_TLS_CERT and SKETCHLOG_TLS_KEY must be provided for TLS, but only one was found.")
    if args.tls_cert and args.tls_key:
        kwargs["ssl_certfile"] = args.tls_cert
        kwargs["ssl_keyfile"] = args.tls_key
    uvicorn.run(
        "sketchlog.server:app",
        host=args.host,
        port=args.port,
        reload=False,
        **kwargs,
    )

if __name__ == "__main__":
    main()
