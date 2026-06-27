/**
 * SketchLog WASM Wrapper
 * Provides an ergonomic, asynchronous API for the WASM SketchLog core.
 */

const SketchLogModule = require('./dist/sketchlog.js');

let wasmModule = null;

async function getModule() {
    if (!wasmModule) {
        wasmModule = await SketchLogModule();
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

    static async init() {
        await getModule();
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
        this._internal.add_event(name, count);
    }

    eventCount(name) {
        return this._internal.event_count(name);
    }

    addUnique(item) {
        if (typeof item === 'string') {
            this._internal.add_unique_string(item);
        } else if (typeof item === 'number') {
            this._internal.add_unique_int(item);
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

    stats() {
        return this._internal.stats();
    }

    destroy() {
        this._internal.delete();
    }
}

module.exports = {
    StreamLog
};
