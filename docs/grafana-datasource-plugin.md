# Grafana Data Source Plugin

SketchLog already ships a Grafana dashboard that reads metrics through Prometheus. The Grafana data source plugin is different from the Grafana dashboard and is a separate integration: it lets Grafana query SketchLog directly through the SketchLog HTTP API.

## When to use it

Use the dashboard JSON when Prometheus is already scraping SketchLog metrics and you want a ready-made overview.

Use the data source plugin when you want Grafana panels to call SketchLog directly for sketch queries such as percentiles, unique counts, event counts, SLO burn rates, or Streaming SQL results.

## Plugin location

The plugin source lives in:

```text
plugins/grafana-sketchlog-datasource
```

## Supported queries

The initial plugin supports:

- `p50(stream)`
- `p95(stream)`
- `p99(stream)`
- `unique_count(stream)`
- `event_count(stream, event)`
- `slo_burn_rate(stream)`
- SQL queries through `/v1/query`

All non-SQL queries support namespaces. If no namespace is supplied, the configured default namespace is used.

## Configure the data source

1. Build or install the plugin into Grafana's plugin directory.
2. Add a new data source with type `SketchLog`.
3. Set the SketchLog endpoint, for example `http://localhost:8000`.
4. Set the default namespace, usually `default`.
5. If your SketchLog server uses `SKETCHLOG_AUTH_TOKEN`, configure the token field.

The first version is a frontend-only plugin. Token support is intended for trusted self-hosted Grafana deployments. A future backend plugin can proxy requests through Grafana and store tokens in Grafana secure JSON storage.

## Example queries

```text
p99(default/api.latency)
unique_count(default/api.latency)
event_count(default/api.latency, error)
slo_burn_rate(default/api.latency, baseline=api.latency.baseline)
```

SQL example:

```sql
SELECT p99(latency), unique_count(users) FROM api.latency
```

## Sample dashboard

A sample dashboard is included at:

```text
plugins/grafana-sketchlog-datasource/dashboards/sketchlog-direct-overview.json
```

It demonstrates direct p99 latency, unique user count, and event count panels using the SketchLog data source instead of Prometheus.

