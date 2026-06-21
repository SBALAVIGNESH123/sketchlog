import pytest
import httpx
import os
import signal
import subprocess
import time
import sys

@pytest.mark.asyncio
async def test_metrics_endpoint_exposition(live_server):
    """Verify that /metrics exposes prometheus text format with our custom metrics."""
    base = live_server["url"]

    async with httpx.AsyncClient() as client:
        # Generate some traffic to populate metrics
        await client.get(f"{base}/health")
        await client.post(f"{base}/v1/streams/obs_stream/events", json={"latencies": [1.0, 2.0]})

        # Now check metrics
        response = await client.get(f"{base}/metrics")
        assert response.status_code == 200
        content = response.text

        # Verify specific metrics are present
        assert "sketchlog_http_requests_total" in content
        assert "sketchlog_active_streams" in content
        assert "sketchlog_events_ingested_total" in content

@pytest.mark.asyncio
async def test_rejection_metrics_increment(live_server):
    """Verify that payloads exceeding limits increment the rejection metric."""
    base = live_server["url"]

    async with httpx.AsyncClient() as client:
        # Create a payload just over the default MAX_REQUEST_BYTES limit (1MB default, let's say 2MB)
        large_payload = "A" * 2_000_000
        response = await client.post(
            f"{base}/v1/streams/obs_stream/events",
            content=large_payload,
            headers={"Content-Type": "application/json"}
        )
        assert response.status_code == 413

        # Check metrics
        metric_response = await client.get(f"{base}/metrics")
        content = metric_response.text

        # We should see sketchlog_rejections_total{reason="payload_too_large"} 1.0 (or greater)
        assert 'sketchlog_rejections_total{reason="payload_too_large"}' in content

@pytest.mark.asyncio
async def test_readiness_memory_degradation(live_server):
    """Verify the /ready endpoint checks memory and returns correctly."""
    base = live_server["url"]

    async with httpx.AsyncClient() as client:
        response = await client.get(f"{base}/ready")

        # Depending on the CI host's actual memory, it could be healthy or degraded.
        # We cannot mock psutil because the server runs in a separate process.
        assert response.status_code in (200, 503)
        if response.status_code == 200:
            assert response.json()["status"] == "ready"
        else:
            assert "Service degraded" in response.json()["detail"]
