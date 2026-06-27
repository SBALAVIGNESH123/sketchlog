#pragma once

#include <cstdint>
#include <cstddef>
#include <vector>
#include <algorithm>
#include <cmath>
#include <cstring>
#include <stdexcept>
#ifdef _MSC_VER
#include <intrin.h>
#endif

namespace sketchlog {

/// HyperLogLog probabilistic cardinality estimator.
///
/// Estimates the number of distinct elements added to the sketch.
/// Precision p (4–18) controls accuracy vs memory: 2^p registers,
/// relative standard error ≈ 1.04 / sqrt(2^p).
///   p=14 (default) → 16384 registers, ~16 KB, ~0.8% error.
class HyperLogLog {
public:
    /// Construct with precision p in [4, 18]. Uses 2^p registers.
    explicit HyperLogLog(uint8_t precision = 14);

    /// Add raw bytes to the sketch (hashed internally).
    void add(const void* data, size_t len);

    /// Add a pre-computed 64-bit hash value.
    void add(uint64_t hash);

    /// Convenience: add a string (raw bytes, no null-terminator dependency).
    void add_string(const char* str, size_t len);

    /// Return the estimated number of distinct elements.
    double estimate() const;

    /// Bytes consumed by the register array.
    size_t memory_bytes() const;

    /// Merge another HyperLogLog (must have the same precision).
    void merge(const HyperLogLog& other);

    /// Reset all registers to zero.
    void reset();

    /// Return the precision parameter p.
    uint8_t precision() const noexcept { return p_; }

    /// State structure for serialization
    struct State {
        uint8_t precision;
        std::vector<uint8_t> registers;
    };

    /// Get current state for serialization
    [[nodiscard]] State get_state() const;

    /// Restore state from a serialization payload
    void set_state(const State& state);

private:
    uint8_t              p_;          // precision, 4–18
    uint32_t             m_;          // register count = 1 << p_
    std::vector<uint8_t> registers_;  // m_ registers, each in [0, 64-p+1]

    /// MurmurHash3 64-bit finalizer (mix step).
    static uint64_t murmur3_fmix64(uint64_t h) noexcept;

    /// Hash arbitrary bytes → 64-bit value (FNV-1a body + murmur3 finalizer).
    static uint64_t hash_bytes(const void* data, size_t len) noexcept;

    /// Count leading zeros of a 64-bit word (platform-aware).
    static uint8_t clz64(uint64_t x) noexcept;

    /// Compute rho(w): position of the leftmost 1-bit (1-indexed).
    static uint8_t rho(uint64_t w) noexcept;

