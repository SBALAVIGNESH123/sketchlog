#ifndef SKETCHLOG_HPP
#define SKETCHLOG_HPP

#include "ddsketch.hpp"
#include "hyperloglog.hpp"
#include "countmin.hpp"

#include <string>
#include <cstdint>
#include <cstddef>

namespace sketchlog {

/**
 * @brief Stats snapshot of a StreamLog.
 */
struct Stats {
    uint64_t events;          // Total events processed
    size_t memory_bytes;      // Total memory across all sketches
    double memory_kb;         // Total memory in KB
    double latency_p50;       // Median latency (if tracked)
    double latency_p99;       // p99 latency (if tracked)
    double latency_p999;      // p99.9 latency (if tracked)
    uint64_t unique_count;    // Estimated unique items
};

/**
 * @brief StreamLog — Infinite-scale metrics in constant memory.
 *
 * Track latency percentiles, event frequency, and cardinality
 * over billions of events using ~5 KB of RAM.
 *
 * Usage:
 *   StreamLog log;
 *   for (auto& event : stream) {
 *       log.add_latency(event.duration_ms);
 *       log.add_event("api_call");
 *       log.add_unique("user_id", event.user_id);
 *   }
 *   double p99 = log.p99();
 *   uint64_t unique_users = log.unique_count();
 *   int64_t api_calls = log.event_count("api_call");
 */
class StreamLog {
public:
    /**
     * @brief Construct a StreamLog.
     * @param relative_accuracy DDSketch accuracy (default 0.01 = 1% relative error)
     * @param hll_precision HyperLogLog precision (default 10 = 1024 registers)
     * @param cms_width Count-Min Sketch width (default 2048)
     * @param cms_depth Count-Min Sketch depth (default 5)
     */
    StreamLog(double relative_accuracy = 0.01,
              uint8_t hll_precision = 10,
              size_t cms_width = 2048,
              size_t cms_depth = 5);

    // ─── Latency tracking (DDSketch) ─────────────────────────────────

    /** Add a latency measurement. */
    void add_latency(double value);

    /** Add a batch of latency measurements. */
    void add_batch(const std::vector<double>& values);
    void add_batch(const double* values, size_t size);

    /** Get any percentile (0.0 to 1.0). */
    double percentile(double q) const;

    /** Convenience: p50. */
    double p50() const { return percentile(0.50); }

    /** Convenience: p95. */
    double p95() const { return percentile(0.95); }

    /** Convenience: p99. */
    double p99() const { return percentile(0.99); }

    /** Convenience: p999. */
    double p999() const { return percentile(0.999); }

    /** Count latencies > threshold. */
    uint64_t count_greater_than(double threshold) const;

    /** Latency min/max. */
    double latency_min() const;
    double latency_max() const;

    // ─── Event frequency tracking (Count-Min Sketch) ─────────────────

    /** Record an event occurrence. */
    void add_event(const std::string& name, int64_t count = 1);

    /** Estimated count for an event. */
    int64_t event_count(const std::string& name) const;

    // ─── Cardinality tracking (HyperLogLog) ──────────────────────────

    /** Add a unique item (by string). */
    void add_unique(const std::string& item);

    /** Add a unique item (by uint64 id). */
    void add_unique(uint64_t id);

    /** Estimated number of unique items. */
    uint64_t unique_count() const;

    // ─── System ──────────────────────────────────────────────────────

    /** Total events processed. */
    uint64_t total_events() const { return total_events_; }

    /** Total latency events processed. */
    uint64_t latency_count() const { return latency_.count(); }

    /** Total memory used by all sketches (bytes). */
    size_t memory_bytes() const;

    /** Total memory in KB. */
    double memory_kb() const;

    /** Full stats snapshot. */
    Stats stats() const;

    /** Reset everything. */
    void reset();

    /** Merge another StreamLog into this one. */
    void merge(const StreamLog& other);

    // ─── State Serialization ─────────────────────────────────────────

    DDSketch::State get_latency_state() const { return latency_.get_state(); }
    void set_latency_state(const DDSketch::State& s) { latency_.set_state(s); }

    CountMinSketch::State get_events_state() const { return events_.get_state(); }
    void set_events_state(const CountMinSketch::State& s) { events_.set_state(s); }

    HyperLogLog::State get_uniques_state() const { return uniques_.get_state(); }
    void set_uniques_state(const HyperLogLog::State& s) { uniques_.set_state(s); }

    void set_total_events(uint64_t t) { total_events_ = t; }

    size_t latency_memory_bytes() const { return latency_.memory_bytes(); }
    size_t events_memory_bytes() const { return events_.memory_bytes(); }
    size_t uniques_memory_bytes() const { return uniques_.memory_bytes(); }

private:
    DDSketch latency_;
    CountMinSketch events_;
    HyperLogLog uniques_;
    uint64_t total_events_;
};

} // namespace sketchlog

#endif // SKETCHLOG_HPP
