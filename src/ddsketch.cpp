#include "ddsketch.hpp"

#include <algorithm>
#include <cassert>
#include <cmath>
#include <limits>
#include <stdexcept>

namespace sketchlog {

// ════════════════════════════════════════════════════════════════════════
//  Alpha validation (must run before initializer-list uses alpha)
// ════════════════════════════════════════════════════════════════════════

/*static*/ double DDSketch::validate_alpha(double alpha) {
    if (alpha <= 0.0 || alpha >= 1.0)
        throw std::invalid_argument("DDSketch: relative_accuracy must be in (0, 1)");
    return alpha;
}

// ════════════════════════════════════════════════════════════════════════
//  DenseStore
// ════════════════════════════════════════════════════════════════════════

void DDSketch::DenseStore::add(int index, int64_t count) {
    if (empty) {
        // First insertion: allocate a single slot.
        bins.resize(1, 0);
        offset    = index;
        min_index = index;
        max_index = index;
        empty     = false;
    }

    if (index < offset) {
        // Grow leftward.
        int grow = offset - index;
        bins.insert(bins.begin(), static_cast<size_t>(grow), int64_t{0});
        offset = index;
    } else if (index >= offset + static_cast<int>(bins.size())) {
        // Grow rightward.
        bins.resize(static_cast<size_t>(index - offset + 1), int64_t{0});
    }

    bins[static_cast<size_t>(index - offset)] += count;
    if (index < min_index) min_index = index;
    if (index > max_index) max_index = index;
}

int64_t DDSketch::DenseStore::total() const {
    int64_t sum = 0;
    for (auto c : bins) sum += c;
    return sum;
}

void DDSketch::DenseStore::merge(const DenseStore& other) {
    if (other.empty) return;
    for (int i = other.min_index; i <= other.max_index; ++i) {
        int64_t c = other.bins[static_cast<size_t>(i - other.offset)];
        if (c > 0) add(i, c);
    }
}

void DDSketch::DenseStore::reset() {
    bins.clear();
    offset    = 0;
    min_index = 0;
    max_index = 0;
    empty     = true;
}

// ════════════════════════════════════════════════════════════════════════
//  DDSketch construction / reset
// ════════════════════════════════════════════════════════════════════════

DDSketch::DDSketch(double relative_accuracy)
    : alpha_{validate_alpha(relative_accuracy)}
    , gamma_{(1.0 + alpha_) / (1.0 - alpha_)}
    , multiplier_{1.0 / std::log(gamma_)}
{}

void DDSketch::reset() {
    positive_.reset();
    negative_.reset();
    zero_count_ = 0;
    count_      = 0;
    min_value_  = 0.0;
    max_value_  = 0.0;
}

// ════════════════════════════════════════════════════════════════════════
//  Index mapping
// ════════════════════════════════════════════════════════════════════════

int DDSketch::key(double value) const {
    return static_cast<int>(std::ceil(std::log(value) * multiplier_));
}

double DDSketch::bucket_value(int index) const {
    // Representative value at the centre of the bucket in log-space:
    //   v = 2 * gamma^index / (1 + gamma)
    return 2.0 * std::pow(gamma_, index) / (1.0 + gamma_);
}

// ════════════════════════════════════════════════════════════════════════
//  add
// ════════════════════════════════════════════════════════════════════════

void DDSketch::add(double value) {
    if (std::isnan(value) || std::isinf(value)) return; // silently reject
    add(value, 1);
}

void DDSketch::add(double value, size_t count) {
    if (std::isnan(value) || std::isinf(value)) return; // silently reject
    if (count == 0) return;

    auto n = static_cast<int64_t>(count);

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

double DDSketch::quantile(double q) const {
    if (count_ == 0) return 0.0;

    if (q <= 0.0) return min_value_;
    if (q >= 1.0) return max_value_;

    // Target rank (1-based).  We walk buckets until cumulative >= rank.
    double rank = q * static_cast<double>(count_);

    // 1. Walk negative buckets (highest magnitude first → most-negative values first).
    if (!negative_.empty) {
        for (int i = negative_.max_index; i >= negative_.min_index; --i) {
            int64_t c = negative_.bins[static_cast<size_t>(i - negative_.offset)];
            if (c == 0) continue;
            rank -= static_cast<double>(c);
            if (rank <= 0.0) {
                return -bucket_value(i);
            }
        }
    }

    // 2. Walk zero bucket.
    rank -= static_cast<double>(zero_count_);
    if (rank <= 0.0) return 0.0;

    // 3. Walk positive buckets (smallest first).
    if (!positive_.empty) {
        for (int i = positive_.min_index; i <= positive_.max_index; ++i) {
            int64_t c = positive_.bins[static_cast<size_t>(i - positive_.offset)];
            if (c == 0) continue;
            rank -= static_cast<double>(c);
            if (rank <= 0.0) {
                return bucket_value(i);
            }
        }
    }

    // Should not reach here; fall back to max.
    return max_value_;
}

// ════════════════════════════════════════════════════════════════════════
//  Accessors
// ════════════════════════════════════════════════════════════════════════

double DDSketch::min() const { return min_value_; }
double DDSketch::max() const { return max_value_; }
size_t DDSketch::count() const { return count_; }

size_t DDSketch::memory_bytes() const {
    return sizeof(*this)
         + positive_.bins.capacity() * sizeof(int64_t)
         + negative_.bins.capacity() * sizeof(int64_t);
}

// ════════════════════════════════════════════════════════════════════════
//  merge
// ════════════════════════════════════════════════════════════════════════

void DDSketch::merge(const DDSketch& other) {
    if (alpha_ != other.alpha_) {
        throw std::invalid_argument("DDSketch::merge: relative_accuracy mismatch");
    }
    if (other.count_ == 0) return;

    if (count_ == 0) {
        min_value_ = other.min_value_;
        max_value_ = other.max_value_;
    } else {
        if (other.min_value_ < min_value_) min_value_ = other.min_value_;
        if (other.max_value_ > max_value_) max_value_ = other.max_value_;
    }

    count_      += other.count_;
    zero_count_ += other.zero_count_;

    positive_.merge(other.positive_);
    negative_.merge(other.negative_);
}

} // namespace sketchlog
