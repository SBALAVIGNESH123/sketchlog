import { useEffect, useState } from 'react';
import {
  CardinalitySparkline,
  CDFCurve,
  QuantileHeatmap,
  SketchLogProvider,
  useSketchLog,
} from './index';
import { counterToNumber } from './counter';
import './index.css';

interface Metrics {
  p50: number;
  p90: number;
  p99: number;
  p99_9: number;
  unique_count: number;
  total_events: number;
  memory_footprint_bytes: number;
}

interface Anomaly {
  anomaly_score: number;
  sensitivity: number;
  is_anomalous: boolean;
}

interface QueryResult {
  results: Array<{ metric: string; value: number }>;
  execution_time_ms: number;
}

interface DashboardData {
  anomaly: Anomaly;
  query: QueryResult;
  tenantA: Metrics;
  tenantB: Metrics;
}

const api = async <T,>(path: string, init?: RequestInit): Promise<T> => {
  const response = await fetch(`/api${path}`, init);
  if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
  return response.json() as Promise<T>;
};

const formatInteger = (value: number) => new Intl.NumberFormat('en-US').format(value);
const formatLatency = (value: number) => `${value.toFixed(value >= 100 ? 0 : 1)} ms`;

function LiveDashboard() {
  const { state, isConnected, error } = useSketchLog();
  const [data, setData] = useState<DashboardData | null>(null);
  const [apiError, setApiError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    let refreshTimer: number | null = null;
    let controller: AbortController | null = null;

    const refresh = async () => {
      const currentController = new AbortController();
      controller = currentController;
      try {
        const [anomaly, query, tenantA, tenantB] = await Promise.all([
          api<Anomaly>(
            '/v1/streams/demo-current/anomaly?baseline_stream_id=demo-baseline&sensitivity=0.20',
            { signal: currentController.signal },
          ),
          api<QueryResult>('/v1/query', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            signal: currentController.signal,
            body: JSON.stringify({
              query: 'SELECT p50(latency), p99(latency), count_unique(users), event_count(errors, \'HTTP_500\') FROM "default/demo-current"',
            }),
          }),
          api<Metrics>(
            '/v1/namespaces/acme/streams/checkout/metrics',
            { signal: currentController.signal },
          ),
          api<Metrics>(
            '/v1/namespaces/globex/streams/checkout/metrics',
            { signal: currentController.signal },
          ),
        ]);
        if (active) {
          setData({ anomaly, query, tenantA, tenantB });
          setApiError(null);
        }
      } catch (refreshError) {
        if (currentController.signal.aborted) return;
        if (active) {
          setApiError(refreshError instanceof Error ? refreshError.message : 'API unavailable');
        }
      } finally {
        if (active) refreshTimer = window.setTimeout(() => void refresh(), 2000);
      }
    };

    void refresh();
    return () => {
      active = false;
      controller?.abort();
      if (refreshTimer !== null) window.clearTimeout(refreshTimer);
    };
  }, []);

  const metrics = state?.metrics;
  const eventCount = counterToNumber(metrics?.total_events ?? 0);
  const uniqueCount = counterToNumber(metrics?.unique_count ?? 0);
  const memoryBytes = counterToNumber(metrics?.memory_footprint_bytes ?? 0);
  const queryValues = Object.fromEntries(
    (data?.query.results ?? []).map((result) => [result.metric, result.value]),
  );
  const healthy = isConnected && !error && !apiError;

  return (
    <main className="launch-shell">
      <header className="launch-header">
        <div>
          <div className="eyebrow"><span className="brand-mark">S</span> SKETCHLOG CONTROL PLANE</div>
          <h1>Observe the shape of production.</h1>
          <p>Bounded-memory telemetry, live distributions, and streaming decisions—without retaining raw events.</p>
        </div>
        <div className={`system-status ${healthy ? 'is-live' : 'is-waiting'}`} role="status">
          <span className="status-dot" />
          <div><strong>{healthy ? 'SYSTEM LIVE' : 'CONNECTING'}</strong><small>default/demo-current</small></div>
        </div>
      </header>

      <section className="metric-strip" aria-label="Live stream metrics">
        <article><span>P50 LATENCY</span><strong>{formatLatency(metrics?.p50 ?? 0)}</strong><small>median request</small></article>
        <article><span>P99 LATENCY</span><strong>{formatLatency(metrics?.p99 ?? 0)}</strong><small>tail pressure</small></article>
        <article><span>UNIQUE USERS</span><strong>{formatInteger(uniqueCount)}</strong><small>HyperLogLog estimate</small></article>
        <article><span>EVENTS SEEN</span><strong>{formatInteger(eventCount)}</strong><small>and still climbing</small></article>
        <article><span>SKETCH MEMORY</span><strong>{(memoryBytes / 1024).toFixed(1)} KiB</strong><small>bounded footprint</small></article>
      </section>

      {(error || apiError) && <div className="error-banner">Waiting for telemetry: {error?.message ?? apiError}</div>}

      <section className="dashboard-grid">
        <div className="chart-stack">
          <CDFCurve height={330} color="#79f2c0" className="launch-chart" />
          <div className="lower-grid">
            <CardinalitySparkline height={128} color="#79f2c0" className="launch-chart" />
            <article className="evidence-card">
              <span className="card-label">BOUNDED MEMORY EVIDENCE</span>
              <strong>{formatInteger(eventCount)} events</strong>
              <div className="memory-line"><i style={{ width: `${Math.min(100, Math.max(8, memoryBytes / 900))}%` }} /></div>
              <p>The sketch footprint stays compact as event volume grows.</p>
            </article>
          </div>
        </div>
        <QuantileHeatmap height={546} className="launch-chart heatmap" />
      </section>

      <section className="proof-grid">
        <article className="proof-card anomaly-card">
          <div className="card-heading"><span>ANOMALY AUTO-PILOT</span><b className={data?.anomaly.is_anomalous ? 'alert' : 'nominal'}>{data?.anomaly.is_anomalous ? 'DETECTED' : 'NOMINAL'}</b></div>
          <strong className="score">{((data?.anomaly.anomaly_score ?? 0) * 100).toFixed(1)}%</strong>
          <div className="score-bar"><i style={{ width: `${Math.min(100, (data?.anomaly.anomaly_score ?? 0) * 100)}%` }} /></div>
          <p>Live distribution drift versus a fixed healthy baseline. Threshold: {((data?.anomaly.sensitivity ?? 0.2) * 100).toFixed(0)}%.</p>
        </article>

        <article className="proof-card sql-card">
          <div className="card-heading"><span>STREAMING SQL</span><b>REAL QUERY</b></div>
          <code>SELECT p99(latency), count_unique(users)<br />FROM "default/demo-current"</code>
          <div className="query-results">
            <span>p99 <strong>{formatLatency(queryValues['p99(latency)'] ?? metrics?.p99 ?? 0)}</strong></span>
            <span>users <strong>{formatInteger(queryValues['count_unique(users)'] ?? uniqueCount)}</strong></span>
            <small>{(data?.query.execution_time_ms ?? 0).toFixed(2)} ms</small>
          </div>
        </article>

        <article className="proof-card tenant-card">
          <div className="card-heading"><span>MULTI-TENANT ISOLATION</span><b>2 NAMESPACES</b></div>
          <div className="tenant-row"><span><i className="acme" />acme / checkout</span><strong>{formatLatency(data?.tenantA.p99 ?? 0)}</strong></div>
          <div className="tenant-row"><span><i className="globex" />globex / checkout</span><strong>{formatLatency(data?.tenantB.p99 ?? 0)}</strong></div>
          <p>Identical stream names, independently isolated sketches.</p>
        </article>
      </section>

      <footer>
        <span>LIVE DEMO · DETERMINISTIC TELEMETRY</span>
        <span>WebSocket + REST + SQL + Prometheus</span>
      </footer>
    </main>
  );
}

function App() {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const streamUrl = `${protocol}//${window.location.host}/api/v1/streams/demo-current/ws`;
  return <SketchLogProvider url={streamUrl}><LiveDashboard /></SketchLogProvider>;
}

export default App;
