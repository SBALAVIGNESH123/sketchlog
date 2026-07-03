# SketchLog Grafana Data Source Plugin

This plugin lets Grafana query SketchLog streams directly through the SketchLog HTTP API.

It is different from the existing dashboard JSON in `dashboards/sketchlog-overview.json`. The dashboard JSON uses metrics that have already been exported to Prometheus. This plugin turns SketchLog itself into a Grafana query backend.

## Supported queries

- `p50(stream)`
- `p95(stream)`
- `p99(stream)`
- `unique_count(stream)`
- `event_count(stream, event)`
- `slo_burn_rate(stream)`
- SQL queries through `/v1/query`

## Development

```bash
cd plugins/grafana-sketchlog-datasource
npm install
npm run typecheck
npm run build
```

Configure the data source with a SketchLog endpoint such as `http://localhost:8000` and a default namespace such as `default`.

## Authentication model

This is a frontend-only Grafana data source. It does not store or send SketchLog auth tokens, because frontend plugin configuration is visible to the browser. For authenticated SketchLog deployments, put Grafana behind a trusted reverse proxy that injects credentials server-side, or use the existing Prometheus dashboard path. A future backend data source can add Grafana secure JSON storage and proxy requests server-side.
