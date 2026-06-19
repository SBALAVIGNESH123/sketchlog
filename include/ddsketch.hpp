#pragma once

#include <cstddef>
#include <cstdint>
#include <vector>

namespace sketchlog {

/// DDSketch: logarithmic quantile sketch with bounded relative error.
///
/// For any quantile q, the returned value v satisfies:
///   |v - true_v| <= alpha * true_v
/// where alpha is the relative accuracy parameter.
class DDSketch {
public:
    /// Construct a DDSketch with the given relative accuracy guarantee.
    /// @param relative_accuracy  alpha in (0, 1). Default 0.01 (1% error).
    explicit DDSketch(double relative_accuracy = 0.01);

    /// Add a single observation.
    void add(double value);

    /// Add an observation with a repetition count.
    void add(double value, size_t count);

    /// Return the approximate value at the given quantile.
    /// @param q  quantile in [0.0, 1.0], e.g. 0.99 for p99.
    /// @return   approximate quantile value, or 0 if the sketch is empty.
    [[nodiscard]] double quantile(double q) const;

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
        std::vector<int64_t> bins;
        int offset = 0;     // logical index of bins[0]
        int min_index = 0;
        int max_index = 0;
        bool empty = true;

        bool can_add(int index, int64_t count) const;
        void add(int index, int64_t count);
        [[nodiscard]] int64_t total() const;
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

} // namespace sketchlog
