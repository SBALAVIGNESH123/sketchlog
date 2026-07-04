# Production Readiness Checker

`sketchlog-doctor` audits a running SketchLog deployment and reports whether it is safe enough for production use.

This is different from `/health` and `/ready`:

- `/health` confirms the process is alive.
- `/ready` confirms the server can accept traffic.
- `sketchlog-doctor` checks the wider deployment posture: server endpoints, Prometheus metrics, TLS, auth, storage readiness, namespace quotas, mesh settings, and optional integration configuration files.

## Basic usage

```bash
sketchlog-doctor --endpoint http://localhost:8000
```

Example output:

```text
SketchLog production readiness check
Endpoint: http://localhost:8000

PASS  configuration/endpoint            Endpoint URL is valid
WARN  security/tls                      Endpoint does not use HTTPS; use TLS for production deployments
WARN  security/auth-token               No auth token supplied; ensure production deployments require SKETCHLOG_AUTH_TOKEN or namespace tokens
PASS  server/health                     /health endpoint is reachable
PASS  server/ready                      /ready endpoint reports readiness
PASS  observability/metrics             /metrics endpoint exposes Prometheus text
PASS  observability/prometheus-format   Prometheus HELP/TYPE metadata found
WARN  storage/storage-readiness         Durable storage was not explicitly verified; pass --expect-storage for production DB checks

Result: warn (5 pass, 3 warn, 0 fail)
```

## JSON output

Use JSON output for CI/CD and automated environment checks:

```bash
sketchlog-doctor --endpoint https://sketchlog.example.com --format json
```

The JSON report includes a summary, a redacted endpoint, and a structured list of checks. The example below is abbreviated; real output includes all checks counted in the summary:

```json
{
  "endpoint": "https://sketchlog.example.com",
  "summary": {
    "status": "warn",
    "pass_count": 7,
    "warn_count": 2,
    "fail_count": 0,
    "strict": false
  },
  "checks": [
    {
      "category": "server",
      "name": "health",
      "status": "pass",
      "message": "/health endpoint is reachable",
      "detail": null
    }
  ]
}
```

## Strict mode

By default, warnings do not fail the command. Use `--strict` when you want warnings to return a non-zero exit code:

```bash
sketchlog-doctor --endpoint https://sketchlog.example.com --strict
```

This is useful in release gates where missing TLS, auth, storage, or quota configuration should block promotion.

## Production checks

Common production checks:

```bash
sketchlog-doctor \
  --endpoint https://sketchlog.example.com \
  --auth-token "$SKETCHLOG_TOKEN" \
  --expect-auth \
  --expect-tls \
  --expect-storage \
  --namespace-quota-mb 512 \
  --repo-root . \
  --prometheus-config deploy/prometheus.yml \
  --otel-config collector.yaml \
  --grafana-dashboard dashboards/sketchlog-overview.json \
  --retention-policy ops/retention.yaml \
  --backup-policy ops/backups.yaml
```

## Mesh deployments

For mesh deployments, add `--mesh-enabled` and provide the cluster token used for mesh endpoints:

```bash
sketchlog-doctor \
  --endpoint https://sketchlog-node-a.example.com \
  --mesh-enabled \
  --cluster-token "$SKETCHLOG_CLUSTER_SECRET"
```

The checker does not print the cluster token or auth token.

## Exit codes

| Result | Exit code | Meaning |
| --- | ---: | --- |
| `pass` | 0 | No failures and no warnings, or warnings ignored outside strict mode. |
| `warn` | 0 | Warnings found, but no failures. |
| `fail` | 1 | A required check failed, or strict mode converted warnings into failure. |

## Secret handling

`sketchlog doctor` is designed to be safe for logs and CI output:

- Auth tokens are sent only as headers.
- Tokens and credential-like query parameters are redacted in reports.
- Error details are sanitized before rendering.
- The command does not print full credentialed URLs.

## What it does not do

`sketchlog doctor` does not replace monitoring, tracing, or incident response. It is a deployment audit tool that helps answer one question quickly:

> Is this SketchLog deployment configured safely enough for production?