    /// Alpha constant for m registers.
    static double alpha(uint32_t m) noexcept;
};


// --- Implementation ---

// ---------------------------------------------------------------------------
// Construction / reset
// ---------------------------------------------------------------------------

namespace {
    uint8_t validate_precision(uint8_t p) {
        if (p < 4 || p > 18) {
            throw std::invalid_argument("HyperLogLog: precision must be in [4, 18]");
        }
        return p;
    }
}

inline HyperLogLog::HyperLogLog(uint8_t precision)
    : p_{validate_precision(precision)}
    , m_{static_cast<uint32_t>(1ull << p_)}
    , registers_(static_cast<size_t>(1ull << p_), uint8_t{0})
{
}

inline void HyperLogLog::reset() {
    std::fill(registers_.begin(), registers_.end(), uint8_t{0});
}

// ---------------------------------------------------------------------------
// Hashing helpers
// ---------------------------------------------------------------------------

inline uint64_t HyperLogLog::murmur3_fmix64(uint64_t h) noexcept {
    h ^= h >> 33;
    h *= 0xff51afd7ed558ccdULL;
    h ^= h >> 33;
    h *= 0xc4ceb9fe1a85ec53ULL;
    h ^= h >> 33;
    return h;
}

inline uint64_t HyperLogLog::hash_bytes(const void* data, size_t len) noexcept {
    // FNV-1a 64-bit core
    constexpr uint64_t fnv_offset = 14695981039346656037ULL;
    constexpr uint64_t fnv_prime  = 1099511628211ULL;

    auto ptr = static_cast<const uint8_t*>(data);
    uint64_t h = fnv_offset;
    for (size_t i = 0; i < len; ++i) {
        h ^= static_cast<uint64_t>(ptr[i]);
        h *= fnv_prime;
    }
    // Finalize with murmur3 avalanche for better bit distribution
    return murmur3_fmix64(h);
}

inline uint8_t HyperLogLog::clz64(uint64_t x) noexcept {
    if (x == 0) return 64;
#ifdef _MSC_VER
    unsigned long idx;
    _BitScanReverse64(&idx, x);
    return static_cast<uint8_t>(63 - idx);
#else
    return static_cast<uint8_t>(__builtin_clzll(x));
#endif
}

inline uint8_t HyperLogLog::rho(uint64_t w) noexcept {
    // Position of the leftmost 1-bit, 1-indexed.
    // If w == 0 we return 65, which is fine (capped by register width).
    return clz64(w) + 1;
}

inline double HyperLogLog::alpha(uint32_t m) noexcept {
    switch (m) {
        case 16:  return 0.673;
        case 32:  return 0.697;
        case 64:  return 0.709;
        default:  return 0.7213 / (1.0 + 1.079 / static_cast<double>(m));
    }
}

inline HyperLogLog::State HyperLogLog::get_state() const {
    State s;
    s.precision = p_;
    s.registers = registers_;
    return s;
}

inline void HyperLogLog::set_state(const State& s) {
    if (s.precision != p_) {
        throw std::invalid_argument("Cannot restore state with mismatched precision");
    }
    if (s.registers.size() != registers_.size()) {
        throw std::invalid_argument("Cannot restore state with mismatched register count");
    }
    uint8_t max_val = 64 - p_ + 1;
    for (uint8_t val : s.registers) {
        if (val > max_val) {
            throw std::invalid_argument("Cannot restore state with invalid register values");
        }
    }
    registers_ = s.registers;
}

// ---------------------------------------------------------------------------
// Adding elements
// ---------------------------------------------------------------------------

inline void HyperLogLog::add(const void* data, size_t len) {
    add(hash_bytes(data, len));
}

inline void HyperLogLog::add(uint64_t hash) {
    // Upper p bits → register index
    const uint32_t index = static_cast<uint32_t>(hash >> (64 - p_));

    // Shift left by p to put working bits at MSB, then count leading zeros + 1
    const uint64_t w = hash << p_;
    const uint8_t r = (w == 0) ? static_cast<uint8_t>(64 - p_ + 1) : static_cast<uint8_t>(clz64(w) + 1);
    if (r > registers_[index]) {
        registers_[index] = r;
    }
}

inline void HyperLogLog::add_string(const char* str, size_t len) {
    add(static_cast<const void*>(str), len);
}

// ---------------------------------------------------------------------------
// Estimation
// ---------------------------------------------------------------------------

inline double HyperLogLog::estimate() const {
    const double m  = static_cast<double>(m_);
    const double am = alpha(m_);

    // Harmonic mean: sum of 2^(-register[i])
    double sum = 0.0;
    uint32_t zeros = 0;
    for (uint32_t i = 0; i < m_; ++i) {
        sum += std::ldexp(1.0, -static_cast<int>(registers_[i]));
        if (registers_[i] == 0) {
            ++zeros;
        }
    }

    double raw = am * m * m / sum;

    // Small-range correction: linear counting
    if (raw <= 2.5 * m && zeros > 0) {
        raw = m * std::log(m / static_cast<double>(zeros));
    }

    return raw;
}

// ---------------------------------------------------------------------------
// Memory / merge
// ---------------------------------------------------------------------------

inline size_t HyperLogLog::memory_bytes() const {
    return registers_.capacity() * sizeof(uint8_t);
}

inline void HyperLogLog::merge(const HyperLogLog& other) {
    if (p_ != other.p_) {
        throw std::invalid_argument(
            "HyperLogLog::merge: precision mismatch");
    }
    for (uint32_t i = 0; i < m_; ++i) {
        if (other.registers_[i] > registers_[i]) {
            registers_[i] = other.registers_[i];
        }
    }
}


} // namespace sketchlog
