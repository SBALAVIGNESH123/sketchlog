#pragma once

#include <cstddef>
#include <cstdint>
#include <vector>
#include <algorithm>
#include <cassert>
#include <cstring>
#include <limits>
#include <stdexcept>

namespace sketchlog {

/// Count-Min Sketch: probabilistic frequency estimation for streaming data.
/// Uses `depth` independent hash functions over `width` buckets each.
/// Point-query returns an *overestimate* of the true frequency (never under).
class CountMinSketch {
public:
    /// @param width  Number of buckets per hash row (more = lower error).
    /// @param depth  Number of independent hash rows   (more = higher confidence).
    explicit CountMinSketch(size_t width = 2048, size_t depth = 5);

    // ── Insertion ──────────────────────────────────────────────────────
    void add(const void* data, size_t len, int64_t count = 1);
    void add(uint64_t key, int64_t count = 1);
    void add_string(const char* str, size_t len, int64_t count = 1);

    // ── Point-query ────────────────────────────────────────────────────
    [[nodiscard]] int64_t estimate(const void* data, size_t len) const;
    [[nodiscard]] int64_t estimate(uint64_t key) const;
    [[nodiscard]] int64_t estimate_string(const char* str, size_t len) const;

    // ── Accessors / utilities ──────────────────────────────────────────
    [[nodiscard]] int64_t total_count() const noexcept { return total_count_; }
    [[nodiscard]] size_t width()       const noexcept { return width_; }
    [[nodiscard]] size_t depth()       const noexcept { return depth_; }
    [[nodiscard]] size_t memory_bytes() const noexcept;

    /// Element-wise merge (other must have identical width and depth).
    void merge(const CountMinSketch& other);

    /// Zero-out all counters.
    void reset() noexcept;

    /// State structure for serialization
    struct State {
        size_t width;
        size_t depth;
        int64_t total_count;
        std::vector<int64_t> table;
    };

    /// Get current state for serialization
    [[nodiscard]] State get_state() const;

    /// Restore state from a serialization payload
    void set_state(const State& state);

private:
    size_t width_;
    size_t depth_;
    int64_t total_count_ = 0;

    std::vector<int64_t>  table_;       // flat [depth_ × width_]
    std::vector<uint64_t> hash_seeds_;  // one per row

    // ── Internal helpers ───────────────────────────────────────────────
    static uint64_t murmur_finalizer(uint64_t h) noexcept;
    static uint64_t fnv1a(const void* data, size_t len) noexcept;
    static uint64_t splitmix64(uint64_t& state) noexcept;

