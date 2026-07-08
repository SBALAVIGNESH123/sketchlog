"""SketchLog Kubernetes Operator — watches SketchLogCluster CRDs and reconciles
Deployments, Services, and ConfigMaps via the Kubernetes REST API (httpx only).
"""
from __future__ import annotations

import json
import logging
import os
import signal
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, Iterator, List, Optional, Union

import httpx

__all__ = [
    "OperatorConfig",
    "OperatorError",
    "K8sClient",
    "SketchLogOperator",
]

logger = logging.getLogger(__name__)

_GROUP = "sketchlog.io"
_VERSION = "v1alpha1"
_PLURAL = "sketchlogclusters"
_MANAGED_BY_LABEL = "app.kubernetes.io/managed-by"
_MANAGED_BY_VALUE = "sketchlog-operator"


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class OperatorError(Exception):
    """Raised on unrecoverable operator errors."""

    def __init__(self, message: str, *, status_code: Optional[int] = None) -> None:
        super().__init__(message)
        self.status_code = status_code


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OperatorConfig:
    """Immutable configuration for the SketchLog Kubernetes operator."""

    api_server: str = "https://kubernetes.default.svc"
    token_path: str = "/var/run/secrets/kubernetes.io/serviceaccount/token"
    ca_cert_path: str = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"
    token: str = ""
    namespace: str = ""
    reconcile_interval: float = 30.0
    watch_timeout: int = 300
    http_timeout: float = 10.0
    dry_run: bool = False

    def __post_init__(self) -> None:
        if self.reconcile_interval <= 0:
            raise ValueError("reconcile_interval must be > 0")
        if self.http_timeout <= 0:
            raise ValueError("http_timeout must be > 0")
        if self.watch_timeout <= 0:
            raise ValueError("watch_timeout must be > 0")

    @classmethod
    def from_env(cls) -> OperatorConfig:
        """Load configuration from environment variables (in-cluster defaults)."""
        host = os.environ.get("KUBERNETES_SERVICE_HOST", "")
        port = os.environ.get("KUBERNETES_SERVICE_PORT_HTTPS", "443")
        api_server = f"https://{host}:{port}" if host else "https://kubernetes.default.svc"
        return cls(
            api_server=api_server,
            token=os.environ.get("KUBE_TOKEN", ""),
            namespace=os.environ.get("OPERATOR_NAMESPACE", ""),
            reconcile_interval=float(os.environ.get("RECONCILE_INTERVAL", "30")),
            dry_run=os.environ.get("DRY_RUN", "false").lower() == "true",
        )

    def resolve_token(self) -> str:
        """Return bearer token from field or service-account file."""
        if self.token:
            return self.token
        try:
            with open(self.token_path) as fh:
                return fh.read().strip()
        except OSError:
            return ""


# ---------------------------------------------------------------------------
# Kubernetes HTTP client
# ---------------------------------------------------------------------------


