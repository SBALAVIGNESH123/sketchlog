#include "sketchlog.hpp"

#include <iostream>
#include <random>
#include <chrono>
#include <iomanip>
#include <cmath>
#include <vector>
#include <algorithm>
#include <numeric>

int main() {
    const uint64_t TOTAL_EVENTS = 100'000'000;  // 100M events
    const size_t CHECK_INTERVAL = 1'000'000;    // Check every 1M

    std::cout << "══════════════════════════════════════════════════════════════" << std::endl;
    std::cout << "  sketchlog: Infinite-Scale Metrics in Constant Memory" << std::endl;
    std::cout << "  Processing " << TOTAL_EVENTS / 1'000'000 << " million events" << std::endl;
    std::cout << "══════════════════════════════════════════════════════════════" << std::endl;

    // Create StreamLog with default settings
    sketchlog::StreamLog log;
    
    // Random generators for realistic latency distribution (lognormal)
    std::mt19937_64 rng(42);
    std::lognormal_distribution<double> latency_dist(2.0, 1.0);  // ~7ms median, long tail
    std::uniform_int_distribution<uint64_t> user_dist(0, 1'000'000);

    // Ground truth for validation: collect sample percentiles
    std::vector<double> ground_truth_sample;
    ground_truth_sample.reserve(10'000'000);  // 10M samples for ground truth

    std::cout << std::endl;
    std::cout << std::setw(14) << "Events"
              << std::setw(12) << "Mem (KB)"
              << std::setw(12) << "p50 (ms)"
              << std::setw(12) << "p99 (ms)"
              << std::setw(14) << "Unique Users"
              << std::setw(14) << "Events/sec" << std::endl;
    std::cout << std::string(78, '-') << std::endl;

    auto start = std::chrono::high_resolution_clock::now();

    for (uint64_t i = 0; i < TOTAL_EVENTS; ++i) {
        double lat = latency_dist(rng);
        uint64_t user_id = user_dist(rng);

        log.add_latency(lat);
        log.add_event("api_call");
        log.add_unique(user_id);

        // Collect ground truth sample (first 10M)
        if (i < 10'000'000) {
            ground_truth_sample.push_back(lat);
        }

        if ((i + 1) % CHECK_INTERVAL == 0) {
            auto now = std::chrono::high_resolution_clock::now();
            double elapsed = std::chrono::duration<double>(now - start).count();
            double rate = (i + 1) / elapsed;

            std::cout << std::fixed << std::setprecision(2)
                      << std::setw(14) << (i + 1)
                      << std::setw(12) << log.memory_kb()
                      << std::setw(12) << log.p50()
                      << std::setw(12) << log.p99()
                      << std::setw(14) << log.unique_count()
                      << std::setw(14) << std::setprecision(0) << rate << std::endl;
        }
    }

    auto end = std::chrono::high_resolution_clock::now();
    double total_sec = std::chrono::duration<double>(end - start).count();

    // ─── Accuracy validation ─────────────────────────────────────────
    std::cout << std::endl;
    std::cout << "══════════════════════════════════════════════════════════════" << std::endl;
    std::cout << "  ACCURACY VALIDATION (ground truth from 10M samples)" << std::endl;
    std::cout << "══════════════════════════════════════════════════════════════" << std::endl;

    std::sort(ground_truth_sample.begin(), ground_truth_sample.end());
    size_t n = ground_truth_sample.size();

    auto exact_percentile = [&](double q) -> double {
        size_t idx = static_cast<size_t>(q * (n - 1));
        return ground_truth_sample[idx];
    };

    struct PCheck { const char* label; double q; };
    PCheck checks[] = {
        {"p50",  0.50},
        {"p90",  0.90},
        {"p95",  0.95},
        {"p99",  0.99},
        {"p99.9", 0.999},
    };

    // Re-create a sketch with just the ground truth data for fair comparison
    sketchlog::DDSketch validation_sketch(0.01);
    for (double v : ground_truth_sample) {
        validation_sketch.add(v);
    }

    std::cout << std::endl;
    std::cout << std::setw(8) << "Metric"
              << std::setw(14) << "Exact"
              << std::setw(14) << "SketchLog"
              << std::setw(14) << "Error %" << std::endl;
    std::cout << std::string(50, '-') << std::endl;

    for (auto& c : checks) {
        double exact = exact_percentile(c.q);
        double approx = validation_sketch.quantile(c.q);
        double err = std::abs(approx - exact) / exact * 100.0;
        std::cout << std::fixed << std::setprecision(4)
                  << std::setw(8) << c.label
                  << std::setw(14) << exact
                  << std::setw(14) << approx
                  << std::setw(13) << std::setprecision(2) << err << "%" << std::endl;
    }

    // ─── Cardinality validation ──────────────────────────────────────
    std::cout << std::endl;
    std::cout << "  Cardinality: estimated " << log.unique_count()
              << " unique users (actual: ~1,000,000)" << std::endl;
    double card_err = std::abs((double)log.unique_count() - 1'000'000.0) / 1'000'000.0 * 100.0;
    std::cout << "  Error: " << std::fixed << std::setprecision(2) << card_err << "%" << std::endl;

    // ─── Final stats ─────────────────────────────────────────────────
    std::cout << std::endl;
    std::cout << "══════════════════════════════════════════════════════════════" << std::endl;
    std::cout << "  RESULTS" << std::endl;
    std::cout << "══════════════════════════════════════════════════════════════" << std::endl;
    std::cout << "  Events processed:  " << TOTAL_EVENTS / 1'000'000 << " million" << std::endl;
    std::cout << "  Total memory:      " << std::fixed << std::setprecision(2) 
              << log.memory_kb() << " KB" << std::endl;
    std::cout << "  Total time:        " << std::setprecision(1) << total_sec << " seconds" << std::endl;
    std::cout << "  Throughput:        " << std::setprecision(0) 
              << (TOTAL_EVENTS / total_sec) << " events/sec" << std::endl;
    std::cout << "  p99 latency:       " << std::setprecision(4) << log.p99() << " ms" << std::endl;
    std::cout << "  Unique users:      " << log.unique_count() << std::endl;
    std::cout << "══════════════════════════════════════════════════════════════" << std::endl;

    return 0;
}
