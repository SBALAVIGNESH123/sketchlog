#pragma once

#include <cstddef>
#include <cstdint>
#include <vector>
#include <algorithm>
#include <cassert>
#include <cmath>
#include <limits>
#include <map>
#include <stdexcept>
#include <utility>

namespace sketchlog {

/// DDSketch: logarithmic quantile sketch with bounded relative error.
///
/// For any quantile q, the returned value v satisfies:
///   |v - true_v| <= alpha * true_v
/// where alpha is the relative accuracy parameter.
class DDSketch {
public:
    /// Construct a DDSketch with the given relative accuracy guarantee.
    /// @param relative_accuracy  alpha in [1e-6, 1.0). Default 0.01 (1% error).
    explicit DDSketch(double relative_accuracy = 0.01);

    /// Add a single observation.
    void add(double value);

    /// Add an observation with a repetition count.
    void add(double value, size_t count);

    /// Add a batch of observations efficiently.
    void add_batch(const std::vector<double>& values);
    void add_batch(const double* values, size_t size);

    /// Return the approximate value at the given quantile.
    /// @param q  quantile in [0.0, 1.0], e.g. 0.99 for p99.
    /// @return   approximate quantile value, or 0 if the sketch is empty.
    [[nodiscard]] double quantile(double q) const;

    /// Count number of values strictly greater than threshold.
    [[nodiscard]] uint64_t count_greater_than(double threshold) const;

    /// Minimum value added to the sketch.
    [[nodiscard]] double min() const;

    /// Maximum value added to the sketch.
    [[nodiscard]] double max() const;

    /// Total number of values added (counting repetitions).
    [[nodiscard]] size_t count() const;

    /// Estimated current memory usage in bytes.
    [[nodiscard]] size_t memory_bytes() const;

    /// Merge another DDSketch (must have the same relative_accuracy) into this one.
    void merge(const DDSketch& other);

    /// Reset the sketch to its initial empty state.
    void reset();

    /// State structure for serialization
    struct State {
        double alpha;
        int64_t zero_count;
        size_t count;
        double min_value;
        double max_value;
        std::vector<int> pos_indices;
        std::vector<int64_t> pos_bins;
        int pos_offset;
        int pos_min_index;
        int pos_max_index;
        bool pos_empty;
        std::vector<int> neg_indices;
        std::vector<int64_t> neg_bins;
        int neg_offset;
        int neg_min_index;
        int neg_max_index;
        bool neg_empty;
    };

    /// Get current state for serialization
    [[nodiscard]] State get_state() const;

    /// Restore state from a serialization payload
    void set_state(const State& state);

private:
    // ── Logarithmic index mapping ──────────────────────────────────────
    //   gamma = (1 + alpha) / (1 - alpha)
    //   key(v) = ceil(log(v) * multiplier_)   where multiplier_ = 1/log(gamma)

    double alpha_;          // relative accuracy
    double gamma_;          // (1+alpha)/(1-alpha)
    double multiplier_;     // 1.0 / log(gamma)

    /// Map a positive value to its bucket index.
    [[nodiscard]] int key(double value) const;

    /// Recover a representative value from a bucket index.
    [[nodiscard]] double bucket_value(int index) const;

    /// Validate alpha before the initializer list computes gamma/multiplier.
    static double validate_alpha(double alpha);

    // ── Dense store ────────────────────────────────────────────────────
    //   Contiguous vector mapped to sparse integer indices via an offset.
    //   bucket[i] corresponds to logical index (offset_ + i).

    struct DenseStore {
        // Keep the DDS component within a predictable memory envelope. Values
        // that would require a wider logarithmic range are rejected instead of
        // allowing an attacker-controlled multi-gigabyte vector allocation.
        static constexpr size_t MAX_BINS = 1024;

        std::map<int, int64_t> bins;
        int min_index = 0;
        int max_index = 0;
        bool empty = true;

