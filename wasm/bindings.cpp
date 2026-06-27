#include <emscripten/bind.h>
#include <emscripten/val.h>
#include "sketchlog.hpp"
#include <string>
#include <vector>

using namespace emscripten;

namespace {

// Helper to convert JS array to std::vector<double>
std::vector<double> extract_double_array(val v) {
    if (!v.isArray()) {
        return {};
    }
    int l = v["length"].as<int>();
    std::vector<double> res;
    res.reserve(l);
    for (int i = 0; i < l; ++i) {
        res.push_back(v[i].as<double>());
    }
    return res;
}

void streamlog_add_batch(sketchlog::StreamLog& self, val js_array) {
    std::vector<double> vec = extract_double_array(js_array);
    self.add_batch(vec.data(), vec.size());
}

val streamlog_to_dict(const sketchlog::StreamLog& self) {
    auto lat = self.get_latency_state();
    auto uni = self.get_uniques_state();
    auto ev = self.get_events_state();

    val latency = val::object();
    latency.set("alpha", lat.alpha);
    latency.set("zero_count", (double)lat.zero_count);
    latency.set("count", (double)lat.count);
    
    if (lat.count > 0) {
        latency.set("min", lat.min_value);
        latency.set("max", lat.max_value);
    } else {
        latency.set("min", val::null());
        latency.set("max", val::null());
    }

    val pos = val::object();
    for (size_t i = 0; i < lat.pos_bins.size(); ++i) {
        if (lat.pos_bins[i] > 0) {
            pos.set(std::to_string(lat.pos_offset + static_cast<int>(i)), (double)lat.pos_bins[i]);
        }
    }
    latency.set("positive", pos);

    val neg = val::object();
    for (size_t i = 0; i < lat.neg_bins.size(); ++i) {
        if (lat.neg_bins[i] > 0) {
            neg.set(std::to_string(lat.neg_offset + static_cast<int>(i)), (double)lat.neg_bins[i]);
        }
    }
    latency.set("negative", neg);

    val uniques = val::object();
    uniques.set("precision", uni.precision);
    val regs = val::array();
    for (size_t i = 0; i < uni.registers.size(); ++i) {
        regs.set(i, uni.registers[i]);
    }
    uniques.set("registers", regs);

    val events = val::object();
    events.set("width", (double)ev.width);
    events.set("depth", (double)ev.depth);
    events.set("total", (double)ev.total_count);
    val table = val::array();
    for (size_t d = 0; d < ev.depth; ++d) {
        val row = val::array();
        for (size_t w = 0; w < ev.width; ++w) {
            row.set(w, (double)ev.table[d * ev.width + w]);
        }
        table.set(d, row);
    }
    events.set("table", table);

    val d = val::object();
    d.set("version", 1);
    d.set("total", (double)self.total_events());
    d.set("deterministic", false);
    d.set("latency", latency);
    d.set("uniques", uniques);
    d.set("events", events);
    
    return d;
}

val streamlog_stats(const sketchlog::StreamLog& self) {
    auto s = self.stats();
    val obj = val::object();
    obj.set("events", (double)s.events);
    obj.set("memory_bytes", (double)s.memory_bytes);
    obj.set("memory_kb", s.memory_kb);
    obj.set("latency_p50", s.latency_p50);
    obj.set("latency_p99", s.latency_p99);
    obj.set("latency_p999", s.latency_p999);
    obj.set("unique_count", (double)s.unique_count);
    return obj;
}

} // namespace

EMSCRIPTEN_BINDINGS(sketchlog_wasm) {
    class_<sketchlog::StreamLog>("StreamLog")
        .constructor<double, uint8_t, size_t, size_t>()
        .def("add_latency", &sketchlog::StreamLog::add_latency)
        .def("add_batch", &streamlog_add_batch)
        .def("percentile", &sketchlog::StreamLog::percentile)
        .def("p50", &sketchlog::StreamLog::p50)
        .def("p95", &sketchlog::StreamLog::p95)
        .def("p99", &sketchlog::StreamLog::p99)
        .def("p999", &sketchlog::StreamLog::p999)
        .def("count_greater_than", &sketchlog::StreamLog::count_greater_than)
        .def("latency_count", &sketchlog::StreamLog::latency_count)
        
        .def("add_event", &sketchlog::StreamLog::add_event)
        .def("event_count", &sketchlog::StreamLog::event_count)
        
        .def("add_unique_string", select_overload<void(const std::string&)>(&sketchlog::StreamLog::add_unique))
        .def("add_unique_int", select_overload<void(uint64_t)>(&sketchlog::StreamLog::add_unique))
        .def("unique_count", &sketchlog::StreamLog::unique_count)
        
        .def("total_events", &sketchlog::StreamLog::total_events)
        .def("memory_bytes", &sketchlog::StreamLog::memory_bytes)
        .def("memory_kb", &sketchlog::StreamLog::memory_kb)
        .def("reset", &sketchlog::StreamLog::reset)
        .def("merge", &sketchlog::StreamLog::merge)
        .def("to_dict", &streamlog_to_dict)
        .def("stats", &streamlog_stats);
}
