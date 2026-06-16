import time
from typing import Callable, Any
from sketchlog import StreamLog

class SketchLogMiddleware:
    """
    FastAPI / Starlette middleware for tracking API metrics with SketchLog.
    
    Tracks:
      - Request latency per endpoint
      - Endpoint hit counts
      - Status code counts
      
    Usage:
        from fastapi import FastAPI
        from sketchlog import StreamLog
        from sketchlog.integrations.fastapi import SketchLogMiddleware
        
        app = FastAPI()
        log = StreamLog()
        
        app.add_middleware(SketchLogMiddleware, streamlog=log)
    """
    def __init__(self, app: Any, streamlog: StreamLog = None, log: StreamLog = None):
        self.app = app
        self.log = log or streamlog
        if self.log is None:
            raise ValueError("Either 'log' or 'streamlog' must be provided")

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        start_time = time.monotonic()
        path = scope.get("path", "unknown")
        method = scope.get("method", "GET")
        
        status_code = [500]  # Default to 500 in case of unhandled exception

        async def send_wrapper(message: dict) -> None:
            if message["type"] == "http.response.start":
                status_code[0] = message.get("status", 500)
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            elapsed_ms = (time.monotonic() - start_time) * 1000.0
            
            # Record latency
            self.log.add_latency(elapsed_ms)
            
            # Record endpoint hits
            endpoint_str = f"{method} {path}"
            self.log.add_event(endpoint_str)
            
            # Record status codes
            status_str = f"{endpoint_str} {status_code[0]}"
            self.log.add_event(status_str)
