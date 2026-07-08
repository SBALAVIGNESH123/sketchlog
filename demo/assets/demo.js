/* SketchLog Interactive Playground — demo.js */
'use strict';

// ─── DDSketch (browser implementation) ────────────────────────────────────────
class DDSketch {
  constructor(alpha = 0.01) {
    this.alpha = alpha;
    this.gamma = 1 + 2 * alpha / (1 - alpha);
    this.logGamma = Math.log(this.gamma);
    this.buckets = new Map();
    this.count = 0;
    this.min = Infinity;
    this.max = -Infinity;
  }

  _bucketIndex(v) {
    return Math.ceil(Math.log(v) / this.logGamma);
  }

  add(value) {
    if (value <= 0) return;
    const idx = this._bucketIndex(value);
    this.buckets.set(idx, (this.buckets.get(idx) || 0) + 1);
    this.count++;
    if (value < this.min) this.min = value;
    if (value > this.max) this.max = value;
  }

  quantile(q) {
    if (this.count === 0) return null;
    const target = Math.ceil(q * this.count);
    const sorted = Array.from(this.buckets.entries()).sort((a, b) => a[0] - b[0]);
    let cumulative = 0;
    for (const [idx, cnt] of sorted) {
      cumulative += cnt;
      if (cumulative >= target) {
        return 2 * Math.pow(this.gamma, idx) / (1 + this.gamma);
      }
    }
    return this.max;
  }

  reset() {
    this.buckets = new Map();
    this.count = 0;
    this.min = Infinity;
    this.max = -Infinity;
  }

  get sortedBuckets() {
    return Array.from(this.buckets.entries()).sort((a, b) => a[0] - b[0]);
  }
}

// ─── Sketch Demo ──────────────────────────────────────────────────────────────
let sketch = new DDSketch(0.01);

function fmt(v) {
  if (v == null || !isFinite(v)) return '—';
  return v >= 1000 ? (v/1000).toFixed(2) + 'k' : v.toFixed(2);
}

function updateSketchStats() {
  document.getElementById('s-count').textContent = sketch.count || '—';
  document.getElementById('s-min').textContent   = fmt(sketch.min === Infinity ? null : sketch.min);
  document.getElementById('s-max').textContent   = fmt(sketch.max === -Infinity ? null : sketch.max);
  document.getElementById('s-p50').textContent   = fmt(sketch.quantile(0.50));
  document.getElementById('s-p95').textContent   = fmt(sketch.quantile(0.95));
  document.getElementById('s-p99').textContent   = fmt(sketch.quantile(0.99));

  // chart
  const chart = document.getElementById('sketch-chart');
  chart.innerHTML = '';
  if (sketch.count > 0) {
    const buckets = sketch.sortedBuckets;
    const maxCnt  = Math.max(...buckets.map(b => b[1]));
    buckets.forEach(([, cnt]) => {
      const bar = document.createElement('div');
      bar.className = 'mini-chart-bar';
      bar.style.height = `${Math.max(4, (cnt / maxCnt) * 60)}px`;
      chart.appendChild(bar);
    });
  }

  // accuracy note
  const p95 = sketch.quantile(0.95);
  if (p95 != null) {
    const err = sketch.alpha * 100;
    document.getElementById('sketch-accuracy').textContent =
      `Max relative error ±${err.toFixed(1)}% — p95 true value between ${fmt(p95*(1-sketch.alpha))} and ${fmt(p95*(1+sketch.alpha))}`;
  }
}

document.getElementById('sketch-alpha').addEventListener('input', e => {
  const v = parseFloat(e.target.value);
  document.getElementById('sketch-alpha-val').textContent = v.toFixed(3);
  sketch = new DDSketch(v);
  updateSketchStats();
});

