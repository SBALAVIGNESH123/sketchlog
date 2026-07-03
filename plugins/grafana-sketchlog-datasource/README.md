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

Configure the data source with a SketchLog endpoint such as `http://localhost:8000`.

If the SketchLog server uses `SKETCHLOG_AUTH_TOKEN`, the current frontend-only plugin can send that token from `jsonData.authToken`. Use that only in trusted self-hosted Grafana deployments. A future backend plugin can move token handling into Grafana's encrypted secure JSON storage and proxy requests server-side.