class K8sClient:
    """Thin httpx wrapper around the Kubernetes REST API."""

    def __init__(
        self,
        cfg: OperatorConfig,
        *,
        client: Optional[httpx.Client] = None,
    ) -> None:
        self._cfg = cfg
        self._client: Optional[httpx.Client] = client
        self._owned = client is None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def _make_client(self) -> httpx.Client:
        token = self._cfg.resolve_token()
        headers: Dict[str, str] = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        verify: Union[str, bool] = (
            self._cfg.ca_cert_path
            if os.path.exists(self._cfg.ca_cert_path)
            else True
        )
        return httpx.Client(
            base_url=self._cfg.api_server,
            headers=headers,
            verify=verify,
            timeout=self._cfg.http_timeout,
        )

    def _get_client(self) -> httpx.Client:
        if self._client is None:
            self._client = self._make_client()
        return self._client

    def close(self) -> None:
        if self._owned and self._client is not None:
            self._client.close()
            self._client = None

    def __enter__(self) -> K8sClient:
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Core HTTP verbs
    # ------------------------------------------------------------------

    def get(self, path: str) -> Dict[str, Any]:
        """GET a resource; raises OperatorError on failure."""
        client = self._get_client()
        try:
            resp = client.get(path)
            try:
                resp.raise_for_status()
            except httpx.HTTPStatusError as exc:
                raise OperatorError(
                    str(exc), status_code=exc.response.status_code
                ) from exc
            result: Dict[str, Any] = resp.json()
            return result
        except OperatorError:
            raise
        except httpx.TimeoutException as exc:
            raise OperatorError(f"GET {path} timed out: {exc}") from exc
        except httpx.RequestError as exc:
            raise OperatorError(f"GET {path} failed: {exc}") from exc

    def apply(
        self,
        path: str,
        body: Dict[str, Any],
        *,
        method: str = "POST",
    ) -> Dict[str, Any]:
        """POST or PATCH (server-side merge) a resource."""
        client = self._get_client()
        try:
            if method == "PATCH":
                resp = client.patch(
                    path,
                    json=body,
                    headers={"Content-Type": "application/merge-patch+json"},
                )
            else:
                resp = client.post(path, json=body)
            try:
                resp.raise_for_status()
            except httpx.HTTPStatusError as exc:
                raise OperatorError(
                    str(exc), status_code=exc.response.status_code
                ) from exc
            result: Dict[str, Any] = resp.json()
            return result
        except OperatorError:
            raise
        except httpx.TimeoutException as exc:
            raise OperatorError(f"{method} {path} timed out: {exc}") from exc
        except httpx.RequestError as exc:
            raise OperatorError(f"{method} {path} failed: {exc}") from exc

    def delete(self, path: str) -> None:
        """DELETE a resource; 404 is silently ignored."""
        client = self._get_client()
        try:
            resp = client.delete(path)
            try:
                resp.raise_for_status()
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 404:
                    return
                raise OperatorError(
                    str(exc), status_code=exc.response.status_code
                ) from exc
        except OperatorError:
            raise
        except httpx.TimeoutException as exc:
            raise OperatorError(f"DELETE {path} timed out: {exc}") from exc
        except httpx.RequestError as exc:
            raise OperatorError(f"DELETE {path} failed: {exc}") from exc

    def watch(
        self, path: str, resource_version: str = ""
    ) -> Iterator[Dict[str, Any]]:
        """Stream watch events from a Kubernetes watch endpoint."""
        params = f"watch=true&timeoutSeconds={self._cfg.watch_timeout}"
        if resource_version:
            params += f"&resourceVersion={resource_version}"
        url = f"{path}?{params}"
        client = self._get_client()
        try:
            with client.stream("GET", url) as resp:
                try:
                    resp.raise_for_status()
                except httpx.HTTPStatusError as exc:
                    raise OperatorError(
                        str(exc), status_code=exc.response.status_code
                    ) from exc
                for line in resp.iter_lines():
                    if line:
                        event: Dict[str, Any] = json.loads(line)
                        yield event
        except OperatorError:
            raise
        except httpx.TimeoutException as exc:
            raise OperatorError(f"Watch {path} timed out: {exc}") from exc
        except httpx.RequestError as exc:
            raise OperatorError(f"Watch {path} failed: {exc}") from exc


# ---------------------------------------------------------------------------
# Operator
# ---------------------------------------------------------------------------


