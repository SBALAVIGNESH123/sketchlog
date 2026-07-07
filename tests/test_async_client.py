"""
test_async_client.py — tests for AsyncSketchLogClient
======================================================
All tests are fully deterministic — zero time.sleep, zero real network calls.
Uses unittest.mock.AsyncMock to simulate aiohttp responses.
"""
from __future__ import annotations

import asyncio
import json
import sys
import os
import types
import unittest
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

# ---------------------------------------------------------------------------
# Path setup — works in both repo and standalone context
# ---------------------------------------------------------------------------
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python"))

from sketchlog.async_client import (
    AsyncClientConfig,
    AsyncSketchLogClient,
    SketchLogAuthError,
    SketchLogError,
    SketchLogRateLimitError,
    SketchLogServerError,
    SketchLogTimeoutError,
    _backoff,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mock_response(status: int, body: dict | bytes | str) -> MagicMock:
    """Build a mock aiohttp response."""
    resp = MagicMock()
    resp.status = status
    resp.headers = {}
    if isinstance(body, dict):
        raw = json.dumps(body).encode()
    elif isinstance(body, str):
        raw = body.encode()
    else:
        raw = body
    resp.read = AsyncMock(return_value=raw)
    return resp


def _make_cm(response: MagicMock) -> MagicMock:
    """Wrap a response in an async context manager."""
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=response)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


def run(coro):  # type: ignore[no-untyped-def]
    return asyncio.get_event_loop().run_until_complete(coro)


# ---------------------------------------------------------------------------
# AsyncClientConfig tests
# ---------------------------------------------------------------------------

class TestAsyncClientConfig(unittest.TestCase):

    def test_valid_config(self) -> None:
        cfg = AsyncClientConfig(base_url="http://localhost:7700", token="tok")
        self.assertEqual(cfg.base_url, "http://localhost:7700")
        self.assertEqual(cfg.token, "tok")

    def test_https_accepted(self) -> None:
        cfg = AsyncClientConfig(base_url="https://example.com")
        self.assertTrue(cfg.base_url.startswith("https://"))

    def test_invalid_base_url(self) -> None:
        with self.assertRaises(ValueError):
            AsyncClientConfig(base_url="ftp://bad")

    def test_empty_base_url(self) -> None:
        with self.assertRaises(ValueError):
            AsyncClientConfig(base_url="")

    def test_negative_timeout(self) -> None:
        with self.assertRaises(ValueError):
            AsyncClientConfig(base_url="http://localhost:7700", timeout_seconds=-1)

    def test_zero_timeout(self) -> None:
        with self.assertRaises(ValueError):
            AsyncClientConfig(base_url="http://localhost:7700", timeout_seconds=0)

    def test_negative_max_retries(self) -> None:
        with self.assertRaises(ValueError):
            AsyncClientConfig(base_url="http://localhost:7700", max_retries=-1)

    def test_zero_max_connections(self) -> None:
        with self.assertRaises(ValueError):
            AsyncClientConfig(base_url="http://localhost:7700", max_connections=0)

    def test_base_url_stripped(self) -> None:
        cfg = AsyncClientConfig(base_url="http://localhost:7700/")
        self.assertEqual(cfg._base_url_stripped, "http://localhost:7700")

    def test_no_token(self) -> None:
        cfg = AsyncClientConfig(base_url="http://localhost:7700")
        self.assertIsNone(cfg.token)

    def test_frozen(self) -> None:
        cfg = AsyncClientConfig(base_url="http://localhost:7700")
        with self.assertRaises(Exception):
            cfg.token = "x"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Backoff tests
# ---------------------------------------------------------------------------

class TestBackoff(unittest.TestCase):

    def test_no_jitter_exponential(self) -> None:
        d0 = _backoff(0, 0.5, 30.0, False)
        d1 = _backoff(1, 0.5, 30.0, False)
        d2 = _backoff(2, 0.5, 30.0, False)
        self.assertAlmostEqual(d0, 0.5)
        self.assertAlmostEqual(d1, 1.0)
        self.assertAlmostEqual(d2, 2.0)

    def test_cap_respected(self) -> None:
        d = _backoff(100, 0.5, 5.0, False)
        self.assertLessEqual(d, 5.0)

    def test_jitter_within_range(self) -> None:
        for _ in range(50):
            d = _backoff(0, 1.0, 30.0, True)
            self.assertGreaterEqual(d, 0.5)
            self.assertLessEqual(d, 1.0)


# ---------------------------------------------------------------------------
# Client lifecycle tests
# ---------------------------------------------------------------------------

class TestClientLifecycle(unittest.TestCase):

    def test_repr_closed(self) -> None:
        c = AsyncSketchLogClient("http://localhost:7700")
        self.assertIn("closed", repr(c))

    def test_request_without_start_raises(self) -> None:
        c = AsyncSketchLogClient("http://localhost:7700")
        with self.assertRaises(RuntimeError):
            run(c.health())

    def test_config_passthrough(self) -> None:
        cfg = AsyncClientConfig(base_url="http://localhost:7700", token="t")
        c = AsyncSketchLogClient("http://localhost:7700", config=cfg)
        self.assertEqual(c._cfg.token, "t")


