/* SketchLog Product Evaluation Playground */
'use strict';

const $ = (id) => document.getElementById(id);

function formatNumber(value) {
  if (value == null || !Number.isFinite(value)) return '-';
  if (Math.abs(value) >= 1000000) return `${(value / 1000000).toFixed(2)}m`;
  if (Math.abs(value) >= 1000) return `${(value / 1000).toFixed(2)}k`;
  if (Math.abs(value) >= 100) return value.toFixed(0);
  if (Math.abs(value) >= 10) return value.toFixed(1);
  return value.toFixed(2);
}

function escapeHtml(value) {
  return String(value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function setText(id, value) {
  const node = $(id);
  if (node) node.textContent = value;
}

function copyText(text, statusId) {
  const status = $(statusId);
  const done = () => {
    if (status) {
      status.textContent = 'Copied';
      window.setTimeout(() => { status.textContent = ''; }, 1800);
    }
  };
  if (navigator.clipboard && window.isSecureContext) {
    navigator.clipboard.writeText(text).then(done).catch(() => {
      if (status) status.textContent = 'Copy unavailable in this browser';
    });
  } else if (status) {
    status.textContent = 'Select the text and copy manually';
  }
}

const navToggle = $('nav-toggle');
const navLinks = $('nav-links');
if (navToggle && navLinks) {
  navToggle.addEventListener('click', () => {
    const open = navLinks.classList.toggle('open');
    navToggle.setAttribute('aria-expanded', String(open));
  });
  navLinks.querySelectorAll('a').forEach((link) => {
    link.addEventListener('click', () => {
      navLinks.classList.remove('open');
      navToggle.setAttribute('aria-expanded', 'false');
    });
  });
}

class DDSketch {
  constructor(alpha = 0.01) {
    this.alpha = alpha;
    this.gamma = 1 + (2 * alpha / (1 - alpha));
    this.logGamma = Math.log(this.gamma);
    this.buckets = new Map();
    this.count = 0;
    this.min = Infinity;
    this.max = -Infinity;
  }

  bucketIndex(value) {
    return Math.ceil(Math.log(value) / this.logGamma);
  }

  add(value) {
    if (value <= 0 || !Number.isFinite(value)) return;
    const index = this.bucketIndex(value);
    this.buckets.set(index, (this.buckets.get(index) || 0) + 1);
    this.count += 1;
    this.min = Math.min(this.min, value);
    this.max = Math.max(this.max, value);
  }

  quantile(q) {
    if (this.count === 0) return null;
    const target = Math.ceil(q * this.count);
    const sorted = this.sortedBuckets;
    let seen = 0;
    for (const [index, count] of sorted) {
      seen += count;
      if (seen >= target) {
        return 2 * Math.pow(this.gamma, index) / (1 + this.gamma);
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

const TOUR_STEPS = [
  ['Bounded-memory telemetry problem', 'Raw telemetry grows without mercy. SketchLog keeps useful analytics in fixed-size summaries so teams can ask production questions without storing every event forever.', ['bounded memory', 'observability', 'cost control']],
  ['DDSketch percentiles', 'Latency streams keep p50, p95, and p99 estimates with configurable relative accuracy. This is the core pattern behind fast tail-latency visibility.', ['p95', 'p99', 'relative accuracy']],
  ['HyperLogLog-style cardinality', 'Unique users, sessions, tenants, or trace IDs can be estimated without storing every identifier in memory.', ['cardinality', 'unique users', 'HLL']],
  ['Count-Min frequency tracking', 'Track top event types, error codes, endpoints, and noisy tenants with approximate counters suitable for high-volume streams.', ['top events', 'frequency', 'CMS']],
  ['Stream writes and reads', 'SketchLog organizes telemetry by stream paths. The browser stream panel demonstrates write/read semantics safely without a backend.', ['streams', 'JSON records', 'API shape']],
  ['Streaming SQL examples', 'SQL-style queries make sketches usable for humans: group by service, filter by namespace, compare canaries, and summarize tenants.', ['streaming SQL', 'group by', 'copyable queries']],
  ['Anomaly before/after comparison', 'Compare current windows against a baseline to spot latency spikes, traffic shifts, or error bursts early.', ['anomaly', 'baseline', 'incident response']],
  ['SLO and canary analysis', 'Sketch summaries support SLO burn and canary risk checks without requiring every raw request to stay resident.', ['SLO', 'burn rate', 'canary']],
  ['Multi-tenant namespace isolation', 'Namespaces keep tenants, services, and environments separated while still letting operators reason about fleet-wide behavior.', ['multi-tenancy', 'namespaces', 'isolation']],
  ['Sketch Mesh aggregation', 'Distributed nodes can merge sketches, allowing fleet-level summaries without centralizing all raw events.', ['mesh', 'distributed', 'mergeable sketches']],
  ['Exporter payloads', 'Preview how SketchLog summaries can flow into Loki, Datadog, and New Relic before configuring external systems.', ['Loki', 'Datadog', 'New Relic']],
  ['Storage durability proof links', 'The proof section links browser simulation to reproducible local checks for in-memory, PostgreSQL, and OmniKV-backed storage paths.', ['durability', 'PostgreSQL', 'OmniKV']],
  ['Local Docker proof instructions', 'Run the Docker demo stack and smoke verifier to prove real ingestion, dashboard, and metrics behavior on your machine.', ['Docker', 'smoke test', 'local proof']],
  ['Final launch call to action', 'After evaluating the playground and local proof commands, star the repository, try the package, and share feedback from real workloads.', ['GitHub star', 'try locally', 'feedback']],
].map(([title, body, tags]) => ({ title, body, tags }));

let activeTourStep = 0;

function renderTour() {
  const list = $('tour-list');
  if (!list) return;
  list.innerHTML = TOUR_STEPS.map((step, index) => (
    `<li><button type="button" class="${index === activeTourStep ? 'active' : ''}" data-step="${index}">${index + 1}. ${escapeHtml(step.title)}</button></li>`
  )).join('');
  list.querySelectorAll('button').forEach((button) => {
    button.addEventListener('click', () => {
      activeTourStep = Number(button.dataset.step);
      renderTour();
    });
  });
  const step = TOUR_STEPS[activeTourStep];
  setText('tour-count', `Step ${activeTourStep + 1} of ${TOUR_STEPS.length}`);
  setText('tour-title', step.title);
  setText('tour-body', step.body);
  $('tour-tags').innerHTML = step.tags.map((tag) => `<span>${escapeHtml(tag)}</span>`).join('');
  $('tour-prev').disabled = activeTourStep === 0;
  $('tour-next').textContent = activeTourStep === TOUR_STEPS.length - 1 ? 'Restart tour' : 'Next step';
}

if ($('tour-prev')) {
  $('tour-prev').addEventListener('click', () => {
    activeTourStep = Math.max(0, activeTourStep - 1);
    renderTour();
  });
}

if ($('tour-next')) {
  $('tour-next').addEventListener('click', () => {
    activeTourStep = activeTourStep === TOUR_STEPS.length - 1 ? 0 : activeTourStep + 1;
    renderTour();
  });
}

const SCENARIOS = {
  steady: {
    production: {
      latencies: [28, 31, 36, 41, 44, 48, 52, 56, 62, 69, 74, 81, 96, 124, 155],
      users: 18420, sessions: 39210, top: 'checkout.completed', anomaly: 'Healthy', detail: 'baseline aligned', slo: '0.7x', canary: 'Low', status: 'ok',
      mesh: [['us-east-1', 'healthy', '42k events/s'], ['eu-west-1', 'healthy', '31k events/s'], ['ap-south-1', 'healthy', '18k events/s']],
    },
    checkout: {
      latencies: [21, 24, 29, 33, 37, 41, 46, 51, 58, 64, 79, 88, 111, 138, 166],
      users: 9820, sessions: 22140, top: 'cart.updated', anomaly: 'Healthy', detail: 'no drift', slo: '0.5x', canary: 'Low', status: 'ok',
      mesh: [['checkout-a', 'healthy', '18k events/s'], ['checkout-b', 'healthy', '17k events/s'], ['checkout-c', 'healthy', '15k events/s']],
    },
    payments: {
      latencies: [40, 45, 51, 58, 63, 72, 80, 91, 104, 119, 136, 160, 188, 230, 280],
      users: 6330, sessions: 11780, top: 'payment.authorized', anomaly: 'Watch', detail: 'tail rising', slo: '1.1x', canary: 'Medium', status: 'warn',
      mesh: [['payments-a', 'healthy', '7k events/s'], ['payments-b', 'healthy', '6k events/s'], ['payments-c', 'suspect', '2k events/s']],
    },
  },
  spike: {
    production: {
      latencies: [32, 38, 44, 53, 66, 80, 110, 155, 210, 285, 360, 480, 620, 810, 1040],
      users: 20120, sessions: 43800, top: 'http.503', anomaly: 'Triggered', detail: 'p99 +315%', slo: '3.8x', canary: 'High', status: 'danger',
      mesh: [['us-east-1', 'healthy', '43k events/s'], ['eu-west-1', 'suspect', '29k events/s'], ['ap-south-1', 'healthy', '19k events/s']],
    },
    checkout: {
      latencies: [29, 35, 44, 61, 84, 120, 180, 260, 390, 520, 690, 840, 980, 1130, 1290],
      users: 10480, sessions: 23890, top: 'checkout.timeout', anomaly: 'Triggered', detail: 'timeout burst', slo: '5.2x', canary: 'High', status: 'danger',
      mesh: [['checkout-a', 'healthy', '18k events/s'], ['checkout-b', 'suspect', '9k events/s'], ['checkout-c', 'suspect', '8k events/s']],
    },
    payments: {
      latencies: [50, 64, 88, 130, 190, 260, 340, 430, 560, 710, 880, 1040, 1190, 1360, 1510],
      users: 7110, sessions: 13220, top: 'payment.retry', anomaly: 'Triggered', detail: 'retry storm', slo: '6.4x', canary: 'High', status: 'danger',
      mesh: [['payments-a', 'suspect', '5k events/s'], ['payments-b', 'healthy', '6k events/s'], ['payments-c', 'down', '0 events/s']],
    },
  },
  canary: {
    production: {
      latencies: [26, 29, 34, 39, 47, 58, 73, 96, 135, 205, 310, 450, 610, 780, 930],
      users: 19040, sessions: 40100, top: 'canary.error_rate', anomaly: 'Watch', detail: 'canary p99 +178%', slo: '2.1x', canary: 'Elevated', status: 'warn',
      mesh: [['stable-fleet', 'healthy', '54k events/s'], ['canary-fleet', 'suspect', '3k events/s'], ['control-fleet', 'healthy', '12k events/s']],
    },
    checkout: {
      latencies: [22, 27, 31, 36, 43, 52, 67, 91, 140, 230, 360, 510, 670, 790, 880],
      users: 10130, sessions: 22760, top: 'canary.checkout_error', anomaly: 'Watch', detail: 'new build drift', slo: '1.9x', canary: 'Elevated', status: 'warn',
      mesh: [['stable-checkout', 'healthy', '46k events/s'], ['canary-checkout', 'suspect', '2k events/s'], ['shadow-checkout', 'healthy', '4k events/s']],
    },
    payments: {
      latencies: [42, 47, 54, 62, 75, 93, 125, 170, 245, 330, 420, 540, 690, 820, 990],
      users: 6800, sessions: 12350, top: 'risk_model.timeout', anomaly: 'Watch', detail: 'model v2 slower', slo: '2.7x', canary: 'Elevated', status: 'warn',
      mesh: [['stable-payments', 'healthy', '12k events/s'], ['canary-payments', 'suspect', '1k events/s'], ['fraud-worker', 'healthy', '4k events/s']],
    },
  },
};

let dashboardTick = 0;

function percentile(values, q) {
  const sorted = [...values].sort((a, b) => a - b);
  if (sorted.length === 0) return null;
  const index = Math.min(sorted.length - 1, Math.ceil(q * sorted.length) - 1);
  return sorted[index];
}

function renderBars(id, values) {
  const target = $(id);
  if (!target) return;
  const max = Math.max(...values, 1);
  target.innerHTML = values.map((value) => {
    const height = Math.max(8, (value / max) * 132);
    return `<div class="chart-bar" style="height:${height}px" title="${value} ms"></div>`;
  }).join('');
}

function updateDashboard() {
  const namespace = $('namespace-select')?.value || 'production';
  const scenario = $('scenario-select')?.value || 'steady';
  const base = SCENARIOS[scenario][namespace];
  const jitter = dashboardTick % 4;
  const latencies = base.latencies.map((value, index) => value + ((index % 3) * jitter));
  setText('dash-p50', `${formatNumber(percentile(latencies, 0.50))} ms`);
  setText('dash-p95', `${formatNumber(percentile(latencies, 0.95))} ms`);
  setText('dash-p99', `${formatNumber(percentile(latencies, 0.99))} ms`);
  setText('dash-users', formatNumber(base.users + (dashboardTick * 17)));
  setText('dash-sessions', formatNumber(base.sessions + (dashboardTick * 29)));
  setText('dash-top-event', base.top);
  setText('dash-anomaly', base.anomaly);
  setText('dash-anomaly-detail', base.detail);
  setText('dash-slo', base.slo);
  setText('dash-canary', base.canary);
  ['dash-anomaly', 'dash-slo', 'dash-canary'].forEach((id) => {
    const card = $(id)?.closest('.metric-card');
    if (card) {
      card.classList.remove('ok', 'warn', 'danger');
      card.classList.add(base.status);
    }
  });
  renderBars('dashboard-chart', latencies);
  const mesh = $('mesh-nodes');
  if (mesh) {
    mesh.innerHTML = base.mesh.map(([name, status, rate]) => (
      `<div class="mesh-node ${status === 'healthy' ? '' : status === 'down' ? 'danger' : 'warn'}">
        <span class="node-dot"></span>
        <strong>${escapeHtml(name)}</strong>
        <span>${escapeHtml(status)} - ${escapeHtml(rate)}</span>
      </div>`
    )).join('');
  }
}

['namespace-select', 'scenario-select'].forEach((id) => {
  if ($(id)) $(id).addEventListener('change', updateDashboard);
});

if ($('advance-sample-btn')) {
  $('advance-sample-btn').addEventListener('click', () => {
    dashboardTick += 1;
    updateDashboard();
  });
}

let sketch = new DDSketch(0.01);

function updateSketchStats() {
  setText('s-count', sketch.count || '-');
  setText('s-min', formatNumber(sketch.min === Infinity ? null : sketch.min));
  setText('s-max', formatNumber(sketch.max === -Infinity ? null : sketch.max));
  setText('s-p50', formatNumber(sketch.quantile(0.50)));
  setText('s-p95', formatNumber(sketch.quantile(0.95)));
  setText('s-p99', formatNumber(sketch.quantile(0.99)));
  const chart = $('sketch-chart');
  if (chart) {
    const buckets = sketch.sortedBuckets;
    const maxCount = Math.max(...buckets.map((bucket) => bucket[1]), 1);
    chart.innerHTML = buckets.map(([, count]) => (
      `<div class="mini-chart-bar" style="height:${Math.max(5, (count / maxCount) * 76)}px"></div>`
    )).join('');
  }
  const p95 = sketch.quantile(0.95);
  const note = $('sketch-accuracy');
  if (note) {
    note.textContent = p95 == null
      ? 'Add positive values to calculate relative-error bounds.'
      : `Max relative error +/-${(sketch.alpha * 100).toFixed(1)}%; p95 range ${formatNumber(p95 * (1 - sketch.alpha))} to ${formatNumber(p95 * (1 + sketch.alpha))} ms.`;
  }
}

if ($('sketch-alpha')) {
  $('sketch-alpha').addEventListener('input', (event) => {
    const alpha = Number.parseFloat(event.target.value);
    setText('sketch-alpha-val', alpha.toFixed(3));
    sketch = new DDSketch(alpha);
    updateSketchStats();
  });
}

if ($('sketch-add-btn')) {
  $('sketch-add-btn').addEventListener('click', () => {
    const values = $('sketch-input').value
      .split(/[,\n\s]+/)
      .map((value) => Number.parseFloat(value.trim()))
      .filter((value) => Number.isFinite(value) && value > 0);
    if (values.length === 0) {
      setText('sketch-accuracy', 'No valid positive numbers found.');
      return;
    }
    values.forEach((value) => sketch.add(value));
    updateSketchStats();
  });
}

if ($('sketch-reset-btn')) {
  $('sketch-reset-btn').addEventListener('click', () => {
    sketch.reset();
    updateSketchStats();
  });
}

const streamStore = new Map();
let writtenCount = 0;
let readCount = 0;

function logLine(text, className = '') {
  const box = $('stream-log');
  if (!box) return;
  const line = document.createElement('div');
  line.className = `log-line${className ? ` ${className}` : ''}`;
  line.textContent = `[${new Date().toISOString().slice(11, 23)}] ${text}`;
  box.appendChild(line);
  box.scrollTop = box.scrollHeight;
}

if ($('stream-write-btn')) {
  $('stream-write-btn').addEventListener('click', () => {
    const path = $('stream-path').value.trim();
    if (!path) {
      logLine('ERROR empty stream path', 'error');
      return;
    }
    const lines = $('stream-input').value.trim().split('\n').filter(Boolean);
    if (!streamStore.has(path)) streamStore.set(path, []);
    let ok = 0;
    lines.forEach((line) => {
      try {
        const record = JSON.parse(line);
        streamStore.get(path).push({ ...record, _ingested_at: Date.now() });
        logLine(`WRITE ${path} -> ${JSON.stringify(record)}`, 'write');
        ok += 1;
      } catch {
        logLine(`ERROR invalid JSON: ${line}`, 'error');
      }
    });
    writtenCount += ok;
    setText('s-written', String(writtenCount));
  });
}

if ($('stream-read-btn')) {
  $('stream-read-btn').addEventListener('click', () => {
    const path = $('stream-path').value.trim();
    const records = streamStore.get(path) || [];
    if (records.length === 0) {
      logLine(`READ ${path || '(empty)'} -> no records yet`, 'read');
      return;
    }
    records.forEach((record, index) => {
      logLine(`READ ${path}[${index}] -> ${JSON.stringify(record)}`, 'read');
      readCount += 1;
    });
    setText('s-read', String(readCount));
  });
}

if ($('stream-reset-btn')) {
  $('stream-reset-btn').addEventListener('click', () => {
    streamStore.clear();
    writtenCount = 0;
    readCount = 0;
    setText('s-written', '0');
    setText('s-read', '0');
    $('stream-log').innerHTML = '';
    logLine('Stream store cleared.');
  });
}

const SQL_EXAMPLES = {
  service: `SELECT service, p99(latency_ms), count(), approx_count_distinct(user_id)
FROM checkout_latency
WHERE namespace = 'production'
GROUP BY service
ORDER BY p99(latency_ms) DESC`,
  tenant: `SELECT tenant, p95(latency_ms), approx_count_distinct(session_id)
FROM api_latency
WHERE namespace = 'checkout'
GROUP BY tenant
ORDER BY p95(latency_ms) DESC`,
  errors: `SELECT error_type, count_min_count(error_type) AS estimated_events
FROM error_events
WHERE namespace = 'production'
GROUP BY error_type
ORDER BY estimated_events DESC
LIMIT 5`,
  canary: `SELECT build, p99(latency_ms), error_rate(), slo_burn_rate()
FROM checkout_latency
WHERE namespace = 'production'
  AND build IN ('stable', 'canary')
GROUP BY build`,
};

const SQL_RESULTS = {
  service: {
    headers: ['service', 'p99_latency_ms', 'events', 'unique_users'],
    rows: [['checkout', 930, 184200, 18420], ['payments', 640, 71200, 6330], ['catalog', 210, 96100, 14220]],
  },
  tenant: {
    headers: ['tenant', 'p95_latency_ms', 'sessions'],
    rows: [['acme', 188, 12400], ['beta', 142, 6880], ['orbit', 116, 4810]],
  },
  errors: {
    headers: ['error_type', 'estimated_events'],
    rows: [['checkout.timeout', 1280], ['http.503', 940], ['payment.retry', 680], ['cart.conflict', 420], ['auth.expired', 310]],
  },
  canary: {
    headers: ['build', 'p99_latency_ms', 'error_rate', 'slo_burn_rate'],
    rows: [['stable', 188, '0.18%', '0.7x'], ['canary', 610, '1.92%', '2.1x']],
  },
};

let activeQuery = 'service';

function setSqlExample(key) {
  activeQuery = key;
  document.querySelectorAll('[data-query]').forEach((button) => {
    button.classList.toggle('active', button.dataset.query === key);
  });
  if ($('sql-query')) $('sql-query').value = SQL_EXAMPLES[key];
  renderSqlResults(key);
}

function renderSqlResults(key) {
  const result = SQL_RESULTS[key] || SQL_RESULTS.service;
  const html = `<table>
    <thead><tr>${result.headers.map((header) => `<th>${escapeHtml(header)}</th>`).join('')}</tr></thead>
    <tbody>${result.rows.map((row) => `<tr>${row.map((cell) => `<td>${escapeHtml(cell)}</td>`).join('')}</tr>`).join('')}</tbody>
  </table>`;
  if ($('sql-results')) $('sql-results').innerHTML = html;
}

document.querySelectorAll('[data-query]').forEach((button) => {
  button.addEventListener('click', () => setSqlExample(button.dataset.query));
});

if ($('sql-run-btn')) {
  $('sql-run-btn').addEventListener('click', () => {
    renderSqlResults(activeQuery);
    setText('sql-copy-status', 'Sample query executed in browser');
    window.setTimeout(() => setText('sql-copy-status', ''), 1800);
  });
}

if ($('sql-copy-btn')) {
  $('sql-copy-btn').addEventListener('click', () => copyText($('sql-query').value, 'sql-copy-status'));
}

function previewExport(type) {
  const now = Math.floor(Date.now() / 1000);
  const path = $('stream-path')?.value.trim() || 'production/checkout/latency';
  const metricName = path.replace(/[^a-zA-Z0-9_]+/g, '_');

  if (type === 'loki') {
    const url = $('loki-url').value;
    const job = $('loki-job').value;
    const payload = {
      streams: [{
        stream: { job, stream_path: path, namespace: path.split('/')[0] || 'production' },
        values: [
          [`${now}000000000`, `p50=42.1 p95=188.4 p99=610.2 count=184200 stream=${path}`],
          [`${now + 60}000000000`, `unique_users=18420 top_event=checkout.timeout anomaly=watch stream=${path}`],
        ],
      }],
    };
    $('loki-output').textContent = `POST ${url}/loki/api/v1/push\nContent-Type: application/json\n\n${JSON.stringify(payload, null, 2)}`;
  }

  if (type === 'datadog') {
    const site = $('dd-site').value;
    const prefix = $('dd-prefix').value;
    const payload = {
      series: [
        { metric: `${prefix}${metricName}.p95`, type: 0, points: [[now, 188.4]], tags: [`stream:${path}`, 'backend:browser-demo'] },
        { metric: `${prefix}${metricName}.p99`, type: 0, points: [[now, 610.2]], tags: [`stream:${path}`, 'backend:browser-demo'] },
        { metric: `${prefix}${metricName}.unique_users`, type: 0, points: [[now, 18420]], tags: [`stream:${path}`] },
        { metric: `${prefix}${metricName}.slo_burn`, type: 0, points: [[now, 2.1]], tags: [`stream:${path}`] },
      ],
    };
    $('dd-output').textContent = `POST https://api.${site}/api/v2/series\nDD-API-KEY: <your-key>\n\n${JSON.stringify(payload, null, 2)}`;
  }

  if (type === 'newrelic') {
    const region = $('nr-region').value;
    const eventType = $('nr-type').value;
    const endpoint = region === 'EU'
      ? 'https://insights-collector.eu01.nr-data.net/v1/accounts/{ACCOUNT_ID}/events'
      : 'https://insights-collector.newrelic.com/v1/accounts/{ACCOUNT_ID}/events';
    const payload = [{
      eventType,
      stream_path: path,
      p50: 42.1,
      p95: 188.4,
      p99: 610.2,
      unique_users: 18420,
      anomaly: 'watch',
      slo_burn_rate: 2.1,
      timestamp: now,
    }];
    $('nr-output').textContent = `POST ${endpoint}\nX-Insert-Key: <your-key>\n\n${JSON.stringify(payload, null, 2)}`;
  }
}

document.querySelectorAll('.export-preview-btn').forEach((button) => {
  button.addEventListener('click', () => previewExport(button.dataset.export));
});

document.querySelectorAll('.tab-btn').forEach((button) => {
  button.addEventListener('click', () => {
    document.querySelectorAll('.tab-btn').forEach((item) => item.classList.remove('active'));
    document.querySelectorAll('.tab-panel').forEach((panel) => panel.classList.remove('active'));
    button.classList.add('active');
    $(button.dataset.target).classList.add('active');
  });
});

const SNIPPETS = {
  basic: `from sketchlog import SketchLog

sl = SketchLog({"server": "http://localhost:7654"})

for latency_ms in [12, 25, 50, 100, 200, 500]:
    sl.write("production/checkout/latency", latency_ms)

p95 = sl.query("production/checkout/latency", quantile=0.95)
print(f"p95 = {p95:.1f} ms")`,

  stream: `from sketchlog import SketchLog

sl = SketchLog({"server": "http://localhost:7654"})

sl.write_batch("production/errors/checkout", [
    {"value": 1, "error_type": "timeout", "tenant": "acme"},
    {"value": 1, "error_type": "http.503", "tenant": "beta"},
])

records = sl.read("production/errors/checkout", limit=100)
for record in records:
    print(record)`,

  agent: `{
  "server": "http://localhost:7654",
  "namespace": "production",
  "scrape_interval": 15,
  "targets": [
    {
      "url": "http://app:9090/metrics",
      "mappings": [
        {
          "metric": "http_request_duration_seconds",
          "stream": "checkout/latency",
          "type": "latency"
        }
      ]
    }
  ]
}

sketchlog-agent --config sketchlog-agent.json`,

  loki: `from sketchlog.exporters import LokiConfig, LokiExporter, LokiStream

cfg = LokiConfig(url="http://loki:3100")

with LokiExporter(cfg) as exporter:
    exporter.push_stream(LokiStream(
        labels={"job": "sketchlog", "stream": "production/checkout/latency"},
        lines=["p50=42.1 p95=188.4 p99=610.2 count=184200"],
    ))`,
};

function setSnippet(key) {
  document.querySelectorAll('[data-snippet]').forEach((button) => {
    button.classList.toggle('active', button.dataset.snippet === key);
  });
  setText('snippet-code', SNIPPETS[key]);
}

document.querySelectorAll('[data-snippet]').forEach((button) => {
  button.addEventListener('click', () => setSnippet(button.dataset.snippet));
});

renderTour();
updateDashboard();
if ($('sketch-add-btn')) $('sketch-add-btn').click();
setSqlExample('service');
setSnippet('basic');
