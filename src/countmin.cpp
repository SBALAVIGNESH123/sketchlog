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

    table_.resize(width_ * depth_, 0);
    init_seeds();
}

void CountMinSketch::reset() noexcept {
    std::fill(table_.begin(), table_.end(), int64_t{0});
    total_count_ = 0;
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
    for (size_t row = 0; row < depth_; ++row) {
        uint64_t col = murmur_finalizer(key ^ hash_seeds_[row]) % width_;
        table_[row * width_ + col] += count;
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

    for (size_t i = 0, n = table_.size(); i < n; ++i)
        table_[i] += other.table_[i];

    total_count_ += other.total_count_;
}

} // namespace sketchlog
