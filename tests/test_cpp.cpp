#undef NDEBUG
#include <iostream>
#include <cassert>
#include <cmath>
#include <stdexcept>
#include "sketchlog.hpp"
#include "hyperloglog.hpp"
#include "countmin.hpp"
#include "ddsketch.hpp"

using namespace sketchlog;

void test_constructor_validation() {
    bool caught;

    // CountMinSketch depth
    caught = false;
    try {
        CountMinSketch cms(100, 0);
    } catch (const std::invalid_argument&) { caught = true; }
    assert(caught && "Should throw on depth 0");

    // CountMinSketch width
    caught = false;
    try {
        CountMinSketch cms(0, 5);
    } catch (const std::invalid_argument&) { caught = true; }
    assert(caught && "Should throw on width 0");

    // HyperLogLog precision lower bound
    caught = false;
    try {
        HyperLogLog hll(3);
    } catch (const std::invalid_argument&) { caught = true; }
    assert(caught && "Should throw on precision < 4");

    // HyperLogLog precision upper bound
    caught = false;
    try {
        HyperLogLog hll(19);
    } catch (const std::invalid_argument&) { caught = true; }
    assert(caught && "Should throw on precision > 18");

    std::cout << "test_constructor_validation passed\n";
}

void test_nan_and_inf_handling() {
    StreamLog log;
    log.add_latency(NAN);
    log.add_latency(INFINITY);
    log.add_latency(-INFINITY);

    Stats s = log.stats();
    assert(s.events == 0 && "Events should be 0 after adding NaN/Inf");

    std::cout << "test_nan_and_inf_handling passed\n";
}

void test_negative_zero_counts() {
    StreamLog log;
    bool caught;

    caught = false;
    try {
        log.add_event("event1", 0);
    } catch (const std::invalid_argument&) { caught = true; }
    assert(caught && "Should throw on zero count");

    caught = false;
    try {
        log.add_event("event2", -5);
    } catch (const std::invalid_argument&) { caught = true; }
    assert(caught && "Should throw on negative count");

    std::cout << "test_negative_zero_counts passed\n";
}

void test_cardinality() {
    StreamLog log;
    log.add_unique("a");
    log.add_unique("b");
    log.add_unique("c");

    assert(log.unique_count() == 3 && "Should accurately estimate small cardinalities");

    std::cout << "test_cardinality passed\n";
}

void test_merge_mismatch() {
    CountMinSketch c1(100, 5);
    CountMinSketch c2(200, 5);

    bool caught = false;
    try {
        c1.merge(c2);
    } catch (const std::invalid_argument&) { caught = true; }
    assert(caught && "Should throw when merging mismatched CountMinSketch dimensions");

    HyperLogLog h1(10);
    HyperLogLog h2(12);

    caught = false;
    try {
        h1.merge(h2);
    } catch (const std::invalid_argument&) { caught = true; }
    assert(caught && "Should throw when merging mismatched HyperLogLog precision");

    std::cout << "test_merge_mismatch passed\n";
}

void test_ddsketch_nan_inf_validation() {
    bool caught;
    const double invalid_vals[] = {NAN, INFINITY, -INFINITY};
    for (double val : invalid_vals) {
        caught = false;
        try {
            DDSketch sketch(val);
        } catch (const std::invalid_argument&) { caught = true; }
        assert(caught && "Should throw on invalid DDSketch accuracy");

        caught = false;
        try {
            StreamLog log(val);
        } catch (const std::invalid_argument&) { caught = true; }
        assert(caught && "Should throw on invalid StreamLog accuracy");
    }
    std::cout << "test_ddsketch_nan_inf_validation passed\n";
}

