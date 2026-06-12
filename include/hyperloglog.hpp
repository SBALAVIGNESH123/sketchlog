#pragma once

#include <cstdint>
#include <cstddef>
#include <vector>

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

} // namespace sketchlog
