#include "hyperloglog.hpp"

#include <algorithm>
#include <cmath>
#include <cstring>
#include <stdexcept>

#ifdef _MSC_VER
#include <intrin.h>
#endif

namespace sketchlog {

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

HyperLogLog::HyperLogLog(uint8_t precision)
    : p_{validate_precision(precision)}
    , m_{static_cast<uint32_t>(1ull << p_)}
    , registers_(static_cast<size_t>(1ull << p_), uint8_t{0})
{
}

void HyperLogLog::reset() {
    std::fill(registers_.begin(), registers_.end(), uint8_t{0});
}

// ---------------------------------------------------------------------------
// Hashing helpers
// ---------------------------------------------------------------------------

uint64_t HyperLogLog::murmur3_fmix64(uint64_t h) noexcept {
    h ^= h >> 33;
    h *= 0xff51afd7ed558ccdULL;
    h ^= h >> 33;
    h *= 0xc4ceb9fe1a85ec53ULL;
    h ^= h >> 33;
    return h;
}

uint64_t HyperLogLog::hash_bytes(const void* data, size_t len) noexcept {
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

uint8_t HyperLogLog::clz64(uint64_t x) noexcept {
    if (x == 0) return 64;
#ifdef _MSC_VER
    unsigned long idx;
    _BitScanReverse64(&idx, x);
    return static_cast<uint8_t>(63 - idx);
#else
    return static_cast<uint8_t>(__builtin_clzll(x));
#endif
}

uint8_t HyperLogLog::rho(uint64_t w) noexcept {
    // Position of the leftmost 1-bit, 1-indexed.
    // If w == 0 we return 65, which is fine (capped by register width).
    return clz64(w) + 1;
}

double HyperLogLog::alpha(uint32_t m) noexcept {
    switch (m) {
        case 16:  return 0.673;
        case 32:  return 0.697;
        case 64:  return 0.709;
        default:  return 0.7213 / (1.0 + 1.079 / static_cast<double>(m));
    }
}

// ---------------------------------------------------------------------------
// Adding elements
// ---------------------------------------------------------------------------

void HyperLogLog::add(const void* data, size_t len) {
    add(hash_bytes(data, len));
}

void HyperLogLog::add(uint64_t hash) {
    // Upper p bits → register index
    const uint32_t index = static_cast<uint32_t>(hash >> (64 - p_));

    // Shift left by p to put working bits at MSB, then count leading zeros + 1
    const uint64_t w = hash << p_;
    const uint8_t r = (w == 0) ? static_cast<uint8_t>(64 - p_ + 1) : static_cast<uint8_t>(clz64(w) + 1);
    if (r > registers_[index]) {
        registers_[index] = r;
    }
}

void HyperLogLog::add_string(const char* str, size_t len) {
    add(static_cast<const void*>(str), len);
}

// ---------------------------------------------------------------------------
// Estimation
// ---------------------------------------------------------------------------

double HyperLogLog::estimate() const {
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

size_t HyperLogLog::memory_bytes() const {
    return registers_.capacity() * sizeof(uint8_t);
}

void HyperLogLog::merge(const HyperLogLog& other) {
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