void test_overflow_guards() {
    bool caught;

    // 1. Counter overflow
    CountMinSketch cms(100, 5);
    int64_t maximum = std::numeric_limits<int64_t>::max();
    cms.add(1, maximum);
    caught = false;
    try {
        cms.add(1, maximum);
    } catch (const std::overflow_error&) { caught = true; }
    assert(caught && "CountMinSketch should throw on overflow");
    assert(cms.total_count() == maximum && "CountMinSketch state should not mutate on overflow");

    // 2. StreamLog DDSketch bin count overflow
    StreamLog bin_log;
    StreamLog pow_log;
    pow_log.add_latency(1.0);
    for (int i = 0; i < 63; ++i) {
        bin_log.merge(pow_log);
        if (i < 62) {
            pow_log.merge(pow_log);
        }
    }

    caught = false;
    uint64_t total_before = bin_log.total_events();
    double p99_before = bin_log.p99();
    try {
        bin_log.add_latency(1.0);
    } catch (const std::overflow_error&) { caught = true; }
    assert(caught && "StreamLog should throw on DDSketch bin overflow via add_latency");
    assert(bin_log.total_events() == total_before && "StreamLog should not mutate total_events on bin overflow");
    assert(bin_log.p99() == p99_before && "StreamLog should not mutate p99 on bin overflow");

    // 3. StreamLog total_events overflow via add_latency
    StreamLog lat_log;
    lat_log.merge(bin_log); // lat_log now has INT64_MAX events

    int64_t max_cms = std::numeric_limits<int64_t>::max();
    lat_log.add_event("x", max_cms);
    lat_log.add_latency(2.0);
    assert(lat_log.total_events() == std::numeric_limits<uint64_t>::max() && "Should exactly reach UINT64_MAX");

    p99_before = lat_log.p99();
    caught = false;
    try {
        lat_log.add_latency(3.0);
    } catch (const std::overflow_error&) { caught = true; }
    assert(caught && "StreamLog should throw on total_events overflow via add_latency");
    assert(lat_log.total_events() == std::numeric_limits<uint64_t>::max() && "StreamLog should not mutate total_events on overflow");
    assert(lat_log.p99() == p99_before && "StreamLog should not mutate p99 on overflow");

    // 4. StreamLog merge atomicity
    StreamLog log1;
    log1.add_event("x", max_cms / 2);
    log1.add_latency(1.0);

    StreamLog log2;
    log2.add_event("y", (max_cms / 2) + 2);
    log2.add_latency(2.0);

    p99_before = log1.p99();
    caught = false;
    try {
        log1.merge(log2);
    } catch (const std::overflow_error&) { caught = true; }
    assert(caught && "StreamLog should throw on CountMinSketch overflow during merge");
    assert(log1.p99() == p99_before && "StreamLog should not mutate on failed merge");

    // 5. Batch failure must not commit earlier values.
    StreamLog batch_log;
    StreamLog batch_power;
    batch_power.add_latency(1.0);
    batch_power.merge(batch_power);  // start at 2
    for (int exponent = 1; exponent < 63; ++exponent) {
        batch_log.merge(batch_power);
        if (exponent < 62) {
            batch_power.merge(batch_power);
        }
    }
    const double repeated[] = {1.0, 1.0};
    total_before = batch_log.total_events();
    caught = false;
    try {
        batch_log.add_batch(repeated, 2);
    } catch (const std::overflow_error&) { caught = true; }
    assert(caught && "Batch cumulative bin overflow must throw");
    assert(batch_log.latency_count() == total_before
           && "Failed batch must leave latency count unchanged");

    std::cout << "test_overflow_guards passed\n";
}

void test_sparse_store_bounds() {
    DDSketch extremes;
    const double values[] = {1e-300, 1e300};
    extremes.add_batch(values, 2);
    assert(extremes.count() == 2);
    assert(extremes.memory_bytes() < 256 * 1024);

    DDSketch capacity;
    std::vector<double> too_many;
    const double gamma = 1.01 / 0.99;
    for (int index = 0; index < 1025; ++index) {
        too_many.push_back(std::pow(gamma, index * 2));
    }
    bool caught = false;
    try {
        capacity.add_batch(too_many.data(), too_many.size());
    } catch (const std::invalid_argument&) { caught = true; }
    assert(caught && "Sparse bucket capacity must be enforced");
    assert(capacity.count() == 0 && "Capacity rejection must be transactional");
    std::cout << "test_sparse_store_bounds passed\n";
}

int main() {
    test_constructor_validation();
    test_ddsketch_nan_inf_validation();
    test_nan_and_inf_handling();
    test_negative_zero_counts();
    test_cardinality();
    test_merge_mismatch();
    test_overflow_guards();
    test_sparse_store_bounds();

    std::cout << "All tests passed successfully.\n";
    return 0;
}
