// sketchlog pybind11 bindings
// Exposes the C++ hot path to Python while keeping the API familiar.

#include <pybind11/pybind11.h>
#include <pybind11/stl.h>
#include <pybind11/numpy.h>

#include "sketchlog.hpp"
#include "ddsketch.hpp"
#include "hyperloglog.hpp"
#include "countmin.hpp"

#include <string>
#include <vector>

namespace py = pybind11;

PYBIND11_MODULE(_sketchlog_cpp, m) {
    m.doc() = "sketchlog C++ core — constant-memory streaming analytics engine";

    // ═══════════════════════════════════════════════════════════════════
    // DDSketch
    // ═══════════════════════════════════════════════════════════════════

    py::class_<sketchlog::DDSketch>(m, "DDSketch",
        "Logarithmic quantile sketch. O(1) memory for any percentile.")
        .def(py::init<double>(), py::arg("relative_accuracy") = 0.01)
        .def("add", py::overload_cast<double>(&sketchlog::DDSketch::add),
             py::arg("value"), "Add a single observation.")
        .def("add_batch", [](sketchlog::DDSketch& self, py::object values) {
            sketchlog::DDSketch temp = self;
            bool fast_path_used = false;

            try {
                if (py::isinstance<py::array_t<double>>(values)) {
                    auto arr = values.cast<py::array_t<double>>();
                    auto buf = arr.unchecked<1>();
                    for (py::ssize_t i = 0; i < buf.shape(0); i++) {
                        temp.add(buf(i));
                    }
                    fast_path_used = true;
                }
            } catch (const py::error_already_set&) {
                // numpy not installed or cast failed, ignore and fallback
            }

            if (!fast_path_used) {
                for (auto item : py::iter(values)) {
                    temp.add(item.cast<double>());
                }
            }
            self = std::move(temp);
        }, py::arg("values"), "Bulk-add values from an iterable (fast path for numpy arrays).")
        .def("quantile", &sketchlog::DDSketch::quantile, py::arg("q"))
        .def("min", &sketchlog::DDSketch::min)
        .def("max", &sketchlog::DDSketch::max)
        .def("count", &sketchlog::DDSketch::count)
        .def("memory_bytes", &sketchlog::DDSketch::memory_bytes)
        .def("merge", &sketchlog::DDSketch::merge, py::arg("other"))
        .def("reset", &sketchlog::DDSketch::reset);

    // ═══════════════════════════════════════════════════════════════════
    // HyperLogLog
    // ═══════════════════════════════════════════════════════════════════

    py::class_<sketchlog::HyperLogLog>(m, "HyperLogLog",
        "Probabilistic cardinality estimator. O(1) memory.")
        .def(py::init<uint8_t>(), py::arg("precision") = 14)
        .def("add_string", [](sketchlog::HyperLogLog& self, const std::string& s) {
            self.add_string(s.data(), s.size());
        }, py::arg("item"), "Add a string item.")
        .def("add_int", [](sketchlog::HyperLogLog& self, uint64_t id) {
            uint8_t bytes[8];
            for (int i = 0; i < 8; ++i) {
                bytes[i] = static_cast<uint8_t>((id >> (i * 8)) & 0xFF);
            }
            self.add(bytes, 8);
        }, py::arg("id"), "Add a uint64 item.")
        .def("estimate", &sketchlog::HyperLogLog::estimate)
        .def("memory_bytes", &sketchlog::HyperLogLog::memory_bytes)
        .def("precision", &sketchlog::HyperLogLog::precision)
        .def("merge", &sketchlog::HyperLogLog::merge, py::arg("other"))
        .def("reset", &sketchlog::HyperLogLog::reset);

    // ═══════════════════════════════════════════════════════════════════
    // CountMinSketch
    // ═══════════════════════════════════════════════════════════════════

    py::class_<sketchlog::CountMinSketch>(m, "CountMinSketch",
        "Probabilistic frequency estimator. Never underestimates.")
        .def(py::init<size_t, size_t>(),
             py::arg("width") = 2048, py::arg("depth") = 5)
        .def("add_string", [](sketchlog::CountMinSketch& self,
                               const std::string& s, int64_t count) {
            self.add_string(s.data(), s.size(), count);
        }, py::arg("item"), py::arg("count") = 1)
        .def("add_int", [](sketchlog::CountMinSketch& self,
                            uint64_t key, int64_t count) {
            self.add(key, count);
        }, py::arg("key"), py::arg("count") = 1)
        .def("estimate_string", [](const sketchlog::CountMinSketch& self,
                                    const std::string& s) -> int64_t {
            return self.estimate_string(s.data(), s.size());
        }, py::arg("item"))
        .def("estimate_int", [](const sketchlog::CountMinSketch& self,
                                 uint64_t key) -> int64_t {
            return self.estimate(key);
        }, py::arg("key"))
        .def("total_count", &sketchlog::CountMinSketch::total_count)
        .def("width", &sketchlog::CountMinSketch::width)
        .def("depth", &sketchlog::CountMinSketch::depth)
        .def("memory_bytes", &sketchlog::CountMinSketch::memory_bytes)
        .def("merge", &sketchlog::CountMinSketch::merge, py::arg("other"))
        .def("reset", &sketchlog::CountMinSketch::reset);

    // ═══════════════════════════════════════════════════════════════════
    // StreamLog (main API)
    // ═══════════════════════════════════════════════════════════════════

    py::class_<sketchlog::StreamLog>(m, "StreamLog",
        "Streaming approximate analytics engine in constant memory.\n\n"
        "Tracks latency percentiles (DDSketch), event frequency (Count-Min),\n"
        "and cardinality (HyperLogLog) over unlimited events using ~93 KB.")
        .def(py::init<double, uint8_t, size_t, size_t>(),
             py::arg("relative_accuracy") = 0.01,
             py::arg("hll_precision") = 10,
             py::arg("cms_width") = 2048,
             py::arg("cms_depth") = 5)

        // ─── Latency ─────────────────────────────────────────────────
        .def("add_latency", &sketchlog::StreamLog::add_latency,
             py::arg("value"), "Add a latency measurement.")
        .def("add_batch", [](sketchlog::StreamLog& self,
                              py::object values) {
            sketchlog::StreamLog temp = self;
            bool fast_path_used = false;

            try {
                if (py::isinstance<py::array_t<double>>(values)) {
                    auto arr = values.cast<py::array_t<double>>();
                    auto buf = arr.unchecked<1>();
                    for (py::ssize_t i = 0; i < buf.shape(0); i++) {
                        temp.add_latency(buf(i));
                    }
                    fast_path_used = true;
                }
            } catch (const py::error_already_set&) {
                // numpy not installed or cast failed, ignore and fallback
            }

            if (!fast_path_used) {
                for (auto item : py::iter(values)) {
                    temp.add_latency(item.cast<double>());
                }
            }
            self = std::move(temp);
        }, py::arg("values"),
           "Bulk-add latency values from an iterable (fast path for numpy arrays).")
        .def("percentile", &sketchlog::StreamLog::percentile, py::arg("q"))
        .def("p50", &sketchlog::StreamLog::p50)
        .def("p95", &sketchlog::StreamLog::p95)
        .def("p99", &sketchlog::StreamLog::p99)
        .def("p999", &sketchlog::StreamLog::p999)

        // ─── Events ──────────────────────────────────────────────────
        .def("add_event", &sketchlog::StreamLog::add_event,
             py::arg("name"), py::arg("count") = 1)
        .def("event_count", &sketchlog::StreamLog::event_count,
             py::arg("name"))

        // ─── Cardinality ─────────────────────────────────────────────
        .def("add_unique", py::overload_cast<const std::string&>(
             &sketchlog::StreamLog::add_unique), py::arg("item"))
        .def("add_unique", py::overload_cast<uint64_t>(
             &sketchlog::StreamLog::add_unique), py::arg("id"))
        .def("unique_count", &sketchlog::StreamLog::unique_count)

        // ─── System ──────────────────────────────────────────────────
        .def("total_events", &sketchlog::StreamLog::total_events)
        .def("memory_bytes", &sketchlog::StreamLog::memory_bytes)
        .def("memory_kb", &sketchlog::StreamLog::memory_kb)
        .def("reset", &sketchlog::StreamLog::reset)
        .def("merge", &sketchlog::StreamLog::merge, py::arg("other"))
        .def("stats", [](const sketchlog::StreamLog& self) {
            auto s = self.stats();
            return py::make_tuple(s.events, s.memory_bytes, s.memory_kb,
                                  s.latency_p50, s.latency_p99, s.latency_p999, s.unique_count);
        })

        .def("__repr__", [](const sketchlog::StreamLog& self) {
            auto s = self.stats();
            return "StreamLog(events=" + std::to_string(s.events) +
                   ", memory=" + std::to_string(int(s.memory_kb * 100) / 100.0)
                   .substr(0, 6) + " KB)";
        });
}