        bool can_fit(int index) const;
        bool can_add(int index, int64_t count) const;
        void add(int index, int64_t count);
        [[nodiscard]] uint64_t total() const;
        void merge(const DenseStore& other);
        void reset();
    };

    DenseStore positive_;   // buckets for positive values
    DenseStore negative_;   // buckets for |negative values|

    int64_t zero_count_ = 0;
    size_t  count_      = 0;
    double  min_value_  = 0.0;
    double  max_value_  = 0.0;
};


// --- Implementation ---

// ════════════════════════════════════════════════════════════════════════
//  Alpha validation (must run before initializer-list uses alpha)
// ════════════════════════════════════════════════════════════════════════

inline double DDSketch::validate_alpha(double alpha) {
    if (!std::isfinite(alpha) || alpha < 1e-6 || alpha >= 1.0)
        throw std::invalid_argument("DDSketch: relative_accuracy must be in [1e-6, 1.0)");
    return alpha;
}

// ════════════════════════════════════════════════════════════════════════
//  DenseStore
// ════════════════════════════════════════════════════════════════════════

inline bool DDSketch::DenseStore::can_add(int index, int64_t count) const {
    const auto it = bins.find(index);
    if (it == bins.end()) return true;
    if (std::numeric_limits<int64_t>::max() - it->second < count) return false;
    return true;
}

inline bool DDSketch::DenseStore::can_fit(int index) const {
    return bins.find(index) != bins.end() || bins.size() < MAX_BINS;
}

inline void DDSketch::DenseStore::add(int index, int64_t count) {
    if (!can_fit(index)) {
        throw std::invalid_argument(
            "DDSketch: occupied bucket count exceeds bounded sparse-store capacity");
    }

    if (empty) {
        min_index = index;
        max_index = index;
        empty     = false;
    }

    auto [it, inserted] = bins.try_emplace(index, 0);
    (void)inserted;
    if (std::numeric_limits<int64_t>::max() - it->second < count) {
        throw std::overflow_error("DDSketch: bin count overflow");
    }
    it->second += count;
    if (index < min_index) min_index = index;
    if (index > max_index) max_index = index;
}

inline uint64_t DDSketch::DenseStore::total() const {
    uint64_t sum = 0;
    for (const auto& [index, count] : bins) {
        (void)index;
        sum += static_cast<uint64_t>(count);
    }
    return sum;
}

inline void DDSketch::DenseStore::merge(const DenseStore& other) {
    if (other.empty) return;
    for (const auto& [index, count] : other.bins) {
        add(index, count);
    }
}

inline void DDSketch::DenseStore::reset() {
    bins.clear();
    min_index = 0;
    max_index = 0;
    empty     = true;
}

// ════════════════════════════════════════════════════════════════════════
//  DDSketch construction / reset
// ════════════════════════════════════════════════════════════════════════

inline DDSketch::DDSketch(double relative_accuracy)
    : alpha_{validate_alpha(relative_accuracy)}
    , gamma_{(1.0 + alpha_) / (1.0 - alpha_)}
    , multiplier_{1.0 / std::log(gamma_)}
{}

inline void DDSketch::reset() {
    positive_.reset();
    negative_.reset();
    zero_count_ = 0;
    count_      = 0;
    min_value_  = 0.0;
    max_value_  = 0.0;
}

inline DDSketch::State DDSketch::get_state() const {
    State s;
    s.alpha = alpha_;
    s.zero_count = zero_count_;
    s.count = count_;
    s.min_value = min_value_;
    s.max_value = max_value_;
    for (const auto& [index, count] : positive_.bins) {
        s.pos_indices.push_back(index);
        s.pos_bins.push_back(count);
    }
    s.pos_offset = 0;
    s.pos_min_index = positive_.min_index;
    s.pos_max_index = positive_.max_index;
    s.pos_empty = positive_.empty;
    for (const auto& [index, count] : negative_.bins) {
        s.neg_indices.push_back(index);
        s.neg_bins.push_back(count);
    }
    s.neg_offset = 0;
    s.neg_min_index = negative_.min_index;
    s.neg_max_index = negative_.max_index;
    s.neg_empty = negative_.empty;
    return s;
}

inline void DDSketch::set_state(const State& s) {
    if (std::abs(alpha_ - s.alpha) > 1e-9) {
        throw std::invalid_argument("Cannot restore state with mismatched alpha");
    }
    if (s.zero_count < 0) {
        throw std::invalid_argument("Cannot restore state with negative zero_count");
    }

    auto validate_store = [](const std::vector<int>& indices,
                             const std::vector<int64_t>& bins, int min_idx,
                             int max_idx, bool empty) -> uint64_t {
        if (empty) {
            if (!bins.empty() || !indices.empty()) {
                throw std::invalid_argument("Empty DDSketch store must not contain bins");
            }
            return 0;
        }
        if (bins.empty() || bins.size() != indices.size()
                || bins.size() > DenseStore::MAX_BINS) {
            throw std::invalid_argument("DDSketch store has an invalid bounded size");
        }
        if (min_idx > max_idx) {
            throw std::invalid_argument("Cannot restore state with min_index > max_index");
        }
        if (indices.front() != min_idx || indices.back() != max_idx) {
            throw std::invalid_argument("DDSketch min/max indexes must reference positive bins");
        }

        uint64_t total = 0;
        for (size_t i = 0; i < bins.size(); ++i) {
            const auto c = bins[i];
            if (i > 0 && indices[i - 1] >= indices[i]) {
                throw std::invalid_argument(
                    "DDSketch restored indexes must be strictly increasing");
            }
            if (c <= 0) {
                throw std::invalid_argument("DDSketch restored bin counts must be positive");
            }
            if (std::numeric_limits<uint64_t>::max() - total
                    < static_cast<uint64_t>(c)) {
                throw std::invalid_argument("DDSketch bin totals overflow");
            }
            total += static_cast<uint64_t>(c);
        }
        return total;
    };

    const uint64_t pos_total = validate_store(
        s.pos_indices, s.pos_bins, s.pos_min_index, s.pos_max_index, s.pos_empty);
    const uint64_t neg_total = validate_store(
        s.neg_indices, s.neg_bins, s.neg_min_index, s.neg_max_index, s.neg_empty);
    uint64_t restored_total = static_cast<uint64_t>(s.zero_count);
    if (std::numeric_limits<uint64_t>::max() - restored_total < pos_total
            || std::numeric_limits<uint64_t>::max() - restored_total - pos_total < neg_total) {
        throw std::invalid_argument("DDSketch restored count overflows");
    }
    restored_total += pos_total + neg_total;
    if (restored_total != s.count) {
        throw std::invalid_argument("DDSketch restored bins do not sum to count");
    }
    if (!std::isfinite(s.min_value) || !std::isfinite(s.max_value)
            || (s.count > 0 && s.min_value > s.max_value)) {
        throw std::invalid_argument("DDSketch restored extrema are invalid");
    }
    if (s.count == 0 && (!s.pos_empty || !s.neg_empty || s.zero_count != 0)) {
        throw std::invalid_argument("Empty DDSketch has non-empty restored state");
    }

    zero_count_ = s.zero_count;
    count_ = s.count;
    min_value_ = s.min_value;
    max_value_ = s.max_value;
    positive_.bins.clear();
    for (size_t i = 0; i < s.pos_indices.size(); ++i) {
        positive_.bins.emplace(s.pos_indices[i], s.pos_bins[i]);
    }
    positive_.min_index = s.pos_min_index;
    positive_.max_index = s.pos_max_index;
    positive_.empty = s.pos_empty;
    negative_.bins.clear();
    for (size_t i = 0; i < s.neg_indices.size(); ++i) {
        negative_.bins.emplace(s.neg_indices[i], s.neg_bins[i]);
    }
    negative_.min_index = s.neg_min_index;
    negative_.max_index = s.neg_max_index;
    negative_.empty = s.neg_empty;
}

// ════════════════════════════════════════════════════════════════════════
//  Index mapping
// ════════════════════════════════════════════════════════════════════════

inline int DDSketch::key(double value) const {
    return static_cast<int>(std::ceil(std::log(value) * multiplier_));
}

inline double DDSketch::bucket_value(int index) const {
    // Representative value at the centre of the bucket in log-space:
    //   v = 2 * gamma^index / (1 + gamma)
    double v = (2.0 / (1.0 + gamma_)) * std::pow(gamma_, index);
    if (v == 0.0) return std::numeric_limits<double>::denorm_min();
    if (std::isinf(v)) return std::numeric_limits<double>::max();
    return v;
}

// ════════════════════════════════════════════════════════════════════════
//  add
// ════════════════════════════════════════════════════════════════════════

inline void DDSketch::add(double value) {
    if (std::isnan(value) || std::isinf(value)) return; // silently reject
    add(value, 1);
}

inline void DDSketch::add_batch(const double* values, size_t size) {
    // Apply to a copy so range/count failures cannot partially mutate the
    // sketch. This also makes StreamLog::add_batch transactional.
    DDSketch temp(*this);
    for (size_t i = 0; i < size; ++i) {
        temp.add(values[i]);
    }
    *this = std::move(temp);
}

inline void DDSketch::add_batch(const std::vector<double>& values) {
    add_batch(values.data(), values.size());
}

inline void DDSketch::add(double value, size_t count) {
    if (std::isnan(value) || std::isinf(value)) return; // silently reject
    if (count == 0) return;

    if (value != 0.0) {
        double abs_v = std::abs(value);
        int idx = key(abs_v);
        double rep = bucket_value(idx);
        if (std::abs(rep - abs_v) / abs_v > alpha_) {
            throw std::invalid_argument("DDSketch: value magnitude too small to satisfy relative accuracy");
        }
    }

    if (std::numeric_limits<size_t>::max() - count_ < count) {
        throw std::overflow_error("DDSketch: total count overflow");
    }

    if (count > static_cast<size_t>(std::numeric_limits<int64_t>::max())) {
        throw std::overflow_error("DDSketch: count exceeds int64_t capacity");
    }
    auto n = static_cast<int64_t>(count);

    if (value > 0.0) {
        const int idx = key(value);
        if (!positive_.can_fit(idx)) {
            throw std::invalid_argument(
                "DDSketch: value range exceeds the bounded dense-store capacity");
        }
        if (!positive_.can_add(idx, n)) {
             throw std::overflow_error("DDSketch: bin count overflow");
        }
    } else if (value < 0.0) {
        const int idx = key(-value);
        if (!negative_.can_fit(idx)) {
            throw std::invalid_argument(
                "DDSketch: value range exceeds the bounded dense-store capacity");
        }
        if (!negative_.can_add(idx, n)) {
             throw std::overflow_error("DDSketch: bin count overflow");
        }
    } else {
        if (std::numeric_limits<int64_t>::max() - zero_count_ < n) {
            throw std::overflow_error("DDSketch: zero count overflow");
        }
    }

    if (count_ == 0) {
        min_value_ = value;
        max_value_ = value;
    } else {
        if (value < min_value_) min_value_ = value;
        if (value > max_value_) max_value_ = value;
    }
    count_ += count;

    if (value > 0.0) {
        positive_.add(key(value), n);
    } else if (value < 0.0) {
        negative_.add(key(-value), n);
    } else {
        zero_count_ += n;
    }
}

// ════════════════════════════════════════════════════════════════════════
//  quantile
// ════════════════════════════════════════════════════════════════════════

inline double DDSketch::quantile(double q) const {
    if (std::isnan(q) || std::isinf(q)) {
        throw std::invalid_argument("Quantile must be in [0, 1]");
    }
    if (count_ == 0) return 0.0;

    if (q <= 0.0) return min_value_;
    if (q >= 1.0) return max_value_;

    // Target rank (1-based).  We walk buckets until cumulative >= rank.
    double rank = q * static_cast<double>(count_);

    // 1. Walk negative buckets (highest magnitude first → most-negative values first).
    if (!negative_.empty) {
        for (auto it = negative_.bins.rbegin(); it != negative_.bins.rend(); ++it) {
            rank -= static_cast<double>(it->second);
            if (rank <= 0.0) {
                return -bucket_value(it->first);
            }
        }
    }

    // 2. Walk zero bucket.
    rank -= static_cast<double>(zero_count_);
    if (rank <= 0.0) return 0.0;

    // 3. Walk positive buckets (smallest first).
    if (!positive_.empty) {
        for (const auto& [index, count] : positive_.bins) {
            rank -= static_cast<double>(count);
            if (rank <= 0.0) {
                return bucket_value(index);
            }
        }
    }

    // Should not reach here; fall back to max.
    return max_value_;
}

inline uint64_t DDSketch::count_greater_than(double threshold) const {
    if (count_ == 0) return 0;
    if (threshold >= max_value_) return 0;
    if (threshold < min_value_) return count_;

    uint64_t count_gt = 0;

    if (threshold < 0.0) {
        int idx = key(-threshold);
        if (!negative_.empty) {
            for (const auto& [index, count] : negative_.bins) {
                // Include the threshold bucket. DDSketch cannot recover
                // ordering within a bucket, so this is a conservative upper
                // bound suitable for alerting rather than an unsafe undercount.
                if (index <= idx) {
                    count_gt += static_cast<uint64_t>(count);
                }
            }
        }
        count_gt += zero_count_;
        if (!positive_.empty) {
            count_gt += positive_.total();
        }
        return count_gt;
    }

    if (threshold == 0.0) {
        if (!positive_.empty) {
            count_gt += positive_.total();
        }
        return count_gt;
    }

    int idx = key(threshold);
    if (!positive_.empty) {
        for (const auto& [index, count] : positive_.bins) {
            if (index >= idx) {
                count_gt += static_cast<uint64_t>(count);
            }
        }
    }

    return count_gt;
}

// ════════════════════════════════════════════════════════════════════════
//  Accessors
// ════════════════════════════════════════════════════════════════════════

inline double DDSketch::min() const { return min_value_; }
inline double DDSketch::max() const { return max_value_; }
inline size_t DDSketch::count() const { return count_; }

inline size_t DDSketch::memory_bytes() const {
    return sizeof(*this)
         + (positive_.bins.size() + negative_.bins.size())
           * (sizeof(std::pair<const int, int64_t>) + 3 * sizeof(void*));
}

// ════════════════════════════════════════════════════════════════════════
//  merge
// ════════════════════════════════════════════════════════════════════════

inline void DDSketch::merge(const DDSketch& other) {
    if (alpha_ != other.alpha_) {
        throw std::invalid_argument("DDSketch::merge: relative_accuracy mismatch");
    }
    if (other.count_ == 0) return;

    DDSketch temp(*this);

    if (std::numeric_limits<size_t>::max() - temp.count_ < other.count_) {
        throw std::overflow_error("DDSketch: total count overflow");
    }
    temp.count_ += other.count_;

    if (std::numeric_limits<int64_t>::max() - temp.zero_count_ < other.zero_count_) {
        throw std::overflow_error("DDSketch: zero count overflow");
    }
    temp.zero_count_ += other.zero_count_;

    if (count_ == 0) {
        temp.min_value_ = other.min_value_;
        temp.max_value_ = other.max_value_;
    } else {
        if (other.min_value_ < temp.min_value_) temp.min_value_ = other.min_value_;
        if (other.max_value_ > temp.max_value_) temp.max_value_ = other.max_value_;
    }

    temp.positive_.merge(other.positive_);
    temp.negative_.merge(other.negative_);

    *this = std::move(temp);
}


} // namespace sketchlog
