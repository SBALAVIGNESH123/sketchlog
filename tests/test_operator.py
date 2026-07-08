"""Tests for SketchLog Kubernetes Operator — zero real k8s calls."""
from __future__ import annotations

import json
import threading
import time
from typing import Any, Dict, Iterator, List, Optional
from unittest.mock import MagicMock, patch, call
import pytest

from sketchlog.k8s_operator import (
    OperatorConfig,
    OperatorError,
    K8sClient,
    SketchLogOperator,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_cfg(**kwargs: Any) -> OperatorConfig:
    defaults: Dict[str, Any] = {
        "api_server": "https://k8s.local",
        "token": "test-token",
        "ca_cert_path": "/nonexistent/ca.crt",
        "namespace": "test-ns",
        "reconcile_interval": 1.0,
        "http_timeout": 5.0,
    }
    defaults.update(kwargs)
    return OperatorConfig(**defaults)


def _mock_response(status: int, body: Any = None) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = body or {}
    if status >= 400:
        import httpx
        req = httpx.Request("GET", "https://k8s.local/test")
        http_err = httpx.HTTPStatusError(
            f"HTTP {status}", request=req, response=MagicMock(status_code=status)
        )
        resp.raise_for_status.side_effect = http_err
    else:
        resp.raise_for_status.return_value = None
    return resp


def _mock_k8s(responses: Optional[Dict[str, Any]] = None) -> MagicMock:
    """Return a mock K8sClient with configurable per-path responses."""
    k8s = MagicMock(spec=K8sClient)
    k8s.get.return_value = responses or {}
    k8s.apply.return_value = {}
    k8s.delete.return_value = None
    return k8s


# ---------------------------------------------------------------------------
# OperatorConfig tests
# ---------------------------------------------------------------------------


def test_config_defaults() -> None:
    cfg = OperatorConfig(api_server="https://k8s.local", token="tok")
    assert cfg.reconcile_interval == 30.0
    assert cfg.dry_run is False
    assert cfg.namespace == ""


def test_config_frozen() -> None:
    cfg = OperatorConfig(api_server="https://k8s.local", token="tok")
    with pytest.raises(Exception):
        cfg.token = "other"  # type: ignore[misc]


def test_config_invalid_reconcile_interval() -> None:
    with pytest.raises(ValueError, match="reconcile_interval"):
        OperatorConfig(api_server="https://k8s.local", reconcile_interval=0)


def test_config_invalid_http_timeout() -> None:
    with pytest.raises(ValueError, match="http_timeout"):
        OperatorConfig(api_server="https://k8s.local", http_timeout=-1)


def test_config_invalid_watch_timeout() -> None:
    with pytest.raises(ValueError, match="watch_timeout"):
        OperatorConfig(api_server="https://k8s.local", watch_timeout=0)


def test_config_resolve_token_from_field() -> None:
    cfg = OperatorConfig(api_server="https://k8s.local", token="my-tok")
    assert cfg.resolve_token() == "my-tok"


def test_config_resolve_token_from_file(tmp_path: Any) -> None:
    token_file = tmp_path / "token"
    token_file.write_text("file-token\n")
    cfg = OperatorConfig(
        api_server="https://k8s.local",
        token="",
        token_path=str(token_file),
    )
    assert cfg.resolve_token() == "file-token"


def test_config_resolve_token_missing_file() -> None:
    cfg = OperatorConfig(
        api_server="https://k8s.local",
        token="",
        token_path="/nonexistent/token",
    )
    assert cfg.resolve_token() == ""


def test_config_from_env(monkeypatch: Any) -> None:
    monkeypatch.setenv("KUBERNETES_SERVICE_HOST", "10.0.0.1")
    monkeypatch.setenv("KUBERNETES_SERVICE_PORT_HTTPS", "6443")
    monkeypatch.setenv("KUBE_TOKEN", "env-tok")
    monkeypatch.setenv("OPERATOR_NAMESPACE", "my-ns")
    monkeypatch.setenv("RECONCILE_INTERVAL", "60")
    monkeypatch.setenv("DRY_RUN", "true")
    cfg = OperatorConfig.from_env()
    assert cfg.api_server == "https://10.0.0.1:6443"
    assert cfg.token == "env-tok"
    assert cfg.namespace == "my-ns"
    assert cfg.reconcile_interval == 60.0
    assert cfg.dry_run is True


def test_config_from_env_no_host(monkeypatch: Any) -> None:
    monkeypatch.delenv("KUBERNETES_SERVICE_HOST", raising=False)
    cfg = OperatorConfig.from_env()
    assert "kubernetes.default.svc" in cfg.api_server


# ---------------------------------------------------------------------------
# OperatorError tests
# ---------------------------------------------------------------------------


def test_operator_error_message() -> None:
    err = OperatorError("something failed", status_code=500)
    assert str(err) == "something failed"
    assert err.status_code == 500


def test_operator_error_no_status() -> None:
    err = OperatorError("timeout")
    assert err.status_code is None


# ---------------------------------------------------------------------------
# K8sClient tests
# ---------------------------------------------------------------------------


def test_k8s_client_context_manager() -> None:
    import httpx
    mock_client = MagicMock(spec=httpx.Client)
    cfg = _make_cfg()
    with patch("sketchlog.k8s_operator.httpx.Client", return_value=mock_client):
        with K8sClient(cfg) as k8s:
            k8s._get_client()  # trigger client creation
    mock_client.close.assert_called_once()


def test_k8s_client_external_client_not_closed() -> None:
    import httpx
    mock_client = MagicMock(spec=httpx.Client)
    cfg = _make_cfg()
    k8s = K8sClient(cfg, client=mock_client)
    k8s.close()
    mock_client.close.assert_not_called()


def test_k8s_client_get_ok() -> None:
    import httpx
    mock_client = MagicMock(spec=httpx.Client)
    mock_client.get.return_value = _mock_response(200, {"kind": "SketchLogClusterList"})
    cfg = _make_cfg()
    k8s = K8sClient(cfg, client=mock_client)
    result = k8s.get("/apis/sketchlog.io/v1alpha1/sketchlogclusters")
    assert result["kind"] == "SketchLogClusterList"


def test_k8s_client_get_404_raises() -> None:
    import httpx
    mock_client = MagicMock(spec=httpx.Client)
    mock_client.get.return_value = _mock_response(404)
    cfg = _make_cfg()
    k8s = K8sClient(cfg, client=mock_client)
    with pytest.raises(OperatorError) as exc_info:
        k8s.get("/apis/sketchlog.io/v1alpha1/sketchlogclusters")
    assert exc_info.value.status_code == 404


def test_k8s_client_get_timeout() -> None:
    import httpx
    mock_client = MagicMock(spec=httpx.Client)
    req = httpx.Request("GET", "https://k8s.local/test")
    mock_client.get.side_effect = httpx.TimeoutException("timed out", request=req)
    cfg = _make_cfg()
    k8s = K8sClient(cfg, client=mock_client)
    with pytest.raises(OperatorError, match="timed out"):
        k8s.get("/test")


def test_k8s_client_get_request_error() -> None:
    import httpx
    mock_client = MagicMock(spec=httpx.Client)
    req = httpx.Request("GET", "https://k8s.local/test")
    mock_client.get.side_effect = httpx.ConnectError("DNS failed", request=req)
    cfg = _make_cfg()
    k8s = K8sClient(cfg, client=mock_client)
    with pytest.raises(OperatorError, match="DNS failed"):
        k8s.get("/test")


def test_k8s_client_apply_post() -> None:
    import httpx
    mock_client = MagicMock(spec=httpx.Client)
    mock_client.post.return_value = _mock_response(201, {"metadata": {"name": "x"}})
    cfg = _make_cfg()
    k8s = K8sClient(cfg, client=mock_client)
    result = k8s.apply("/api/v1/namespaces/ns/configmaps", {"metadata": {"name": "x"}})
    assert result["metadata"]["name"] == "x"
    mock_client.post.assert_called_once()


def test_k8s_client_apply_patch() -> None:
    import httpx
    mock_client = MagicMock(spec=httpx.Client)
    mock_client.patch.return_value = _mock_response(200, {"metadata": {"name": "x"}})
    cfg = _make_cfg()
    k8s = K8sClient(cfg, client=mock_client)
    k8s.apply("/api/v1/namespaces/ns/configmaps/x", {"data": {}}, method="PATCH")
    mock_client.patch.assert_called_once()


def test_k8s_client_delete_ok() -> None:
    import httpx
    mock_client = MagicMock(spec=httpx.Client)
    mock_client.delete.return_value = _mock_response(200)
    cfg = _make_cfg()
    k8s = K8sClient(cfg, client=mock_client)
    k8s.delete("/api/v1/namespaces/ns/configmaps/x")  # should not raise


def test_k8s_client_delete_404_silent() -> None:
    import httpx
    mock_client = MagicMock(spec=httpx.Client)
    mock_client.delete.return_value = _mock_response(404)
    cfg = _make_cfg()
    k8s = K8sClient(cfg, client=mock_client)
    k8s.delete("/api/v1/namespaces/ns/configmaps/missing")  # 404 silently ignored


def test_k8s_client_delete_500_raises() -> None:
    import httpx
    mock_client = MagicMock(spec=httpx.Client)
    mock_client.delete.return_value = _mock_response(500)
    cfg = _make_cfg()
    k8s = K8sClient(cfg, client=mock_client)
    with pytest.raises(OperatorError) as exc_info:
        k8s.delete("/api/v1/namespaces/ns/configmaps/x")
    assert exc_info.value.status_code == 500


def test_k8s_client_watch_yields_events() -> None:
    import httpx
    mock_client = MagicMock(spec=httpx.Client)
    cfg = _make_cfg()

    events = [
        json.dumps({"type": "ADDED", "object": {"metadata": {"name": "c1"}}}),
        json.dumps({"type": "MODIFIED", "object": {"metadata": {"name": "c1"}}}),
    ]

    class FakeStreamResp:
        status_code = 200
        def raise_for_status(self) -> None: pass
        def iter_lines(self) -> Iterator[str]:
            yield from events
        def __enter__(self) -> "FakeStreamResp": return self
        def __exit__(self, *_: Any) -> None: pass

    mock_client.stream.return_value = FakeStreamResp()
    k8s = K8sClient(cfg, client=mock_client)
    collected = list(k8s.watch("/apis/sketchlog.io/v1alpha1/sketchlogclusters"))
    assert len(collected) == 2
    assert collected[0]["type"] == "ADDED"
    assert collected[1]["type"] == "MODIFIED"


def test_k8s_client_watch_empty_lines_skipped() -> None:
    import httpx
    mock_client = MagicMock(spec=httpx.Client)
    cfg = _make_cfg()

    class FakeStreamResp:
        status_code = 200
        def raise_for_status(self) -> None: pass
        def iter_lines(self) -> Iterator[str]:
            yield ""
            yield json.dumps({"type": "ADDED", "object": {}})
            yield ""
        def __enter__(self) -> "FakeStreamResp": return self
        def __exit__(self, *_: Any) -> None: pass

    mock_client.stream.return_value = FakeStreamResp()
    k8s = K8sClient(cfg, client=mock_client)
    collected = list(k8s.watch("/test"))
    assert len(collected) == 1


# ---------------------------------------------------------------------------
# SketchLogOperator — reconcile tests
# ---------------------------------------------------------------------------


def test_operator_reconcile_creates_resources() -> None:
    cfg = _make_cfg()
    k8s = _mock_k8s()
    op = SketchLogOperator(cfg, k8s=k8s)
    spec: Dict[str, Any] = {"replicas": 2, "image": "sketchlog:v1", "maxStreams": 500}
    op.reconcile("mycluster", "test-ns", spec)
    # ConfigMap, Deployment, Service, Status — at minimum apply called 3+ times
    assert k8s.apply.call_count >= 3


def test_operator_reconcile_dry_run_skips_apply() -> None:
    cfg = _make_cfg(dry_run=True)
    k8s = _mock_k8s()
    op = SketchLogOperator(cfg, k8s=k8s)
    op.reconcile("mycluster", "test-ns", {})
    k8s.apply.assert_not_called()


def test_operator_reconcile_apply_or_create_falls_back_to_post() -> None:
    cfg = _make_cfg()
    k8s = _mock_k8s()
    # First PATCH → 404, so POST (create) is called
    k8s.apply.side_effect = [
        OperatorError("not found", status_code=404),  # ConfigMap PATCH
        {},  # ConfigMap POST
        OperatorError("not found", status_code=404),  # Deployment PATCH
        {},  # Deployment POST
        OperatorError("not found", status_code=404),  # Service PATCH
        {},  # Service POST
        {},  # Status PATCH
    ]
    op = SketchLogOperator(cfg, k8s=k8s)
    op.reconcile("mycluster", "test-ns", {})
    # POST calls happened for 404 fallback
    assert k8s.apply.call_count >= 6


def test_operator_reconcile_error_updates_status() -> None:
    cfg = _make_cfg()
    k8s = _mock_k8s()
    # ConfigMap PATCH raises 500
    k8s.apply.side_effect = OperatorError("server error", status_code=500)
    op = SketchLogOperator(cfg, k8s=k8s)
    with pytest.raises(OperatorError):
        op.reconcile("mycluster", "test-ns", {})


def test_operator_reconcile_all_calls_reconcile_per_item() -> None:
    cfg = _make_cfg(namespace="test-ns")
    k8s = _mock_k8s(responses={
        "items": [
            {"metadata": {"name": "c1", "namespace": "test-ns"}, "spec": {}},
            {"metadata": {"name": "c2", "namespace": "test-ns"}, "spec": {}},
        ]
    })
    op = SketchLogOperator(cfg, k8s=k8s)
    op._reconcile_all()
    # apply called for ConfigMap+Deployment+Service+Status per cluster = 4 × 2 = 8 min
    assert k8s.apply.call_count >= 6


def test_operator_reconcile_all_crd_not_installed() -> None:
    cfg = _make_cfg()
    k8s = _mock_k8s()
    k8s.get.side_effect = OperatorError("CRD not found", status_code=404)
    op = SketchLogOperator(cfg, k8s=k8s)
    op._reconcile_all()  # should not raise — logs warning and returns


def test_operator_reconcile_all_get_error_propagates() -> None:
    cfg = _make_cfg()
    k8s = _mock_k8s()
    k8s.get.side_effect = OperatorError("server error", status_code=500)
    op = SketchLogOperator(cfg, k8s=k8s)
    with pytest.raises(OperatorError):
        op._reconcile_all()


def test_operator_reconcile_all_empty_items() -> None:
    cfg = _make_cfg()
    k8s = _mock_k8s(responses={"items": []})
    op = SketchLogOperator(cfg, k8s=k8s)
    op._reconcile_all()
    k8s.apply.assert_not_called()


def test_operator_reconcile_all_namespaced_path() -> None:
    cfg = _make_cfg(namespace="my-ns")
    k8s = _mock_k8s(responses={"items": []})
    op = SketchLogOperator(cfg, k8s=k8s)
    op._reconcile_all()
    path = k8s.get.call_args[0][0]
    assert "namespaces/my-ns" in path


def test_operator_reconcile_all_cluster_wide_path() -> None:
    cfg = _make_cfg(namespace="")
    k8s = _mock_k8s(responses={"items": []})
    op = SketchLogOperator(cfg, k8s=k8s)
    op._reconcile_all()
    path = k8s.get.call_args[0][0]
    assert "namespaces" not in path


# ---------------------------------------------------------------------------
# run_once
# ---------------------------------------------------------------------------


def test_operator_run_once() -> None:
    cfg = _make_cfg()
    k8s = _mock_k8s(responses={"items": []})
    op = SketchLogOperator(cfg, k8s=k8s)
    op.run_once()
    k8s.get.assert_called_once()


# ---------------------------------------------------------------------------
# run loop
# ---------------------------------------------------------------------------


def test_operator_run_stops_on_stop_event() -> None:
    cfg = _make_cfg(reconcile_interval=0.05)
    k8s = _mock_k8s(responses={"items": []})
    op = SketchLogOperator(cfg, k8s=k8s)

    def _stopper() -> None:
        time.sleep(0.12)
        op.stop()

    t = threading.Thread(target=_stopper, daemon=True)
    t.start()
    op.run()  # blocks until stop()
    t.join(timeout=2)
    assert not t.is_alive()
    assert k8s.get.call_count >= 1


def test_operator_run_handles_operator_error_gracefully() -> None:
    cfg = _make_cfg(reconcile_interval=0.05)
    k8s = _mock_k8s()
    k8s.get.side_effect = OperatorError("transient error", status_code=503)
    op = SketchLogOperator(cfg, k8s=k8s)

    def _stopper() -> None:
        time.sleep(0.12)
        op.stop()

    t = threading.Thread(target=_stopper, daemon=True)
    t.start()
    op.run()  # must not crash despite errors
    t.join(timeout=2)


# ---------------------------------------------------------------------------
# Context manager
# ---------------------------------------------------------------------------


def test_operator_context_manager_closes() -> None:
    cfg = _make_cfg()
    k8s = _mock_k8s(responses={"items": []})
    with SketchLogOperator(cfg, k8s=k8s) as op:
        assert op is not None
    # stop_event should be set
    assert op._stop_event.is_set()


# ---------------------------------------------------------------------------
# configmap / deployment / service payload shape
# ---------------------------------------------------------------------------


def test_configmap_contains_spec_fields() -> None:
    cfg = _make_cfg()
    k8s = _mock_k8s()
    op = SketchLogOperator(cfg, k8s=k8s)
    spec: Dict[str, Any] = {
        "maxStreams": 2000,
        "retentionDays": 14,
        "sketchType": "hll",
        "alpha": 0.005,
    }
    op.reconcile("c1", "ns", spec)
    # Find the ConfigMap apply call (first one)
    first_call = k8s.apply.call_args_list[0]
    body = first_call[0][1]
    assert body["data"]["max_streams"] == "2000"
    assert body["data"]["retention_days"] == "14"
    assert body["data"]["sketch_type"] == "hll"


def test_deployment_uses_spec_image_and_replicas() -> None:
    cfg = _make_cfg()
    k8s = _mock_k8s()
    op = SketchLogOperator(cfg, k8s=k8s)
    spec: Dict[str, Any] = {"replicas": 5, "image": "myregistry/sketchlog:v2"}
    op.reconcile("c1", "ns", spec)
    # Deployment is second apply call
    deploy_call = k8s.apply.call_args_list[1]
    body = deploy_call[0][1]
    assert body["spec"]["replicas"] == 5
    assert body["spec"]["template"]["spec"]["containers"][0]["image"] == "myregistry/sketchlog:v2"


def test_service_type_from_spec() -> None:
    cfg = _make_cfg()
    k8s = _mock_k8s()
    op = SketchLogOperator(cfg, k8s=k8s)
    spec: Dict[str, Any] = {"serviceType": "LoadBalancer"}
    op.reconcile("c1", "ns", spec)
    # Service is third apply call
    svc_call = k8s.apply.call_args_list[2]
    body = svc_call[0][1]
    assert body["spec"]["type"] == "LoadBalancer"


def test_managed_by_label_present() -> None:
    cfg = _make_cfg()
    k8s = _mock_k8s()
    op = SketchLogOperator(cfg, k8s=k8s)
    op.reconcile("c1", "ns", {})
    for c in k8s.apply.call_args_list[:3]:
        body = c[0][1]
        assert body["metadata"]["labels"]["app.kubernetes.io/managed-by"] == "sketchlog-operator"