document.getElementById('sketch-add-btn').addEventListener('click', () => {
  const raw = document.getElementById('sketch-input').value;
  const vals = raw.split(/[,\n\s]+/).map(s => parseFloat(s.trim())).filter(n => isFinite(n) && n > 0);
  if (vals.length === 0) { alert('No valid positive numbers found.'); return; }
  vals.forEach(v => sketch.add(v));
  updateSketchStats();
});

document.getElementById('sketch-reset-btn').addEventListener('click', () => {
  sketch.reset();
  updateSketchStats();
});

// Init with defaults
document.getElementById('sketch-add-btn').click();

// ─── Stream Demo ──────────────────────────────────────────────────────────────
const streamStore = new Map();
let writtenCount = 0, readCount = 0;

function logLine(text, cls = '') {
  const box  = document.getElementById('stream-log');
  const line = document.createElement('div');
  line.className = 'log-line' + (cls ? ' ' + cls : '');
  const ts = new Date().toISOString().substr(11, 12);
  line.textContent = `[${ts}] ${text}`;
  box.appendChild(line);
  box.scrollTop = box.scrollHeight;
}

document.getElementById('stream-write-btn').addEventListener('click', () => {
  const path = document.getElementById('stream-path').value.trim();
  if (!path) { logLine('ERROR: stream path is empty', 'error'); return; }
  const lines = document.getElementById('stream-input').value.trim().split('\n').filter(Boolean);
  if (!streamStore.has(path)) streamStore.set(path, []);
  let ok = 0;
  lines.forEach(line => {
    try {
      const rec = JSON.parse(line);
      streamStore.get(path).push({ ...rec, _ingested: Date.now() });
      logLine(`WRITE ${path} → ${JSON.stringify(rec)}`, 'write');
      ok++;
    } catch {
      logLine(`ERROR: invalid JSON: ${line}`, 'error');
    }
  });
  writtenCount += ok;
  document.getElementById('s-written').textContent = writtenCount;
});

document.getElementById('stream-read-btn').addEventListener('click', () => {
  const path = document.getElementById('stream-path').value.trim();
  const records = streamStore.get(path) || [];
  if (records.length === 0) {
    logLine(`READ ${path} → (empty — write first)`, 'read');
    return;
  }
  records.forEach((r, i) => {
    logLine(`READ ${path}[${i}] → ${JSON.stringify(r)}`, 'read');
    readCount++;
  });
  document.getElementById('s-read').textContent = readCount;
});

document.getElementById('stream-reset-btn').addEventListener('click', () => {
  streamStore.clear();
  writtenCount = 0; readCount = 0;
  document.getElementById('s-written').textContent = '0';
  document.getElementById('s-read').textContent    = '0';
  document.getElementById('stream-log').innerHTML  = '';
  logLine('Stream store cleared.');
});