class SketchLogOperator:
    """
    SketchLog Kubernetes Operator.

    Watches ``SketchLogCluster`` custom resources and reconciles the
    corresponding Deployment, Service, and ConfigMap for each instance.
    """

    def __init__(
        self,
        cfg: OperatorConfig,
        *,
        k8s: Optional[K8sClient] = None,
    ) -> None:
        self._cfg = cfg
        self._k8s: K8sClient = k8s if k8s is not None else K8sClient(cfg)
        self._stop_event = threading.Event()
        self._owned_k8s = k8s is None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def stop(self) -> None:
        """Signal the operator to stop after the current sleep."""
        self._stop_event.set()

    def close(self) -> None:
        self.stop()
        if self._owned_k8s:
            self._k8s.close()

    def __enter__(self) -> SketchLogOperator:
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self) -> None:
        """Run the reconcile loop until stop() is called."""
        logger.info("SketchLog Operator starting")
        ns_label = self._cfg.namespace or "all namespaces"
        logger.info("Watching: %s", ns_label)

        while not self._stop_event.is_set():
            try:
                self._reconcile_all()
            except OperatorError as exc:
                logger.error("Reconcile loop error: %s", exc)
            except Exception as exc:  # noqa: BLE001
                logger.exception("Unexpected error in operator loop: %s", exc)
            self._stop_event.wait(timeout=self._cfg.reconcile_interval)

        logger.info("SketchLog Operator stopped")

    def run_once(self) -> None:
        """Reconcile all clusters exactly once (useful for --once / CI)."""
        self._reconcile_all()

    def reconcile(self, name: str, namespace: str, spec: Dict[str, Any]) -> None:
        """Reconcile one SketchLogCluster resource to desired state."""
        logger.info("Reconciling SketchLogCluster %s/%s", namespace, name)
        try:
            self._ensure_configmap(name, namespace, spec)
            self._ensure_deployment(name, namespace, spec)
            self._ensure_service(name, namespace, spec)
            self._update_status(name, namespace, "Ready", "Reconciled successfully")
        except OperatorError as exc:
            logger.error("Failed to reconcile %s/%s: %s", namespace, name, exc)
            self._update_status(name, namespace, "Error", str(exc))
            raise

    # ------------------------------------------------------------------
    # Internal reconciliation helpers
    # ------------------------------------------------------------------

    def _reconcile_all(self) -> None:
        ns = self._cfg.namespace
        if ns:
            path = f"/apis/{_GROUP}/{_VERSION}/namespaces/{ns}/{_PLURAL}"
        else:
            path = f"/apis/{_GROUP}/{_VERSION}/{_PLURAL}"

        try:
            cluster_list = self._k8s.get(path)
        except OperatorError as exc:
            if exc.status_code == 404:
                logger.warning(
                    "SketchLogCluster CRD not installed — skipping reconcile"
                )
                return
            raise

        items: List[Dict[str, Any]] = cluster_list.get("items", [])
        logger.info("Found %d SketchLogCluster(s)", len(items))

        for item in items:
            meta: Dict[str, Any] = item.get("metadata", {})
            item_name: str = meta.get("name", "")
            item_ns: str = meta.get("namespace", "default")
            item_spec: Dict[str, Any] = item.get("spec", {})
            if item_name:
                try:
                    self.reconcile(item_name, item_ns, item_spec)
                except OperatorError:
                    pass  # already logged in reconcile()

    def _ensure_configmap(
        self, name: str, namespace: str, spec: Dict[str, Any]
    ) -> None:
        cm_name = f"{name}-config"
        path = f"/api/v1/namespaces/{namespace}/configmaps/{cm_name}"
        body: Dict[str, Any] = {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": {
                "name": cm_name,
                "namespace": namespace,
                "labels": {
                    _MANAGED_BY_LABEL: _MANAGED_BY_VALUE,
                    "sketchlog-cluster": name,
                },
            },
            "data": {
                "max_streams": str(spec.get("maxStreams", 1000)),
                "retention_days": str(spec.get("retentionDays", 7)),
                "sketch_type": str(spec.get("sketchType", "ddsketch")),
                "alpha": str(spec.get("alpha", 0.01)),
            },
        }
        if self._cfg.dry_run:
            logger.info("[dry-run] ConfigMap %s/%s", namespace, cm_name)
            return
        self._apply_or_create(path, body)

    def _ensure_deployment(
        self, name: str, namespace: str, spec: Dict[str, Any]
    ) -> None:
        deploy_name = f"{name}-sketchlog"
        path = f"/apis/apps/v1/namespaces/{namespace}/deployments/{deploy_name}"
        replicas: int = int(spec.get("replicas", 1))
        image: str = str(spec.get("image", "sketchlog/sketchlog:latest"))
        resources: Dict[str, Any] = spec.get("resources", {})
        body: Dict[str, Any] = {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {
                "name": deploy_name,
                "namespace": namespace,
                "labels": {
                    _MANAGED_BY_LABEL: _MANAGED_BY_VALUE,
                    "sketchlog-cluster": name,
                },
            },
            "spec": {
                "replicas": replicas,
                "selector": {"matchLabels": {"sketchlog-cluster": name}},
                "template": {
                    "metadata": {
                        "labels": {
                            "sketchlog-cluster": name,
                            _MANAGED_BY_LABEL: _MANAGED_BY_VALUE,
                        }
                    },
                    "spec": {
                        "serviceAccountName": f"{name}-sa",
                        "containers": [
                            {
                                "name": "sketchlog",
                                "image": image,
                                "ports": [
                                    {"containerPort": 7654, "name": "grpc"},
                                    {"containerPort": 8080, "name": "http"},
                                ],
                                "envFrom": [
                                    {"configMapRef": {"name": f"{name}-config"}}
                                ],
                                "resources": resources,
                                "readinessProbe": {
                                    "httpGet": {"path": "/health", "port": 8080},
                                    "initialDelaySeconds": 5,
                                    "periodSeconds": 10,
                                },
                                "livenessProbe": {
                                    "httpGet": {"path": "/health", "port": 8080},
                                    "initialDelaySeconds": 15,
                                    "periodSeconds": 20,
                                },
                            }
                        ],
                    },
                },
            },
        }
        if self._cfg.dry_run:
            logger.info("[dry-run] Deployment %s/%s", namespace, deploy_name)
            return
        self._apply_or_create(path, body)

    def _ensure_service(
        self, name: str, namespace: str, spec: Dict[str, Any]
    ) -> None:
        svc_name = f"{name}-sketchlog"
        path = f"/api/v1/namespaces/{namespace}/services/{svc_name}"
        svc_type: str = str(spec.get("serviceType", "ClusterIP"))
        body: Dict[str, Any] = {
            "apiVersion": "v1",
            "kind": "Service",
            "metadata": {
                "name": svc_name,
                "namespace": namespace,
                "labels": {
                    _MANAGED_BY_LABEL: _MANAGED_BY_VALUE,
                    "sketchlog-cluster": name,
                },
            },
            "spec": {
                "selector": {"sketchlog-cluster": name},
                "type": svc_type,
                "ports": [
                    {"name": "grpc", "port": 7654, "targetPort": 7654, "protocol": "TCP"},
                    {"name": "http", "port": 8080, "targetPort": 8080, "protocol": "TCP"},
                ],
            },
        }
        if self._cfg.dry_run:
            logger.info("[dry-run] Service %s/%s", namespace, svc_name)
            return
        self._apply_or_create(path, body)

    def _update_status(
        self, name: str, namespace: str, phase: str, message: str
    ) -> None:
        path = (
            f"/apis/{_GROUP}/{_VERSION}/namespaces/{namespace}"
            f"/{_PLURAL}/{name}/status"
        )
        body: Dict[str, Any] = {
            "status": {
                "phase": phase,
                "message": message,
                "lastReconcileTime": time.strftime(
                    "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
                ),
            }
        }
        if self._cfg.dry_run:
            logger.info("[dry-run] Status %s/%s → %s", namespace, name, phase)
            return
        try:
            self._k8s.apply(path, body, method="PATCH")
        except OperatorError as exc:
            logger.warning(
                "Could not update status for %s/%s: %s", namespace, name, exc
            )

    def _apply_or_create(self, path: str, body: Dict[str, Any]) -> None:
        """PATCH resource; fall back to POST (create) on 404."""
        try:
            self._k8s.apply(path, body, method="PATCH")
        except OperatorError as exc:
            if exc.status_code == 404:
                collection = path.rsplit("/", 1)[0]
                self._k8s.apply(collection, body, method="POST")
            else:
                raise


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="SketchLog Kubernetes Operator")
    parser.add_argument("--namespace", default="", help="Namespace to watch (default: all)")
    parser.add_argument("--api-server", default="", help="Kubernetes API server URL")
    parser.add_argument(
        "--reconcile-interval", type=float, default=30.0, metavar="SECS"
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--once", action="store_true", help="Reconcile once and exit")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    base = OperatorConfig.from_env()
    cfg = OperatorConfig(
        api_server=args.api_server or base.api_server,
        token=base.token,
        token_path=base.token_path,
        ca_cert_path=base.ca_cert_path,
        namespace=args.namespace or base.namespace,
        reconcile_interval=args.reconcile_interval,
        dry_run=args.dry_run,
    )

    with SketchLogOperator(cfg) as op:
        if args.once:
            op.run_once()
            return

        def _handle(sig: int, _frame: Any) -> None:
            logger.info("Signal %d — stopping", sig)
            op.stop()

        signal.signal(signal.SIGTERM, _handle)
        signal.signal(signal.SIGINT, _handle)
        op.run()


if __name__ == "__main__":
    main()
