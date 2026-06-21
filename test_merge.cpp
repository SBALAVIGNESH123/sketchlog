#include <iostream>
#include "sketchlog.hpp"
using namespace sketchlog;
int main() {
StreamLog log1; log1.add_event("x", 9223372036854775807LL / 2); log1.add_latency(1.0);
StreamLog log2; log2.add_event("y", 9223372036854775807LL / 2 + 2); log2.add_latency(2.0);
std::cout << "Before: " << log1.p99() << std::endl;
try { log1.merge(log2); } catch (const std::exception& e) { std::cout << "Caught: " << e.what() << std::endl; }
std::cout << "After: " << log1.p99() << std::endl; return 0; }
