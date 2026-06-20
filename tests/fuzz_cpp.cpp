#include <cstdint>
#include <cstddef>
#include "sketchlog.hpp"
#include <string>
#include <vector>

extern "C" int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size) {
    if (size == 0) return 0;
    
    // We treat the input as a sequence of operations
    sketchlog::StreamLog log;
    
    size_t i = 0;
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
    log.events();
    log.unique_count();
    if (log.events() > 0) {
        log.p50();
        log.p99();
    }
    
    return 0;
}

