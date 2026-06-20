#include <cstdint>
#include <cstddef>
#include <cstring>
#include <cmath>
#include "sketchlog.hpp"
#include <string>
#include <vector>

extern "C" int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size) {
    if (size == 0) return 0;

    try {
        // Occasionally try to initialize the streamlog via deserialization or constructor
        sketchlog::StreamLog log;
        size_t i = 0;

        if (size > 21 && data[0] % 4 == 0) {
            // parameterized constructor
            double rel_acc;
            std::memcpy(&rel_acc, data + 1, sizeof(double));
            uint8_t hll_p = (data[9] % 15) + 4; // 4 to 18
            uint32_t cms_w, cms_d;
            std::memcpy(&cms_w, data + 10, sizeof(uint32_t));
            std::memcpy(&cms_d, data + 14, sizeof(uint32_t));
            cms_w = (cms_w % 100000) + 1;
            cms_d = (cms_d % 20) + 1;

            i += 18;
            if (!std::isnan(rel_acc) && !std::isinf(rel_acc) && rel_acc >= 0.0001 && rel_acc < 1.0) {
                sketchlog::StreamLog parameterized(rel_acc, hll_p, cms_w, cms_d);
                log = parameterized;
            }
        } else {
            i = 1;
        }

        while (i < size) {
            uint8_t op = data[i++] % 4; // 4 operations

            if (op == 0 && i + sizeof(double) <= size) {
                double val;
                std::memcpy(&val, data + i, sizeof(double));
                i += sizeof(double);
                if (!std::isnan(val) && !std::isinf(val) && val >= 0.0) {
                    log.add_latency(val);
                }
            } else if (op == 1 && i + 1 <= size) {
                uint8_t len = data[i++] % 32; // max string length 31
                if (i + len <= size) {
                    std::string str(reinterpret_cast<const char*>(data + i), len);
                    i += len;
                    log.add_event(str);
                }
            } else if (op == 2 && i + 1 <= size) {
                uint8_t len = data[i++] % 32;
                if (i + len <= size) {
                    std::string str(reinterpret_cast<const char*>(data + i), len);
                    i += len;
                    log.add_unique(str);
                }
            } else if (op == 3 && i + sizeof(double) <= size) {
                // merge with another sketch
                sketchlog::StreamLog other;
                double val;
                std::memcpy(&val, data + i, sizeof(double));
                i += sizeof(double);
                if (!std::isnan(val) && !std::isinf(val) && val >= 0.0) {
                    other.add_latency(val);
                    log.merge(other);
                }
            }
        }

        // Test observable functions
        log.total_events();
        log.unique_count();
        if (log.total_events() > 0) {
            log.p50();
            log.p99();
        }
    } catch (...) {
        // Ignore expected exceptions (e.g. overflow, invalid arguments)
    }

    return 0;
}
