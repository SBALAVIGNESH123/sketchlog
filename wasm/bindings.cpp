#include <emscripten/bind.h>
#include <emscripten/val.h>
#include "sketchlog.hpp"
#include <string>
#include <vector>

using namespace emscripten;

namespace {

// Helper to convert JS array to std::vector<double>
std::vector<double> extract_double_array(val v) {
    if (!v.hasOwnProperty("length")) {
        throw std::invalid_argument("Input must be an array or typed array");
    }
    int l = v["length"].as<int>();
    if (l < 0) {
        throw std::invalid_argument("Negative length property");
    }
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

void streamlog_add_event(
        sketchlog::StreamLog& self, const std::string& name, int64_t count) {
    self.add_event(name, count);
}

int64_t streamlog_event_count(
        const sketchlog::StreamLog& self, const std::string& name) {
    return self.event_count(name);
}

val streamlog_to_dict(const sketchlog::StreamLog& self) {
    auto lat = self.get_latency_state();
    auto uni = self.get_uniques_state();
    auto ev = self.get_events_state();

    val latency = val::object();
    latency.set("alpha", lat.alpha);
    latency.set("zero_count", std::to_string(lat.zero_count));
    latency.set("count", std::to_string(lat.count));

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
            pos.set(std::to_string(lat.pos_indices[i]), std::to_string(lat.pos_bins[i]));
        }
    }
    latency.set("positive", pos);

    val neg = val::object();
    for (size_t i = 0; i < lat.neg_bins.size(); ++i) {
        if (lat.neg_bins[i] > 0) {
            neg.set(std::to_string(lat.neg_indices[i]), std::to_string(lat.neg_bins[i]));
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
    events.set("total", std::to_string(ev.total_count));
    val table = val::array();
    for (size_t d = 0; d < ev.depth; ++d) {
        val row = val::array();
        for (size_t w = 0; w < ev.width; ++w) {
            row.set(w, std::to_string(ev.table[d * ev.width + w]));
        }
        table.set(d, row);
    }
    events.set("table", table);

    val d = val::object();
    d.set("version", 1);
    d.set("total", std::to_string(self.total_events()));
    d.set("deterministic", false);
    d.set("latency", latency);
    d.set("uniques", uniques);
    d.set("events", events);

    return d;
}

val streamlog_stats(const sketchlog::StreamLog& self) {
    auto s = self.stats();
    val obj = val::object();
    obj.set("events", std::to_string(s.events));
    obj.set("memory_bytes", std::to_string(s.memory_bytes));
    obj.set("memory_kb", s.memory_kb);
    obj.set("latency_p50", s.latency_p50);
    obj.set("latency_p99", s.latency_p99);
    obj.set("latency_p999", s.latency_p999);
    obj.set("unique_count", std::to_string(s.unique_count));
    return obj;
}

} // namespace

EMSCRIPTEN_BINDINGS(sketchlog_wasm) {
    class_<sketchlog::StreamLog>("StreamLog")
        .constructor<double, uint8_t, size_t, size_t>()
        .function("add_latency", &sketchlog::StreamLog::add_latency)
        .function("add_batch", &streamlog_add_batch)
        .function("percentile", &sketchlog::StreamLog::percentile)
        .function("p50", &sketchlog::StreamLog::p50)
        .function("p95", &sketchlog::StreamLog::p95)
        .function("p99", &sketchlog::StreamLog::p99)
        .function("p999", &sketchlog::StreamLog::p999)
        .function("count_greater_than", &sketchlog::StreamLog::count_greater_than)
        .function("latency_count", &sketchlog::StreamLog::latency_count)

        .function("add_event", &streamlog_add_event)
        .function("event_count", &streamlog_event_count)

        .function("add_unique_string", select_overload<void(const std::string&)>(&sketchlog::StreamLog::add_unique))
        .function("add_unique_int", select_overload<void(uint64_t)>(&sketchlog::StreamLog::add_unique))
        .function("unique_count", &sketchlog::StreamLog::unique_count)

        .function("total_events", &sketchlog::StreamLog::total_events)
        .function("memory_bytes", &sketchlog::StreamLog::memory_bytes)
        .function("memory_kb", &sketchlog::StreamLog::memory_kb)
        .function("reset", &sketchlog::StreamLog::reset)
        .function("merge", &sketchlog::StreamLog::merge)
        .function("to_dict", &streamlog_to_dict)
        .function("stats", &streamlog_stats);
}
