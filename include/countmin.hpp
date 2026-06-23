#pragma once

#include <cstddef>
#include <cstdint>
#include <vector>

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

} // namespace sketchlog