    void init_seeds();
};


// --- Implementation ---

// ════════════════════════════════════════════════════════════════════════
// Construction / reset
// ════════════════════════════════════════════════════════════════════════

inline CountMinSketch::CountMinSketch(size_t width, size_t depth)
    : width_(width), depth_(depth)
{
    if (width_ == 0 || depth_ == 0)
        throw std::invalid_argument("CountMinSketch: width and depth must be > 0");

    if (width_ > std::numeric_limits<size_t>::max() / depth_)
        throw std::invalid_argument("CountMinSketch: width * depth overflows size_t");

    table_.resize(width_ * depth_, 0);
    init_seeds();
}

inline void CountMinSketch::reset() noexcept {
    std::fill(table_.begin(), table_.end(), int64_t{0});
    total_count_ = 0;
}

CountMinSketch::State CountMinSketch::get_state() const {
    State s;
    s.width = width_;
    s.depth = depth_;
    s.total_count = total_count_;
    s.table = table_;
    return s;
}

inline void CountMinSketch::set_state(const State& s) {
    if (s.width != width_ || s.depth != depth_) {
        throw std::invalid_argument("Cannot restore state with mismatched CMS dimensions");
    }
    if (s.table.size() != table_.size()) {
        throw std::invalid_argument("Cannot restore state with mismatched table size");
    }
    for (int64_t count : s.table) {
        if (count < 0) {
            throw std::invalid_argument("Cannot restore state with negative counts in CMS table");
        }
    }
    total_count_ = s.total_count;
    table_ = s.table;
}

// ════════════════════════════════════════════════════════════════════════
// Seed initialisation — deterministic splitmix64 from a fixed seed
// ════════════════════════════════════════════════════════════════════════

inline uint64_t CountMinSketch::splitmix64(uint64_t& state) noexcept {
    state += 0x9e3779b97f4a7c15ULL;
    uint64_t z = state;
    z = (z ^ (z >> 30)) * 0xbf58476d1ce4e5b9ULL;
    z = (z ^ (z >> 27)) * 0x94d049bb133111ebULL;
    return z ^ (z >> 31);
}

inline void CountMinSketch::init_seeds() {
    hash_seeds_.resize(depth_);
    uint64_t prng_state = 42;
    for (size_t i = 0; i < depth_; ++i)
        hash_seeds_[i] = splitmix64(prng_state);
}

// ════════════════════════════════════════════════════════════════════════
// Hash helpers
// ════════════════════════════════════════════════════════════════════════

inline uint64_t CountMinSketch::murmur_finalizer(uint64_t h) noexcept {
    h ^= h >> 33;
    h *= 0xff51afd7ed558ccdULL;
    h ^= h >> 33;
    h *= 0xc4ceb9fe1a85ec53ULL;
    h ^= h >> 33;
    return h;
}

inline uint64_t CountMinSketch::fnv1a(const void* data, size_t len) noexcept {
    const auto* bytes = static_cast<const uint8_t*>(data);
    uint64_t hash = 0xcbf29ce484222325ULL; // FNV offset basis
    for (size_t i = 0; i < len; ++i) {
        hash ^= static_cast<uint64_t>(bytes[i]);
        hash *= 0x100000001b3ULL;            // FNV prime
    }
    return hash;
}

// ════════════════════════════════════════════════════════════════════════
// Insertion
// ════════════════════════════════════════════════════════════════════════

inline void CountMinSketch::add(uint64_t key, int64_t count) {
    if (count <= 0) {
        throw std::invalid_argument("Event count must be strictly positive");
    }
    if (std::numeric_limits<int64_t>::max() - total_count_ < count) {
        throw std::overflow_error("CountMinSketch: total_count overflow");
    }

    size_t local_idx[64];
    std::vector<size_t> dyn_idx;
    size_t* idx_ptr = local_idx;

    if (depth_ > 64) {
        dyn_idx.resize(depth_);
        idx_ptr = dyn_idx.data();
    }

    for (size_t row = 0; row < depth_; ++row) {
        uint64_t col = murmur_finalizer(key ^ hash_seeds_[row]) % width_;
        idx_ptr[row] = row * width_ + col;
        if (std::numeric_limits<int64_t>::max() - table_[idx_ptr[row]] < count) {
            throw std::overflow_error("CountMinSketch: bucket counter overflow");
        }
    }

    for (size_t row = 0; row < depth_; ++row) {
        table_[idx_ptr[row]] += count;
    }
    total_count_ += count;
}

inline void CountMinSketch::add(const void* data, size_t len, int64_t count) {
    add(fnv1a(data, len), count);
}

inline void CountMinSketch::add_string(const char* str, size_t len, int64_t count) {
    add(static_cast<const void*>(str), len, count);
}

// ════════════════════════════════════════════════════════════════════════
// Point-query (minimum across all rows)
// ════════════════════════════════════════════════════════════════════════

inline int64_t CountMinSketch::estimate(uint64_t key) const {
    int64_t min_val = std::numeric_limits<int64_t>::max();
    for (size_t row = 0; row < depth_; ++row) {
        uint64_t col = murmur_finalizer(key ^ hash_seeds_[row]) % width_;
        min_val = std::min(min_val, table_[row * width_ + col]);
    }
    return min_val;
}

inline int64_t CountMinSketch::estimate(const void* data, size_t len) const {
    return estimate(fnv1a(data, len));
}

inline int64_t CountMinSketch::estimate_string(const char* str, size_t len) const {
    return estimate(static_cast<const void*>(str), len);
}

// ════════════════════════════════════════════════════════════════════════
// Memory accounting
// ════════════════════════════════════════════════════════════════════════

inline size_t CountMinSketch::memory_bytes() const noexcept {
    return sizeof(*this)
         + table_.capacity()      * sizeof(int64_t)
         + hash_seeds_.capacity() * sizeof(uint64_t);
}

// ════════════════════════════════════════════════════════════════════════
// Merge
// ════════════════════════════════════════════════════════════════════════

inline void CountMinSketch::merge(const CountMinSketch& other) {
    if (width_ != other.width_ || depth_ != other.depth_)
        throw std::invalid_argument(
            "CountMinSketch::merge: width and depth must match");

    if (std::numeric_limits<int64_t>::max() - total_count_ < other.total_count_) {
        throw std::overflow_error("CountMinSketch: total_count overflow during merge");
    }

    for (size_t i = 0, n = table_.size(); i < n; ++i) {
        if (std::numeric_limits<int64_t>::max() - table_[i] < other.table_[i]) {
            throw std::overflow_error("CountMinSketch: bucket counter overflow during merge");
        }
    }

    for (size_t i = 0, n = table_.size(); i < n; ++i)
        table_[i] += other.table_[i];

    total_count_ += other.total_count_;
}


} // namespace sketchlog