// ─── Export Demo ──────────────────────────────────────────────────────────────
function previewExport(type) {
  const now = Math.floor(Date.now() / 1000);
  const path = document.getElementById('stream-path').value.trim() || 'myapp/latency/api';
  const metricName = path.replace(/\/g, '_');

  if (type === 'loki') {
    const url  = document.getElementById('loki-url').value;
    const job  = document.getElementById('loki-job').value;
    const payload = {
      streams: [{
        stream: { job, stream_path: path },
        values: [
          [`${now}000000000`, `p50=25.1 p95=98.4 p99=187.2 count=1000 stream=${path}`],
          [`${now + 60}000000000`, `p50=26.3 p95=102.1 p99=192.7 count=1020 stream=${path}`],
        ]
      }]
    };
    document.getElementById('loki-output').textContent =
      `POST ${url}/loki/api/v1/push\nContent-Type: application/json\n\n` +
      JSON.stringify(payload, null, 2);
  }

  if (type === 'datadog') {
    const site   = document.getElementById('dd-site').value;
    const prefix = document.getElementById('dd-prefix').value;
    const payload = {
      series: [
        { metric: `${prefix}${metricName}.p50`, type: 0, points: [[now, 25.1]], tags: [`stream:${path}`] },
        { metric: `${prefix}${metricName}.p95`, type: 0, points: [[now, 98.4]], tags: [`stream:${path}`] },
        { metric: `${prefix}${metricName}.p99`, type: 0, points: [[now, 187.2]], tags: [`stream:${path}`] },
        { metric: `${prefix}${metricName}.count`, type: 1, points: [[now, 1000]], tags: [`stream:${path}`] },
      ]
    };
    document.getElementById('dd-output').textContent =
      `POST https://api.${site}/api/v2/series\nDD-API-KEY: <your-api-key>\n\n` +
      JSON.stringify(payload, null, 2);
  }

  if (type === 'newrelic') {
    const region   = document.getElementById('nr-region').value;
    const evtType  = document.getElementById('nr-type').value;
    const endpoint = region === 'EU'
      ? 'https://insights-collector.eu01.nr-data.net/v1/accounts/{ACCOUNT_ID}/events'
      : 'https://insights-collector.newrelic.com/v1/accounts/{ACCOUNT_ID}/events';
    const payload = [{
      eventType: evtType,
      stream_path: path,
      p50: 25.1, p95: 98.4, p99: 187.2,
      count: 1000,
      timestamp: now,
    }];
    document.getElementById('nr-output').textContent =
      `POST ${endpoint}\nX-Insert-Key: <your-license-key>\n\n` +
      JSON.stringify(payload, null, 2);
  }
}

// ─── Export tab switching ─────────────────────────────────────────────────────
document.querySelectorAll('.tab-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
    document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
    btn.classList.add('active');
    document.getElementById(btn.dataset.target).classList.add('active');
  });
});

// ─── Code Snippets ────────────────────────────────────────────────────────────
const SNIPPETS = {
  basic: `from sketchlog import SketchLog

sl = SketchLog({"server": "http://localhost:7654"})

# Write latency values
for latency_ms in [12, 25, 50, 100, 200, 500]:
    sl.write("myapp/latency/api", latency_ms)

# Query quantiles
p95 = sl.query("myapp/latency/api", quantile=0.95)
print(f"p95 = {p95:.1f} ms")`,

  stream: `from sketchlog import SketchLog

sl = SketchLog({"server": "http://localhost:7654"})

# Write a batch
sl.write_batch("myapp/errors/checkout", [
    {"value": 1, "ts": 1700000000},
    {"value": 0, "ts": 1700000001},
    {"value": 1, "ts": 1700000002},
])

# Read back
records = sl.read("myapp/errors/checkout", limit=100)
for r in records:
    print(r)`,

  agent: `# sketchlog-agent.json
{
  "server": "http://localhost:7654",
  "namespace": "myapp",
  "scrape_interval": 15,
  "targets": [
    {
      "url": "http://app:9090/metrics",
      "mappings": [
        {
          "metric": "http_request_duration_seconds",
          "stream": "latency/http",
          "type": "latency",
          "labels": {"method": "GET", "status": "200"}
        }
      ]
    }
  ]
}

# Run the agent
sketchlog-agent --config sketchlog-agent.json`,

  loki: `from sketchlog.exporters import LokiExporter, LokiConfig, LokiStream

cfg = LokiConfig(
    url="http://loki:3100",
    token="my-bearer-token",   # optional
)

with LokiExporter(cfg) as exp:
    exp.push_stream(LokiStream(
        labels={"job": "sketchlog", "stream": "myapp/latency/api"},
        lines=["p50=25.1 p95=98.4 p99=187.2 count=1000"],
    ))`
};

function setSnippet(key) {
  document.querySelectorAll('.snippet-btn').forEach(b => b.classList.remove('active'));
  document.querySelector(`.snippet-btn[data-snippet="${key}"]`).classList.add('active');
  document.getElementById('snippet-code').textContent = SNIPPETS[key];
}

document.querySelectorAll('.snippet-btn').forEach(btn => {
  btn.addEventListener('click', () => setSnippet(btn.dataset.snippet));
});
setSnippet('basic');