# ---------------------------------------------------------------------------
# HTTP response tests — using mock aiohttp session
# ---------------------------------------------------------------------------

class TestClientRequests(unittest.TestCase):

    def _make_client(self) -> AsyncSketchLogClient:
        c = AsyncSketchLogClient("http://localhost:7700", token="test-token")
        # Manually inject a mock session
        c._session = MagicMock()
        return c

    def test_ingest_success(self) -> None:
        c = self._make_client()
        resp = _make_mock_response(200, {"ingested": 3})
        c._session.request = MagicMock(return_value=_make_cm(resp))
        result = run(c.ingest("prod", "latency_ms", [1.0, 2.0, 3.0]))
        self.assertEqual(result["ingested"], 3)

    def test_query_percentile_success(self) -> None:
        c = self._make_client()
        resp = _make_mock_response(200, {"value": 42.5})
        c._session.request = MagicMock(return_value=_make_cm(resp))
        result = run(c.query_percentile("prod", "latency_ms", 0.99))
        self.assertAlmostEqual(result, 42.5)

    def test_query_percentile_invalid_quantile(self) -> None:
        c = self._make_client()
        with self.assertRaises(ValueError):
            run(c.query_percentile("prod", "latency_ms", 1.5))

    def test_query_percentile_negative_quantile(self) -> None:
        c = self._make_client()
        with self.assertRaises(ValueError):
            run(c.query_percentile("prod", "latency_ms", -0.1))

    def test_query_count_success(self) -> None:
        c = self._make_client()
        resp = _make_mock_response(200, {"count": 1000})
        c._session.request = MagicMock(return_value=_make_cm(resp))
        result = run(c.query_count("prod", "latency_ms"))
        self.assertEqual(result, 1000)

    def test_query_cardinality_success(self) -> None:
        c = self._make_client()
        resp = _make_mock_response(200, {"cardinality": 512})
        c._session.request = MagicMock(return_value=_make_cm(resp))
        result = run(c.query_cardinality("prod", "latency_ms"))
        self.assertEqual(result, 512)

    def test_query_frequency_success(self) -> None:
        c = self._make_client()
        resp = _make_mock_response(200, {"frequency": 77})
        c._session.request = MagicMock(return_value=_make_cm(resp))
        result = run(c.query_frequency("prod", "latency_ms", "GET /api"))
        self.assertEqual(result, 77)

    def test_query_summary_success(self) -> None:
        c = self._make_client()
        body = {"p50": 10.0, "p95": 45.0, "p99": 99.0, "count": 5000}
        resp = _make_mock_response(200, body)
        c._session.request = MagicMock(return_value=_make_cm(resp))
        result = run(c.query_summary("prod", "latency_ms"))
        self.assertEqual(result["p99"], 99.0)

    def test_health_success(self) -> None:
        c = self._make_client()
        resp = _make_mock_response(200, {"status": "ok", "version": "1.2.3"})
        c._session.request = MagicMock(return_value=_make_cm(resp))
        result = run(c.health())
        self.assertEqual(result["status"], "ok")

    def test_list_namespaces(self) -> None:
        c = self._make_client()
        resp = _make_mock_response(200, {"namespaces": ["prod", "staging"]})
        c._session.request = MagicMock(return_value=_make_cm(resp))
        result = run(c.list_namespaces())
        self.assertEqual(result, ["prod", "staging"])

    def test_list_streams(self) -> None:
        c = self._make_client()
        resp = _make_mock_response(200, {"streams": ["latency_ms", "errors"]})
        c._session.request = MagicMock(return_value=_make_cm(resp))
        result = run(c.list_streams("prod"))
        self.assertEqual(result, ["latency_ms", "errors"])

    def test_delete_stream(self) -> None:
        c = self._make_client()
        resp = _make_mock_response(200, {"deleted": True})
        c._session.request = MagicMock(return_value=_make_cm(resp))
        result = run(c.delete_stream("prod", "latency_ms"))
        self.assertTrue(result["deleted"])


# ---------------------------------------------------------------------------
# Error handling tests
# ---------------------------------------------------------------------------

