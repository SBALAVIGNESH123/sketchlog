#include "countmin.hpp"

#include <algorithm>
#include <cassert>
#include <cstring>
#include <limits>
#include <stdexcept>

namespace sketchlog {

// ════════════════════════════════════════════════════════════════════════
// Construction / reset
// ════════════════════════════════════════════════════════════════════════

CountMinSketch::CountMinSketch(size_t width, size_t depth)
    : width_(width), depth_(depth)
{
    if (width_ == 0 || depth_ == 0)
        throw std::invalid_argument("CountMinSketch: width and depth must be > 0");

    if (width_ > std::numeric_limits<size_t>::max() / depth_)
        throw std::invalid_argument("CountMinSketch: width * depth overflows size_t");

    table_.resize(width_ * depth_, 0);
    init_seeds();
}

void CountMinSketch::reset() noexcept {
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

void CountMinSketch::set_state(const State& s) {
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

uint64_t CountMinSketch::splitmix64(uint64_t& state) noexcept {
    state += 0x9e3779b97f4a7c15ULL;
    uint64_t z = state;
    z = (z ^ (z >> 30)) * 0xbf58476d1ce4e5b9ULL;
    z = (z ^ (z >> 27)) * 0x94d049bb133111ebULL;
    return z ^ (z >> 31);
}

void CountMinSketch::init_seeds() {
    hash_seeds_.resize(depth_);
    uint64_t prng_state = 42;
    for (size_t i = 0; i < depth_; ++i)
        hash_seeds_[i] = splitmix64(prng_state);
}

// ════════════════════════════════════════════════════════════════════════
// Hash helpers
// ════════════════════════════════════════════════════════════════════════

uint64_t CountMinSketch::murmur_finalizer(uint64_t h) noexcept {
    h ^= h >> 33;
    h *= 0xff51afd7ed558ccdULL;
    h ^= h >> 33;
    h *= 0xc4ceb9fe1a85ec53ULL;
    h ^= h >> 33;
    return h;
}

uint64_t CountMinSketch::fnv1a(const void* data, size_t len) noexcept {
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

void CountMinSketch::add(uint64_t key, int64_t count) {
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

void CountMinSketch::add(const void* data, size_t len, int64_t count) {
    add(fnv1a(data, len), count);
}

void CountMinSketch::add_string(const char* str, size_t len, int64_t count) {
    add(static_cast<const void*>(str), len, count);
}

// ════════════════════════════════════════════════════════════════════════
// Point-query (minimum across all rows)
// ════════════════════════════════════════════════════════════════════════

int64_t CountMinSketch::estimate(uint64_t key) const {
    int64_t min_val = std::numeric_limits<int64_t>::max();
    for (size_t row = 0; row < depth_; ++row) {
        uint64_t col = murmur_finalizer(key ^ hash_seeds_[row]) % width_;
        min_val = std::min(min_val, table_[row * width_ + col]);
    }
    return min_val;
}

int64_t CountMinSketch::estimate(const void* data, size_t len) const {
    return estimate(fnv1a(data, len));
}

int64_t CountMinSketch::estimate_string(const char* str, size_t len) const {
    return estimate(static_cast<const void*>(str), len);
}

// ════════════════════════════════════════════════════════════════════════
// Memory accounting
// ════════════════════════════════════════════════════════════════════════

size_t CountMinSketch::memory_bytes() const noexcept {
    return sizeof(*this)
         + table_.capacity()      * sizeof(int64_t)
         + hash_seeds_.capacity() * sizeof(uint64_t);
}

// ════════════════════════════════════════════════════════════════════════
// Merge
// ════════════════════════════════════════════════════════════════════════

void CountMinSketch::merge(const CountMinSketch& other) {
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
