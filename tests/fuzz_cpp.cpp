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

        if (size > 1 && data[0] % 4 == 0) {
            uint16_t des_len;
            if (size > 3) {
                std::memcpy(&des_len, data + 1, sizeof(uint16_t));
                i += 3;
                if (i + des_len <= size) {
                    sketchlog::StreamLog::deserialize(data + i, des_len);
                    i += des_len;
                }
            }
        } else if (size > 5 && data[0] % 4 == 1) {
            // parameterized constructor
            double rel_acc;
            std::memcpy(&rel_acc, data + 1, sizeof(double));
            i += 9;
            if (!std::isnan(rel_acc) && !std::isinf(rel_acc) && rel_acc > 0 && rel_acc < 1.0) {
                sketchlog::StreamLog parameterized(rel_acc);
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
