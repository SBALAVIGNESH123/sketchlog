#ifndef SKETCHLOG_HPP
#define SKETCHLOG_HPP

#include "ddsketch.hpp"
#include "hyperloglog.hpp"
#include "countmin.hpp"

#include <string>
#include <cstdint>
#include <cstddef>
#include <stdexcept>
#include <limits>
#include <cmath>
#include <utility>

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
    void add_event(uint64_t key, int64_t count = 1);

    /** Estimated count for an event. */
    int64_t event_count(const std::string& name) const;
    int64_t event_count(uint64_t key) const;

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


// --- Implementation ---

inline StreamLog::StreamLog(double relative_accuracy, uint8_t hll_precision,
                     size_t cms_width, size_t cms_depth)
    : latency_(relative_accuracy),
      events_(cms_width, cms_depth),
      uniques_(hll_precision),
      total_events_(0) {
}

// ─── Latency ────────────────────────────────────────────────────────────────

inline void StreamLog::add_latency(double value) {
    if (!std::isfinite(value)) {
        latency_.add(value);
        return;
    }
    if (std::numeric_limits<uint64_t>::max() - total_events_ < 1) {
        throw std::overflow_error("StreamLog: total_events overflow");
    }
    latency_.add(value);
    total_events_++;
}

inline void StreamLog::add_batch(const double* values, size_t size) {
    size_t valid_count = 0;
    for (size_t i = 0; i < size; ++i) {
        if (std::isfinite(values[i])) valid_count++;
    }

    if (std::numeric_limits<uint64_t>::max() - total_events_ < valid_count) {
        throw std::overflow_error("StreamLog: total_events overflow");
    }

    latency_.add_batch(values, size);
    total_events_ += valid_count;
}

inline void StreamLog::add_batch(const std::vector<double>& values) {
    add_batch(values.data(), values.size());
}

inline double StreamLog::percentile(double q) const {
    return latency_.quantile(q);
}

inline uint64_t StreamLog::count_greater_than(double threshold) const {
    return latency_.count_greater_than(threshold);
}

inline double StreamLog::latency_min() const {
    return latency_.min();
}

inline double StreamLog::latency_max() const {
    return latency_.max();
}

// ─── Events ─────────────────────────────────────────────────────────────────

inline void StreamLog::add_event(const std::string& name, int64_t count) {
    if (count <= 0) {
        throw std::invalid_argument("Event count must be strictly positive");
    }
    if (std::numeric_limits<uint64_t>::max() - total_events_ < static_cast<uint64_t>(count)) {
        throw std::overflow_error("StreamLog: total_events overflow");
    }
    events_.add_string(name.c_str(), name.size(), count);
    total_events_ += count;
}

inline void StreamLog::add_event(uint64_t key, int64_t count) {
    if (count <= 0) {
        throw std::invalid_argument("Event count must be strictly positive");
    }
    if (std::numeric_limits<uint64_t>::max() - total_events_ < static_cast<uint64_t>(count)) {
        throw std::overflow_error("StreamLog: total_events overflow");
    }
    events_.add(key, count);
    total_events_ += count;
}

inline int64_t StreamLog::event_count(const std::string& name) const {
    return events_.estimate_string(name.c_str(), name.size());
}

inline int64_t StreamLog::event_count(uint64_t key) const {
    return events_.estimate(key);
}

// ─── Cardinality ────────────────────────────────────────────────────────────

inline void StreamLog::add_unique(const std::string& item) {
    uniques_.add_string(item.c_str(), item.size());
}

inline void StreamLog::add_unique(uint64_t id) {
    // HLL's add(uint64_t) expects a pre-hashed value.
    // Hash the raw ID bytes so users can pass plain IDs.
    uniques_.add(&id, sizeof(id));
}

inline uint64_t StreamLog::unique_count() const {
    double est = uniques_.estimate();
    return (est < 0.0) ? 0 : static_cast<uint64_t>(est + 0.5);
}

// ─── System ─────────────────────────────────────────────────────────────────

inline size_t StreamLog::memory_bytes() const {
    return latency_.memory_bytes() + events_.memory_bytes() + uniques_.memory_bytes();
}

inline double StreamLog::memory_kb() const {
    return static_cast<double>(memory_bytes()) / 1024.0;
}

inline Stats StreamLog::stats() const {
    Stats s;
    s.events = total_events_;
    s.memory_bytes = memory_bytes();
    s.memory_kb = memory_kb();
    s.latency_p50 = (latency_.count() > 0) ? p50() : 0.0;
    s.latency_p99 = (latency_.count() > 0) ? p99() : 0.0;
    s.latency_p999 = (latency_.count() > 0) ? p999() : 0.0;
    s.unique_count = unique_count();
    return s;
}

inline void StreamLog::reset() {
    latency_.reset();
    events_.reset();
    uniques_.reset();
    total_events_ = 0;
}

inline void StreamLog::merge(const StreamLog& other) {
    if (std::numeric_limits<uint64_t>::max() - total_events_ < other.total_events_) {
        throw std::overflow_error("StreamLog: total_events overflow during merge");
    }

    // Merge into copies to ensure atomic commit (CountMinSketch can throw overflow)
    DDSketch new_latency = latency_;
    CountMinSketch new_events = events_;
    HyperLogLog new_uniques = uniques_;

    new_latency.merge(other.latency_);
    new_events.merge(other.events_);
    new_uniques.merge(other.uniques_);

    // Commit state if no exceptions were thrown
    latency_ = std::move(new_latency);
    events_ = std::move(new_events);
    uniques_ = std::move(new_uniques);
    total_events_ += other.total_events_;
}


} // namespace sketchlog

#endif // SKETCHLOG_HPP
