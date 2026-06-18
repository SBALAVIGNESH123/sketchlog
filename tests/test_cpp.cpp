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

int main() {
    test_constructor_validation();
    test_ddsketch_nan_inf_validation();
    test_nan_and_inf_handling();
    test_negative_zero_counts();
    test_cardinality();
    test_merge_mismatch();

    std::cout << "All tests passed successfully.\n";
    return 0;
}
