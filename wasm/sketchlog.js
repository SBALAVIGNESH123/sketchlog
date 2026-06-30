/**
 * SketchLog WASM Wrapper
 * Provides an ergonomic, asynchronous API for the WASM SketchLog core.
 */

let SketchLogModuleFactory;
if (typeof require !== 'undefined' && typeof module !== 'undefined' && module.exports) {
    SketchLogModuleFactory = require('./dist/sketchlog.js');
} else if (typeof globalThis !== 'undefined' && globalThis.SketchLogModule) {
    SketchLogModuleFactory = globalThis.SketchLogModule;
} else if (typeof window !== 'undefined' && window.SketchLogModule) {
    SketchLogModuleFactory = window.SketchLogModule;
}

let wasmModule = null;
let initPromise = null;
const MAX_INT64 = (1n << 63n) - 1n;
const MAX_UINT64 = (1n << 64n) - 1n;

function toBoundedBigInt(value, maximum, label, strictlyPositive = false) {
    let normalized;
    if (typeof value === 'bigint') {
        normalized = value;
    } else if (typeof value === 'number' && Number.isSafeInteger(value)) {
        normalized = BigInt(value);
    } else {
        throw new TypeError(`${label} must be a safe integer or bigint`);
    }
    if ((strictlyPositive ? normalized <= 0n : normalized < 0n) || normalized > maximum) {
        throw new RangeError(`${label} is outside its supported integer range`);
    }
    return normalized;
}

// Ensure WASM module is loaded only once
async function getModule(options = {}) {
    if (!SketchLogModuleFactory) {
        throw new Error("SketchLogModule factory not found. Make sure dist/sketchlog.js is loaded.");
    }
    if (!initPromise) {
        initPromise = SketchLogModuleFactory(options);
    }
    if (!wasmModule) {
        wasmModule = await initPromise;
    }
    return wasmModule;
}

class StreamLog {
    constructor(relativeAccuracy = 0.01, hllPrecision = 10, cmsWidth = 2048, cmsDepth = 5) {
        if (!wasmModule) {
            throw new Error("WASM module not initialized. Call await StreamLog.init() first.");
        }
        this._internal = new wasmModule.StreamLog(relativeAccuracy, hllPrecision, cmsWidth, cmsDepth);
    }

    static async init(options = {}) {
        await getModule(options);
    }

    addLatency(value) {
        this._internal.add_latency(value);
    }

    addBatch(values) {
        this._internal.add_batch(values);
    }

    percentile(q) {
        return this._internal.percentile(q);
    }

    get p50() { return this._internal.p50(); }
    get p95() { return this._internal.p95(); }
    get p99() { return this._internal.p99(); }
    get p999() { return this._internal.p999(); }

    countGreaterThan(threshold) {
        return this._internal.count_greater_than(threshold);
    }

    get latencyCount() {
        return this._internal.latency_count();
    }

    addEvent(name, count = 1) {
        this._internal.add_event(
            name, toBoundedBigInt(count, MAX_INT64, "count", true));
    }

    eventCount(name) {
        return this._internal.event_count(name);
    }

    addUnique(item) {
        if (typeof item === 'string') {
            this._internal.add_unique_string(item);
        } else if (typeof item === 'number' || typeof item === 'bigint') {
            this._internal.add_unique_int(
                toBoundedBigInt(item, MAX_UINT64, "item"));
        } else {
            throw new Error("Item must be string or number");
        }
    }

    get uniqueCount() {
        return this._internal.unique_count();
    }

    get totalEvents() {
        return this._internal.total_events();
    }

    get memoryBytes() {
        return this._internal.memory_bytes();
    }

    get memoryKb() {
        return this._internal.memory_kb();
    }

    reset() {
        this._internal.reset();
    }

    merge(other) {
        if (!(other instanceof StreamLog)) {
            throw new Error("other must be a StreamLog instance");
        }
        this._internal.merge(other._internal);
    }

    toDict() {
        return this._internal.to_dict();
    }

    serialize() {
        return { state: this.toDict() };
    }

    stats() {
        return this._internal.stats();
    }

    destroy() {
        if (this._internal) {
            this._internal.delete();
            this._internal = null;
        }
    }
}

if (typeof module !== 'undefined' && module.exports) {
    module.exports = { StreamLog };
} else if (typeof globalThis !== 'undefined') {
    globalThis.StreamLog = StreamLog;
} else if (typeof window !== 'undefined') {
    window.StreamLog = StreamLog;
}
