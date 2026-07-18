/* SketchLog hosted cost and footprint estimator */
'use strict';

(function exposeCostEstimator(root) {
  const BYTES_PER_SKETCH_BUCKET = 16;
  const SKETCH_FIXED_OVERHEAD_BYTES = 128;
  const HOURLY_WINDOWS_PER_DAY = 24;
  const LATENCY_STREAM_FRACTION = 0.6;
  const COUNTER_STREAM_BYTES_PER_DAY = 64;

  const BACKEND_PROFILES = {
    memory: {
      label: 'In-memory',
      multiplier: 1.0,
      note: 'Volatile hot-path state for demos, tests, and short-lived evaluations.',
    },
    postgres: {
      label: 'PostgreSQL durable',
      multiplier: 1.25,
      note: 'Adds planning headroom for rows, indexes, WAL, and SQL metadata.',
    },
    omnikv: {
      label: 'OmniKV embedded',
      multiplier: 1.15,
      note: 'Adds planning headroom for embedded key/value metadata and compaction.',
    },
  };

  function formatBytes(bytes) {
    if (!Number.isFinite(bytes)) return '-';
    let value = Number(bytes);
    const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB', 'PiB'];
    for (const unit of units) {
      if (Math.abs(value) < 1024) return `${value.toFixed(2)} ${unit}`;
      value /= 1024;
    }
    return `${value.toFixed(2)} EiB`;
  }

  function assertPositiveInteger(name, value) {
    if (!Number.isSafeInteger(value) || value < 1) {
      throw new Error(`${name} must be a positive safe integer`);
    }
  }

  function assertPositiveNumber(name, value) {
    if (!Number.isFinite(value) || value <= 0) {
      throw new Error(`${name} must be a positive number`);
    }
  }

  function assertSafeInteger(name, value) {
    if (!Number.isSafeInteger(value)) {
      throw new Error(`${name} must be a finite safe integer`);
    }
  }

  function roundPositiveHalfUp(name, value) {
    if (!Number.isFinite(value) || value < 0) {
      throw new Error(`${name} must be a finite non-negative number`);
    }
    const rounded = Math.floor(value + 0.5);
    assertSafeInteger(name, rounded);
    return rounded;
  }

  function validateInput(input) {
    assertPositiveInteger('eventsPerDay', input.eventsPerDay);
    assertPositiveInteger('avgEventBytes', input.avgEventBytes);
    assertPositiveInteger('retentionDays', input.retentionDays);
    assertPositiveInteger('streamCount', input.streamCount);
    assertPositiveInteger('namespaceCount', input.namespaceCount);
    assertPositiveNumber('rawCompressionRatio', input.rawCompressionRatio);
    if (input.rawCompressionRatio < 1) {
      throw new Error('rawCompressionRatio must be at least 1');
    }
    if (!Number.isFinite(input.sketchAccuracy)
        || input.sketchAccuracy <= 0
        || input.sketchAccuracy >= 1) {
      throw new Error('sketchAccuracy must be a finite number in (0, 1)');
    }
    if (!Object.prototype.hasOwnProperty.call(BACKEND_PROFILES, input.backend)) {
      throw new Error(`unknown backend: ${input.backend}`);
    }
  }

  function estimateSketchlogCost(input) {
    validateInput(input);
    const backend = BACKEND_PROFILES[input.backend];
    const rawTotalBytes = (
      input.eventsPerDay
      * input.avgEventBytes
      * input.retentionDays
    );
    assertSafeInteger('rawTotalBytes', rawTotalBytes);
    const compressedRawBytes = roundPositiveHalfUp(
      'compressedRawBytes',
      rawTotalBytes / input.rawCompressionRatio,
    );
    const sketchBucketsPerStream = Math.max(
      1,
      Math.ceil(2.0 / input.sketchAccuracy),
    );
    assertSafeInteger('sketchBucketsPerStream', sketchBucketsPerStream);
    const sketchBytesPerLatencyStreamPerDay = (
      (sketchBucketsPerStream * BYTES_PER_SKETCH_BUCKET
        + SKETCH_FIXED_OVERHEAD_BYTES)
      * HOURLY_WINDOWS_PER_DAY
    );
    assertSafeInteger(
      'sketchBytesPerLatencyStreamPerDay',
      sketchBytesPerLatencyStreamPerDay,
    );
    const latencyStreams = Math.max(
      1,
      Math.round(input.streamCount * LATENCY_STREAM_FRACTION),
    );
    const counterStreams = Math.max(0, input.streamCount - latencyStreams);
    assertSafeInteger('latencyStreams', latencyStreams);
    assertSafeInteger('counterStreams', counterStreams);
    const sketchTotalBytes = (
      (
        latencyStreams * sketchBytesPerLatencyStreamPerDay
        + counterStreams * COUNTER_STREAM_BYTES_PER_DAY
      )
      * input.namespaceCount
      * input.retentionDays
    );
    assertSafeInteger('sketchTotalBytes', sketchTotalBytes);
    const backendAdjustedBytes = roundPositiveHalfUp(
      'backendAdjustedBytes',
      sketchTotalBytes * backend.multiplier,
    );
    const savingsBytes = compressedRawBytes - backendAdjustedBytes;
    assertSafeInteger('savingsBytes', savingsBytes);
    const savingsPercent = compressedRawBytes > 0
      ? Math.round((savingsBytes / compressedRawBytes) * 10000) / 100
      : 0;
    const eventsPerSecond = input.eventsPerDay / 86400;
    const totalStreams = input.streamCount * input.namespaceCount;
    assertSafeInteger('totalStreams', totalStreams);
    const hotMemoryBytes = roundPositiveHalfUp(
      'hotMemoryBytes',
      input.namespaceCount * (
        latencyStreams * (sketchBucketsPerStream * 8 + 512)
        + counterStreams * 256
      ),
    );

    return {
      inputs: { ...input },
      backend: {
        id: input.backend,
        label: backend.label,
        multiplier: backend.multiplier,
        note: backend.note,
      },
      rawTelemetry: {
        uncompressedBytes: rawTotalBytes,
        compressedBytes: compressedRawBytes,
        humanUncompressed: formatBytes(rawTotalBytes),
        humanCompressed: formatBytes(compressedRawBytes),
      },
      sketchlogSummary: {
        compactBytes: sketchTotalBytes,
        backendAdjustedBytes,
        humanCompact: formatBytes(sketchTotalBytes),
        humanBackendAdjusted: formatBytes(backendAdjustedBytes),
        latencyStreams,
        counterStreams,
        totalStreams,
        sketchBucketsPerStream,
        sketchBytesPerLatencyStreamPerDay,
      },
      operationalFootprint: {
        eventsPerSecond,
        humanEventsPerSecond: `${eventsPerSecond.toFixed(1)} events/s`,
        hotMemoryBytes,
        humanHotMemory: formatBytes(hotMemoryBytes),
      },
      savings: {
        bytes: savingsBytes,
        human: formatBytes(Math.abs(savingsBytes)),
        percent: savingsPercent,
        isPositive: savingsBytes >= 0,
      },
      caveats: [
        'Estimates are planning numbers, not billing guarantees.',
        'Raw compression ratio is user-selected; real compression depends on event shape.',
        'The compact model mirrors the Python CLI: ceil(2 / epsilon), hourly windows, 60/40 latency/counter split.',
        'Backend totals add rough operational headroom for metadata and persistence overhead.',
        'Hot memory and persisted storage are different; validate real workloads with the proof commands.',
      ],
    };
  }

  function numberFromInput(id, parser = Number) {
    const node = root.document?.getElementById(id);
    if (!node) return null;
    return parser(node.value);
  }

  function setText(id, value) {
    const node = root.document?.getElementById(id);
    if (node) node.textContent = value;
  }

  function renderBreakdown(result) {
    const target = root.document?.getElementById('cost-breakdown');
    if (!target) return;
    target.innerHTML = `
      <table>
        <tbody>
          <tr><th scope="row">Events per second</th><td>${result.operationalFootprint.humanEventsPerSecond}</td></tr>
          <tr><th scope="row">Total streams</th><td>${result.sketchlogSummary.totalStreams.toLocaleString()}</td></tr>
          <tr><th scope="row">Latency streams / namespace</th><td>${result.sketchlogSummary.latencyStreams.toLocaleString()}</td></tr>
          <tr><th scope="row">Counter streams / namespace</th><td>${result.sketchlogSummary.counterStreams.toLocaleString()}</td></tr>
          <tr><th scope="row">DDSketch buckets / stream</th><td>${result.sketchlogSummary.sketchBucketsPerStream.toLocaleString()}</td></tr>
          <tr><th scope="row">Latency stream bytes / day</th><td>${formatBytes(result.sketchlogSummary.sketchBytesPerLatencyStreamPerDay)}</td></tr>
          <tr><th scope="row">Backend model</th><td>${result.backend.label} (${result.backend.multiplier.toFixed(2)}x planning headroom)</td></tr>
        </tbody>
      </table>`;
  }

  function renderCaveats(result) {
    const target = root.document?.getElementById('cost-caveats');
    if (!target) return;
    target.innerHTML = result.caveats.map((caveat) => `<li>${caveat}</li>`).join('');
  }

  function updateSavingsCardStatus(result) {
    const value = root.document?.getElementById('cost-savings-percent');
    const card = value?.closest('.cost-result-card');
    if (!card) return;
    card.classList.remove('ok', 'warn', 'danger');
    card.classList.add(result.savings.isPositive ? 'ok' : 'warn');
  }

  function readEstimatorInput() {
    return {
      eventsPerDay: numberFromInput('cost-events-per-day', Number.parseInt),
      avgEventBytes: numberFromInput('cost-event-bytes', Number.parseInt),
      retentionDays: numberFromInput('cost-retention-days', Number.parseInt),
      sketchAccuracy: numberFromInput('cost-accuracy', Number),
      streamCount: numberFromInput('cost-streams', Number.parseInt),
      namespaceCount: numberFromInput('cost-namespaces', Number.parseInt),
      rawCompressionRatio: numberFromInput('cost-compression', Number),
      backend: root.document?.getElementById('cost-backend')?.value || 'memory',
    };
  }

  function updateCostEstimator() {
    const error = root.document?.getElementById('cost-estimator-error');
    try {
      const result = estimateSketchlogCost(readEstimatorInput());
      if (error) error.textContent = '';
      updateSavingsCardStatus(result);
      setText('cost-raw-total', result.rawTelemetry.humanUncompressed);
      setText('cost-compressed-raw', result.rawTelemetry.humanCompressed);
      setText('cost-compact-total', result.sketchlogSummary.humanCompact);
      setText('cost-backend-total', result.sketchlogSummary.humanBackendAdjusted);
      setText('cost-hot-memory', result.operationalFootprint.humanHotMemory);
      setText(
        'cost-savings-percent',
        `${result.savings.isPositive ? '' : '-'}${Math.abs(result.savings.percent).toFixed(2)}%`,
      );
      setText(
        'cost-savings-detail',
        `${result.savings.isPositive ? 'Saved' : 'Extra'} ${result.savings.human} versus compressed raw telemetry`,
      );
      setText('cost-backend-note', result.backend.note);
      renderBreakdown(result);
      renderCaveats(result);
    } catch (err) {
      if (error) error.textContent = err instanceof Error ? err.message : String(err);
    }
  }

  function bindCostEstimator() {
    const form = root.document?.getElementById('cost-estimator-form');
    if (!form) return;
    form.addEventListener('submit', (event) => {
      event.preventDefault();
      updateCostEstimator();
    });
    form.querySelectorAll('input, select').forEach((node) => {
      node.addEventListener('input', updateCostEstimator);
      node.addEventListener('change', updateCostEstimator);
    });
    updateCostEstimator();
  }

  const api = {
    BACKEND_PROFILES,
    CONSTANTS: {
      BYTES_PER_SKETCH_BUCKET,
      SKETCH_FIXED_OVERHEAD_BYTES,
      HOURLY_WINDOWS_PER_DAY,
      LATENCY_STREAM_FRACTION,
      COUNTER_STREAM_BYTES_PER_DAY,
    },
    estimateSketchlogCost,
    formatBytes,
    bindCostEstimator,
  };

  root.SketchLogCostEstimator = api;
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = api;
  }

  bindCostEstimator();
}(typeof globalThis !== 'undefined' ? globalThis : window));
