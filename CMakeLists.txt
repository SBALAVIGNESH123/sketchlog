cmake_minimum_required(VERSION 3.14)
project(SketchLog CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# Core sketch libraries
add_library(ddsketch src/ddsketch.cpp)
target_include_directories(ddsketch PUBLIC include)

add_library(hyperloglog src/hyperloglog.cpp)
target_include_directories(hyperloglog PUBLIC include)

add_library(countmin src/countmin.cpp)
target_include_directories(countmin PUBLIC include)

# Unified sketchlog library
add_library(sketchlog src/sketchlog.cpp)
target_include_directories(sketchlog PUBLIC include)
target_link_libraries(sketchlog PUBLIC ddsketch hyperloglog countmin)

# Scale benchmark
add_executable(benchmark_scale benchmarks/benchmark_scale.cpp)
target_link_libraries(benchmark_scale PRIVATE sketchlog)
target_include_directories(benchmark_scale PRIVATE include)
