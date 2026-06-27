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
            bool fast_path_used = false;

            try {
                if (py::isinstance<py::array_t<double>>(values)) {
                    auto arr = values.cast<py::array_t<double>>();
                    auto buf = arr.unchecked<1>();
                    for (py::ssize_t i = 0; i < buf.shape(0); i++) {
                        self.add(buf(i));
                    }
                    fast_path_used = true;
                }
            } catch (const py::error_already_set&) {
                // numpy not installed or cast failed, ignore and fallback
            }

            if (!fast_path_used) {
                for (auto item : py::iter(values)) {
                    self.add(item.cast<double>());
                }
            }
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
            bool fast_path_used = false;

            try {
                if (py::isinstance<py::array_t<double>>(values)) {
                    auto arr = values.cast<py::array_t<double>>();
                    auto buf = arr.unchecked<1>();
                    for (py::ssize_t i = 0; i < buf.shape(0); i++) {
                        self.add_latency(buf(i));
                    }
                    fast_path_used = true;
                }
            } catch (const py::error_already_set&) {
                // numpy not installed or cast failed, ignore and fallback
            }

            if (!fast_path_used) {
                for (auto item : py::iter(values)) {
                    self.add_latency(item.cast<double>());
                }
            }
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
                    pos[py::str(std::to_string(lat.pos_offset + static_cast<int>(i)))] = lat.pos_bins[i];
                }
            }
            latency["positive"] = pos;

            py::dict neg;
            for (size_t i = 0; i < lat.neg_bins.size(); ++i) {
                if (lat.neg_bins[i] > 0) {
                    neg[py::str(std::to_string(lat.neg_offset + static_cast<int>(i)))] = lat.neg_bins[i];
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
            if (!d.contains("version") || d["version"].cast<int>() != 1) {
                throw std::invalid_argument("Unsupported or missing serialization version");
            }

            py::dict lat_d = d["latency"].cast<py::dict>();
            py::dict uni_d = d["uniques"].cast<py::dict>();
            py::dict ev_d = d["events"].cast<py::dict>();

            sketchlog::StreamLog log(
                lat_d["alpha"].cast<double>(),
                uni_d["precision"].cast<uint8_t>(),
                ev_d["width"].cast<size_t>(),
                ev_d["depth"].cast<size_t>()
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
            int p_min = 0, p_max = 0;
            bool p_empty = true;
            for (auto item : pos) {
                int k = std::stoi(item.first.cast<py::str>().cast<std::string>());
                if (p_empty) { p_min = p_max = k; p_empty = false; }
                else { p_min = std::min(p_min, k); p_max = std::max(p_max, k); }
            }
            lat.pos_empty = p_empty;
            lat.pos_min_index = p_min;
            lat.pos_max_index = p_max;
            lat.pos_offset = p_empty ? 0 : p_min;
            if (!p_empty) {
                lat.pos_bins.resize(p_max - p_min + 1, 0);
                for (auto item : pos) {
                    lat.pos_bins[std::stoi(item.first.cast<py::str>().cast<std::string>()) - p_min] = item.second.cast<int64_t>();
                }
            }

            py::dict neg = lat_d["negative"].cast<py::dict>();
            int n_min = 0, n_max = 0;
            bool n_empty = true;
            for (auto item : neg) {
                int k = std::stoi(item.first.cast<py::str>().cast<std::string>());
                if (n_empty) { n_min = n_max = k; n_empty = false; }
                else { n_min = std::min(n_min, k); n_max = std::max(n_max, k); }
            }
            lat.neg_empty = n_empty;
            lat.neg_min_index = n_min;
            lat.neg_max_index = n_max;
            lat.neg_offset = n_empty ? 0 : n_min;
            if (!n_empty) {
                lat.neg_bins.resize(n_max - n_min + 1, 0);
                for (auto item : neg) {
                    lat.neg_bins[std::stoi(item.first.cast<py::str>().cast<std::string>()) - n_min] = item.second.cast<int64_t>();
                }
            }
            log.set_latency_state(lat);

            // Uniques
            sketchlog::HyperLogLog::State uni;
            uni.precision = uni_d["precision"].cast<uint8_t>();
            py::list regs = uni_d["registers"].cast<py::list>();
            for (auto r : regs) uni.registers.push_back(r.cast<uint8_t>());
            log.set_uniques_state(uni);

            // Events
            sketchlog::CountMinSketch::State ev;
            ev.width = ev_d["width"].cast<size_t>();
            ev.depth = ev_d["depth"].cast<size_t>();
            ev.total_count = ev_d["total"].cast<int64_t>();
            py::list table = ev_d["table"].cast<py::list>();
            for (auto r : table) {
                py::list row = r.cast<py::list>();
                for (auto c : row) ev.table.push_back(c.cast<int64_t>());
            }
            log.set_events_state(ev);

            log.set_total_events(d["total"].cast<uint64_t>());

            return log;
        })
        .def("__repr__", [](const sketchlog::StreamLog& self) {
            auto s = self.stats();
            return "StreamLog(events=" + std::to_string(s.events) +
                   ", memory=" + std::to_string(int(s.memory_kb * 100) / 100.0)
                   .substr(0, 6) + " KB)";
        });
}