class TestErrorHandling(unittest.TestCase):

    def _make_client(self) -> AsyncSketchLogClient:
        c = AsyncSketchLogClient("http://localhost:7700", token="test-token", max_retries=0)
        c._session = MagicMock()
        return c

    def test_401_raises_auth_error(self) -> None:
        c = self._make_client()
        resp = _make_mock_response(401, {"detail": "Unauthorized"})
        c._session.request = MagicMock(return_value=_make_cm(resp))
        with self.assertRaises(SketchLogAuthError) as ctx:
            run(c.health())
        self.assertEqual(ctx.exception.status_code, 401)

    def test_403_raises_auth_error(self) -> None:
        c = self._make_client()
        resp = _make_mock_response(403, {"detail": "Forbidden"})
        c._session.request = MagicMock(return_value=_make_cm(resp))
        with self.assertRaises(SketchLogAuthError) as ctx:
            run(c.health())
        self.assertEqual(ctx.exception.status_code, 403)

    def test_429_raises_rate_limit_error(self) -> None:
        c = self._make_client()
        resp = _make_mock_response(429, {"detail": "Too Many Requests"})
        c._session.request = MagicMock(return_value=_make_cm(resp))
        with self.assertRaises(SketchLogRateLimitError):
            run(c.health())

    def test_429_with_retry_after_header(self) -> None:
        c = self._make_client()
        resp = _make_mock_response(429, {"detail": "Too Many Requests"})
        resp.headers = {"Retry-After": "60"}
        c._session.request = MagicMock(return_value=_make_cm(resp))
        with self.assertRaises(SketchLogRateLimitError) as ctx:
            run(c.health())
        self.assertEqual(ctx.exception.retry_after_seconds, 60.0)

    def test_500_raises_server_error(self) -> None:
        c = self._make_client()
        resp = _make_mock_response(500, {"detail": "Internal Server Error"})
        c._session.request = MagicMock(return_value=_make_cm(resp))
        with self.assertRaises(SketchLogServerError) as ctx:
            run(c.health())
        self.assertEqual(ctx.exception.status_code, 500)

    def test_503_raises_server_error(self) -> None:
        c = self._make_client()
        resp = _make_mock_response(503, {"detail": "Service Unavailable"})
        c._session.request = MagicMock(return_value=_make_cm(resp))
        with self.assertRaises(SketchLogServerError):
            run(c.health())

    def test_400_raises_sketch_log_error(self) -> None:
        c = self._make_client()
        resp = _make_mock_response(400, {"detail": "Bad Request"})
        c._session.request = MagicMock(return_value=_make_cm(resp))
        with self.assertRaises(SketchLogError) as ctx:
            run(c.health())
        self.assertEqual(ctx.exception.status_code, 400)

    def test_non_json_body_handled(self) -> None:
        c = self._make_client()
        resp = _make_mock_response(200, b"plain text response")
        c._session.request = MagicMock(return_value=_make_cm(resp))
        result = run(c.health())
        self.assertEqual(result, "plain text response")


# ---------------------------------------------------------------------------
# Retry tests
# ---------------------------------------------------------------------------

class TestRetry(unittest.TestCase):

    def test_retries_on_503(self) -> None:
        c = AsyncSketchLogClient(
            "http://localhost:7700",
            max_retries=2,
            retry_backoff_base=0.0,
            retry_jitter=False,
        )
        c._session = MagicMock()
        call_count = 0

        def _side_effect(*args, **kwargs):  # type: ignore[no-untyped-def]
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                return _make_cm(_make_mock_response(503, {"detail": "unavailable"}))
            return _make_cm(_make_mock_response(200, {"status": "ok"}))

        c._session.request = MagicMock(side_effect=_side_effect)
        result = run(c.health())
        self.assertEqual(result["status"], "ok")
        self.assertEqual(call_count, 3)

    def test_no_retry_on_401(self) -> None:
        c = AsyncSketchLogClient(
            "http://localhost:7700",
            max_retries=3,
            retry_backoff_base=0.0,
            retry_jitter=False,
        )
        c._session = MagicMock()
        call_count = 0

        def _side_effect(*args, **kwargs):  # type: ignore[no-untyped-def]
            nonlocal call_count
            call_count += 1
            return _make_cm(_make_mock_response(401, {"detail": "Unauthorized"}))

        c._session.request = MagicMock(side_effect=_side_effect)
        with self.assertRaises(SketchLogAuthError):
            run(c.health())
        # Should NOT retry on auth errors — only called once
        self.assertEqual(call_count, 1)

    def test_exhausted_retries_raises(self) -> None:
        c = AsyncSketchLogClient(
            "http://localhost:7700",
            max_retries=2,
            retry_backoff_base=0.0,
            retry_jitter=False,
        )
        c._session = MagicMock()
        c._session.request = MagicMock(
            return_value=_make_cm(_make_mock_response(503, {"detail": "unavailable"}))
        )
        with self.assertRaises(SketchLogServerError):
            run(c.health())


# ---------------------------------------------------------------------------
# URL building tests
# ---------------------------------------------------------------------------

class TestURLBuilding(unittest.TestCase):

    def test_trailing_slash_stripped(self) -> None:
        c = AsyncSketchLogClient("http://localhost:7700/")
        self.assertEqual(c._url("/health"), "http://localhost:7700/health")

    def test_path_joined_correctly(self) -> None:
        c = AsyncSketchLogClient("http://localhost:7700")
        self.assertEqual(
            c._url("/api/v1/ingest/prod/latency_ms"),
            "http://localhost:7700/api/v1/ingest/prod/latency_ms",
        )

    def test_auth_header_present(self) -> None:
        c = AsyncSketchLogClient("http://localhost:7700", token="my-secret-token")
        headers = c._default_headers()
        self.assertEqual(headers["Authorization"], "Bearer my-secret-token")

    def test_no_auth_header_without_token(self) -> None:
        c = AsyncSketchLogClient("http://localhost:7700")
        headers = c._default_headers()
        self.assertNotIn("Authorization", headers)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    unittest.main()
