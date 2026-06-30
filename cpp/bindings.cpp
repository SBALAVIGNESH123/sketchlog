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
#include <limits>

namespace py = pybind11;

template <typename Sketch>
void add_python_batch(Sketch& self, const py::object& values) {
    // Check the buffer protocol first so ordinary Python iterables do not
    // trigger NumPy's lazy import machinery.
    if (PyObject_CheckBuffer(values.ptr())) {
        auto raw = py::array::ensure(values);
        if (!raw) {
            throw std::invalid_argument("Batch buffer cannot be interpreted as an array");
        }
        if (raw.ndim() != 1) {
            throw std::invalid_argument("NumPy batch input must be one-dimensional");
        }
        using ContiguousDoubles = py::array_t<
            double, py::array::c_style | py::array::forcecast>;
        auto arr = ContiguousDoubles::ensure(values);
        if (!arr) {
            throw std::invalid_argument(
                "NumPy batch input cannot be converted to contiguous float64 values");
        }
        auto info = arr.request();
        self.add_batch(static_cast<const double*>(info.ptr),
                       static_cast<size_t>(info.shape[0]));
        return;
    }

    std::vector<double> vec;
    for (auto item : py::iter(values)) {
        vec.push_back(item.cast<double>());
    }
    self.add_batch(vec.data(), vec.size());
}

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
        .def("add_batch", &add_python_batch<sketchlog::DDSketch>,
             py::arg("values"), "Bulk-add values from an iterable (fast path for numpy arrays).")
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
        .def("add_batch", &add_python_batch<sketchlog::StreamLog>,
             py::arg("values"),
           "Bulk-add latency values from an iterable (fast path for numpy arrays).")
        .def("percentile", &sketchlog::StreamLog::percentile, py::arg("q"))
        .def("p50", &sketchlog::StreamLog::p50)
        .def("p95", &sketchlog::StreamLog::p95)
        .def("p99", &sketchlog::StreamLog::p99)
        .def("p999", &sketchlog::StreamLog::p999)
        .def("count_greater_than", &sketchlog::StreamLog::count_greater_than, py::arg("threshold"))

        // ─── Events ──────────────────────────────────────────────────
        .def("add_event",
             py::overload_cast<const std::string&, int64_t>(
                 &sketchlog::StreamLog::add_event),
             py::arg("name"), py::arg("count") = 1)
        .def("add_event",
             py::overload_cast<uint64_t, int64_t>(
                 &sketchlog::StreamLog::add_event),
             py::arg("key"), py::arg("count") = 1)
        .def("event_count",
             py::overload_cast<const std::string&>(
                 &sketchlog::StreamLog::event_count, py::const_),
             py::arg("name"))
        .def("event_count",
             py::overload_cast<uint64_t>(
                 &sketchlog::StreamLog::event_count, py::const_),
             py::arg("key"))

        // ─── Cardinality ─────────────────────────────────────────────
        .def("add_unique", py::overload_cast<const std::string&>(
             &sketchlog::StreamLog::add_unique), py::arg("item"))
        .def("add_unique", py::overload_cast<uint64_t>(
             &sketchlog::StreamLog::add_unique), py::arg("id"))
        .def("unique_count", &sketchlog::StreamLog::unique_count)

        // ─── System ──────────────────────────────────────────────────
        .def("total_events", &sketchlog::StreamLog::total_events)
        .def("latency_count", &sketchlog::StreamLog::latency_count)
        .def("memory_bytes", &sketchlog::StreamLog::memory_bytes)
        .def("memory_kb", &sketchlog::StreamLog::memory_kb)
        .def("reset", &sketchlog::StreamLog::reset)
        .def("merge", &sketchlog::StreamLog::merge, py::arg("other"))
        .def("stats", [](const sketchlog::StreamLog& self) {
            auto s = self.stats();
            return py::make_tuple(s.events, s.memory_bytes, s.memory_kb,
                                  s.latency_p50, s.latency_p99, s.latency_p999, s.unique_count);
        })
        // ─── Serialization & Memory Parity ───────────────────────────
        .def("memory_breakdown", [](const sketchlog::StreamLog& self) {
            py::dict d;
            d["latency"] = self.latency_memory_bytes();
            d["events"] = self.events_memory_bytes();
            d["uniques"] = self.uniques_memory_bytes();
            d["total"] = self.memory_bytes();
            return d;
        })
        .def("to_dict", [](const sketchlog::StreamLog& self) {
            auto lat = self.get_latency_state();
            auto uni = self.get_uniques_state();
            auto ev = self.get_events_state();

            py::dict latency;
            latency["alpha"] = lat.alpha;
            latency["zero_count"] = lat.zero_count;
            latency["count"] = lat.count;
            if (lat.count > 0) {
                latency["min"] = lat.min_value;
                latency["max"] = lat.max_value;
            } else {
                latency["min"] = py::none();
                latency["max"] = py::none();
            }

            py::dict pos;
            for (size_t i = 0; i < lat.pos_bins.size(); ++i) {
                if (lat.pos_bins[i] > 0) {
                    pos[py::str(std::to_string(lat.pos_indices[i]))] = lat.pos_bins[i];
                }
            }
            latency["positive"] = pos;

            py::dict neg;
            for (size_t i = 0; i < lat.neg_bins.size(); ++i) {
                if (lat.neg_bins[i] > 0) {
                    neg[py::str(std::to_string(lat.neg_indices[i]))] = lat.neg_bins[i];
                }
            }
            latency["negative"] = neg;

            py::dict uniques;
            uniques["precision"] = uni.precision;
            py::list regs;
            for (auto r : uni.registers) regs.append((int)r);
            uniques["registers"] = regs;

            py::dict events;
            events["width"] = ev.width;
            events["depth"] = ev.depth;
            events["total"] = ev.total_count;
            py::list table;
            for (size_t d = 0; d < ev.depth; ++d) {
                py::list row;
                for (size_t w = 0; w < ev.width; ++w) {
                    row.append(ev.table[d * ev.width + w]);
                }
                table.append(row);
            }
            events["table"] = table;

            py::dict d;
            d["version"] = 1;
            d["total"] = self.total_events();
            d["deterministic"] = false;
            d["latency"] = latency;
            d["uniques"] = uniques;
            d["events"] = events;
            return d;
        })
        .def_static("from_dict", [](py::dict d) {
            // Run the same canonical validator used by the pure-Python
            // backend before constructing or allocating any C++ sketch state.
            // This prevents backend-specific accept/reject drift and bounds
            // dimensions/bucket counts before native state is allocated.
            py::object validated = py::module_::import("sketchlog.facade")
                .attr("_PythonStreamLog").attr("from_dict")(d);
            d = validated.attr("to_dict")().cast<py::dict>();

            if (d["deterministic"].cast<bool>()) {
                throw std::invalid_argument(
                    "Deterministic serialized state must use the Python backend");
            }

            py::dict lat_d = d["latency"].cast<py::dict>();
            py::dict uni_d = d["uniques"].cast<py::dict>();
            py::dict ev_d = d["events"].cast<py::dict>();

            const size_t width = ev_d["width"].cast<size_t>();
            const size_t depth = ev_d["depth"].cast<size_t>();
            constexpr size_t MAX_CMS_CELLS = 1'000'000;
            if (depth == 0 || width == 0
                    || width > MAX_CMS_CELLS / depth) {
                throw std::invalid_argument(
                    "CountMinSketch dimensions exceed bounded capacity");
            }

            sketchlog::StreamLog log(
                lat_d["alpha"].cast<double>(),
                uni_d["precision"].cast<uint8_t>(),
                width,
                depth
            );

            // Latency
            sketchlog::DDSketch::State lat;
            lat.alpha = lat_d["alpha"].cast<double>();
            lat.zero_count = lat_d["zero_count"].cast<int64_t>();
            lat.count = lat_d["count"].cast<size_t>();
            if (lat.count > 0) {
                lat.min_value = lat_d["min"].cast<double>();
                lat.max_value = lat_d["max"].cast<double>();
            } else {
                lat.min_value = 0.0;
                lat.max_value = 0.0;
            }

            py::dict pos = lat_d["positive"].cast<py::dict>();
            auto parse_bucket_key = [](py::handle value) -> int {
                if (py::isinstance<py::int_>(value)) {
                    return value.cast<int>();
                }
                if (!py::isinstance<py::str>(value)) {
                    throw std::invalid_argument(
                        "DDSketch bucket keys must be integers or canonical integer strings");
                }
                const std::string text = value.cast<std::string>();
                size_t consumed = 0;
                int result = 0;
                try {
                    result = std::stoi(text, &consumed);
                } catch (const std::exception&) {
                    throw std::invalid_argument("Invalid DDSketch bucket key");
                }
                if (consumed != text.size() || std::to_string(result) != text) {
                    throw std::invalid_argument(
                        "DDSketch bucket keys must be canonical integer strings");
                }
                return result;
            };

            std::vector<std::pair<int, int64_t>> pos_entries;
            pos_entries.reserve(pos.size());
            for (auto item : pos) {
                pos_entries.emplace_back(
                    parse_bucket_key(item.first), item.second.cast<int64_t>());
            }
            std::sort(pos_entries.begin(), pos_entries.end());
            lat.pos_empty = pos_entries.empty();
            lat.pos_min_index = lat.pos_empty ? 0 : pos_entries.front().first;
            lat.pos_max_index = lat.pos_empty ? 0 : pos_entries.back().first;
            for (const auto& [index, count] : pos_entries) {
                lat.pos_indices.push_back(index);
                lat.pos_bins.push_back(count);
            }

            py::dict neg = lat_d["negative"].cast<py::dict>();
            std::vector<std::pair<int, int64_t>> neg_entries;
            neg_entries.reserve(neg.size());
            for (auto item : neg) {
                neg_entries.emplace_back(
                    parse_bucket_key(item.first), item.second.cast<int64_t>());
            }
            std::sort(neg_entries.begin(), neg_entries.end());
            lat.neg_empty = neg_entries.empty();
            lat.neg_min_index = lat.neg_empty ? 0 : neg_entries.front().first;
            lat.neg_max_index = lat.neg_empty ? 0 : neg_entries.back().first;
            for (const auto& [index, count] : neg_entries) {
                lat.neg_indices.push_back(index);
                lat.neg_bins.push_back(count);
            }
            log.set_latency_state(lat);

            // Uniques
            sketchlog::HyperLogLog::State uni;
            uni.precision = uni_d["precision"].cast<uint8_t>();
            py::list regs = uni_d["registers"].cast<py::list>();
            const size_t expected_registers = size_t{1} << uni.precision;
            if (static_cast<size_t>(py::len(regs)) != expected_registers) {
                throw std::invalid_argument("HyperLogLog register count mismatch");
            }
            for (auto r : regs) {
                const int value = r.cast<int>();
                if (value < 0 || value > 64 - uni.precision + 1) {
                    throw std::invalid_argument("HyperLogLog register value out of range");
                }
                uni.registers.push_back(static_cast<uint8_t>(value));
            }
            log.set_uniques_state(uni);

            // Events
            sketchlog::CountMinSketch::State ev;
            ev.width = ev_d["width"].cast<size_t>();
            ev.depth = ev_d["depth"].cast<size_t>();
            ev.total_count = ev_d["total"].cast<int64_t>();
            py::list table = ev_d["table"].cast<py::list>();
            if (static_cast<size_t>(py::len(table)) != depth) {
                throw std::invalid_argument("CountMinSketch row count mismatch");
            }
            for (auto r : table) {
                py::list row = r.cast<py::list>();
                if (static_cast<size_t>(py::len(row)) != width) {
                    throw std::invalid_argument("CountMinSketch column count mismatch");
                }
                for (auto c : row) {
                    ev.table.push_back(c.cast<int64_t>());
                }
            }
            log.set_events_state(ev);

            const uint64_t total = d["total"].cast<uint64_t>();
            if (ev.total_count < 0
                    || std::numeric_limits<uint64_t>::max() - lat.count
                       < static_cast<uint64_t>(ev.total_count)
                    || total != lat.count + static_cast<uint64_t>(ev.total_count)) {
                throw std::invalid_argument(
                    "StreamLog total does not match latency and event totals");
            }
            log.set_total_events(total);

            return log;
        })
        .def("__repr__", [](const sketchlog::StreamLog& self) {
            auto s = self.stats();
            return "StreamLog(events=" + std::to_string(s.events) +
                   ", memory=" + std::to_string(int(s.memory_kb * 100) / 100.0)
                   .substr(0, 6) + " KB)";
        });
}
