# Kubernetes Operator

The SketchLog Kubernetes Operator automates the lifecycle management of
`SketchLogCluster` custom resources — creating and reconciling Deployments,
Services, and ConfigMaps for each cluster instance.

---

## Overview

```
kubectl apply -f crd.yaml rbac.yaml deployment.yaml
kubectl apply -f example_cluster.yaml
# → Operator creates Deployment + Service + ConfigMap for your cluster
```

The operator:
- Watches all (or a single) namespace for `SketchLogCluster` CRs
- Reconciles the desired state every 30 seconds (configurable)
- Updates `.status.phase` on each cluster to `Ready` or `Error`
- Supports `--dry-run` for safe preview without writes
- Uses only `httpx` — zero new dependencies

---

## Installation

### 1. Apply the CRD

```bash
kubectl apply -f k8s/operator/crd.yaml
```

### 2. Apply RBAC

```bash
kubectl create namespace sketchlog-system
kubectl apply -f k8s/operator/rbac.yaml
```

### 3. Deploy the Operator

```bash
kubectl apply -f k8s/operator/deployment.yaml
```

### 4. Verify

```bash
kubectl -n sketchlog-system get pods
# NAME                                  READY   STATUS    RESTARTS
# sketchlog-operator-5d9b7c6d4f-x8k2p   1/1     Running   0
```

---

## Creating a SketchLogCluster

```yaml
apiVersion: sketchlog.io/v1alpha1
kind: SketchLogCluster
metadata:
  name: production
  namespace: monitoring
spec:
  replicas: 3
  image: sketchlog/sketchlog:latest
  maxStreams: 5000
  retentionDays: 30
  sketchType: ddsketch
  alpha: 0.01
  serviceType: ClusterIP
  resources:
    requests:
      cpu: 100m
      memory: 128Mi
    limits:
      cpu: 500m
      memory: 512Mi
```

```bash
kubectl apply -f example_cluster.yaml
kubectl get sketchlogclusters -n monitoring
# NAME         REPLICAS   PHASE   AGE
# production   3          Ready   30s
```

---

## SketchLogCluster Spec Reference

| Field | Type | Default | Description |
|---|---|---|---|
| `replicas` | integer | `1` | Number of SketchLog server pods |
| `image` | string | `sketchlog/sketchlog:latest` | Container image |
| `maxStreams` | integer | `1000` | Maximum concurrent streams |
| `retentionDays` | integer | `7` | Data retention in days |
| `sketchType` | `ddsketch` \| `hll` \| `countmin` | `ddsketch` | Sketch algorithm |
| `alpha` | float | `0.01` | DDSketch relative accuracy (0.0001–0.1) |
| `serviceType` | `ClusterIP` \| `NodePort` \| `LoadBalancer` | `ClusterIP` | Kubernetes Service type |
| `resources` | object | — | Container resource requests and limits |

### Status Fields

| Field | Description |
|---|---|
| `status.phase` | `Ready`, `Error`, or `Pending` |
| `status.message` | Human-readable status detail |
| `status.lastReconcileTime` | RFC3339 timestamp of last reconcile |

---

## Resources Created

For each `SketchLogCluster` named `{name}` in namespace `{ns}`, the operator creates:

| Resource | Name | Description |
|---|---|---|
| `ConfigMap` | `{name}-config` | Server configuration (maxStreams, retentionDays, sketchType, alpha) |
| `Deployment` | `{name}-sketchlog` | SketchLog server pods with health probes |
| `Service` | `{name}-sketchlog` | Exposes gRPC (7654) and HTTP (8080) |

All resources carry the label `app.kubernetes.io/managed-by: sketchlog-operator`.

---

## Operator Configuration

The operator reads configuration from environment variables:

| Variable | Default | Description |
|---|---|---|
| `KUBERNETES_SERVICE_HOST` | — | In-cluster k8s API host (auto-set by k8s) |
| `KUBERNETES_SERVICE_PORT_HTTPS` | `443` | In-cluster k8s API port |
| `KUBE_TOKEN` | — | Bearer token override (default: mounted SA token) |
| `OPERATOR_NAMESPACE` | `""` | Namespace to watch (empty = all namespaces) |
| `RECONCILE_INTERVAL` | `30` | Seconds between full reconcile sweeps |
| `DRY_RUN` | `false` | Log intended changes without writing anything |

### CLI Flags

