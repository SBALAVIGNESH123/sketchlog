#include "sketchlog.hpp"
#include <stdexcept>
#include <limits>
#include <cmath>
#include <utility>

namespace sketchlog {

StreamLog::StreamLog(double relative_accuracy, uint8_t hll_precision,
                     size_t cms_width, size_t cms_depth)
    : latency_(relative_accuracy),
      events_(cms_width, cms_depth),
      uniques_(hll_precision),
      total_events_(0) {
}

// ─── Latency ────────────────────────────────────────────────────────────────

void StreamLog::add_latency(double value) {
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

double StreamLog::percentile(double q) const {
    return latency_.quantile(q);
}

double StreamLog::latency_min() const {
    return latency_.min();
}

double StreamLog::latency_max() const {
    return latency_.max();
}

// ─── Events ─────────────────────────────────────────────────────────────────

void StreamLog::add_event(const std::string& name, int64_t count) {
    if (count <= 0) {
        throw std::invalid_argument("Event count must be strictly positive");
    }
    if (std::numeric_limits<uint64_t>::max() - total_events_ < static_cast<uint64_t>(count)) {
        throw std::overflow_error("StreamLog: total_events overflow");
    }
    events_.add_string(name.c_str(), name.size(), count);
    total_events_ += count;
}

int64_t StreamLog::event_count(const std::string& name) const {
    return events_.estimate_string(name.c_str(), name.size());
}

// ─── Cardinality ────────────────────────────────────────────────────────────

void StreamLog::add_unique(const std::string& item) {
    uniques_.add_string(item.c_str(), item.size());
}

void StreamLog::add_unique(uint64_t id) {
    // HLL's add(uint64_t) expects a pre-hashed value.
    // Hash the raw ID bytes so users can pass plain IDs.
    uniques_.add(&id, sizeof(id));
}

uint64_t StreamLog::unique_count() const {
    double est = uniques_.estimate();
    return (est < 0.0) ? 0 : static_cast<uint64_t>(est + 0.5);
}

// ─── System ─────────────────────────────────────────────────────────────────

size_t StreamLog::memory_bytes() const {
    return latency_.memory_bytes() + events_.memory_bytes() + uniques_.memory_bytes();
}

double StreamLog::memory_kb() const {
    return static_cast<double>(memory_bytes()) / 1024.0;
}

Stats StreamLog::stats() const {
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

void StreamLog::reset() {
    latency_.reset();
    events_.reset();
    uniques_.reset();
    total_events_ = 0;
}

void StreamLog::merge(const StreamLog& other) {
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
