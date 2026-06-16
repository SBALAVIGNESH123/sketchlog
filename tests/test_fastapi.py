import asyncio
from sketchlog import StreamLog
from sketchlog.integrations.fastapi import SketchLogMiddleware

def test_fastapi_middleware():
    log = StreamLog()
    
    # Mock ASGI app
    async def mock_app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200})
        await send({"type": "http.response.body", "body": b"OK"})
    
    middleware = SketchLogMiddleware(mock_app, log)
    
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/test",
    }
    
    async def mock_receive():
        return {"type": "http.request"}
        
    messages = []
    async def mock_send(message):
        messages.append(message)
        
    async def run_test():
        await middleware(scope, mock_receive, mock_send)
        
    asyncio.run(run_test())
    
    # Check that metrics were recorded
    assert log.total_events > 0
    assert log.event_count("GET /api/test") == 1
    assert log.event_count("GET /api/test 200") == 1