```
sketchlog-operator [OPTIONS]

Options:
  --namespace TEXT          Namespace to watch (default: all)
  --api-server TEXT         Kubernetes API server URL
  --reconcile-interval SECS Reconcile interval in seconds (default: 30)
  --dry-run                 Preview changes without applying
  --once                    Reconcile once and exit (CI/smoke)
  --log-level TEXT          Logging level: DEBUG, INFO, WARNING (default: INFO)
```

---

## Python API

You can embed the operator in your own code:

```python
from sketchlog.k8s_operator import OperatorConfig, SketchLogOperator

cfg = OperatorConfig(
    api_server="https://my-k8s-api:6443",
    token="my-service-account-token",
    namespace="monitoring",
    reconcile_interval=30.0,
)

with SketchLogOperator(cfg) as op:
    op.run()  # blocks; handles SIGTERM/SIGINT
```

### `OperatorConfig`

```python
OperatorConfig(
    api_server: str = "https://kubernetes.default.svc",
    token_path: str = "/var/run/secrets/kubernetes.io/serviceaccount/token",
    ca_cert_path: str = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt",
    token: str = "",
    namespace: str = "",
    reconcile_interval: float = 30.0,
    watch_timeout: int = 300,
    http_timeout: float = 10.0,
    dry_run: bool = False,
)
```

Load from environment:

```python
cfg = OperatorConfig.from_env()
```

### `K8sClient`

Low-level Kubernetes API client (httpx-based):

```python
from sketchlog.k8s_operator import K8sClient, OperatorConfig

cfg = OperatorConfig(api_server="https://k8s.local", token="tok")
with K8sClient(cfg) as k8s:
    cluster_list = k8s.get("/apis/sketchlog.io/v1alpha1/sketchlogclusters")
    for event in k8s.watch("/apis/sketchlog.io/v1alpha1/sketchlogclusters"):
        print(event["type"], event["object"]["metadata"]["name"])
```

Methods:

| Method | Description |
|---|---|
| `get(path)` | GET a resource; raises `OperatorError` on failure |
| `apply(path, body, *, method="POST")` | POST or PATCH a resource |
| `delete(path)` | DELETE a resource; 404 silently ignored |
| `watch(path, resource_version="")` | Yield watch events as dicts |

### `SketchLogOperator`

```python
from sketchlog.k8s_operator import OperatorConfig, SketchLogOperator

op = SketchLogOperator(cfg)
op.run()          # blocking reconcile loop
op.run_once()     # single reconcile sweep
op.reconcile("my-cluster", "monitoring", spec_dict)  # reconcile one cluster
op.stop()         # signal the loop to exit
```

### `OperatorError`

```python
from sketchlog.k8s_operator import OperatorError

try:
    k8s.get("/apis/sketchlog.io/v1alpha1/sketchlogclusters")
except OperatorError as e:
    print(e.status_code)  # Optional[int]
    print(str(e))         # error message
```

---

## Error Handling

- All HTTP errors are wrapped as `OperatorError(message, status_code=N)`.
- `404` on the CRD list endpoint → operator logs a warning and skips (CRD not yet installed).
- Per-cluster reconcile errors → `.status.phase = Error` is written; other clusters continue.
- Transport errors (DNS, connection reset) → wrapped as `OperatorError` with `status_code=None`.
- The reconcile loop catches `OperatorError` and logs it without crashing.

---

## RBAC

The operator requires the following permissions (see `rbac.yaml`):

| API Group | Resource | Verbs |
|---|---|---|
| `sketchlog.io` | `sketchlogclusters` | `get, list, watch` |
| `sketchlog.io` | `sketchlogclusters/status` | `get, update, patch` |
| `""` (core) | `configmaps, services, serviceaccounts` | `get, list, watch, create, update, patch, delete` |
| `apps` | `deployments` | `get, list, watch, create, update, patch, delete` |
| `""` (core) | `events` | `create, patch` |

---

## Dry Run

Preview all changes without writing to the cluster:

```bash
sketchlog-operator --dry-run --once
# [dry-run] ConfigMap monitoring/production-config
# [dry-run] Deployment monitoring/production-sketchlog
# [dry-run] Service monitoring/production-sketchlog
# [dry-run] Status monitoring/production → Ready
```

---

## Uninstalling

```bash
kubectl delete -f k8s/operator/deployment.yaml
kubectl delete -f k8s/operator/rbac.yaml
kubectl delete -f k8s/operator/crd.yaml
# CRD deletion also removes all SketchLogCluster resources
```
