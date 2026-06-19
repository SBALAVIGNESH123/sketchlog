cmake_minimum_required(VERSION 3.14)
project(SketchLog CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

if(MSVC)
    set(CXX_WARNINGS /W4 /EHsc)
else()
    set(CXX_WARNINGS -Wall -Wextra)
endif()

# Core sketch libraries
add_library(ddsketch src/ddsketch.cpp)
target_include_directories(ddsketch PUBLIC include)
target_compile_options(ddsketch PRIVATE ${CXX_WARNINGS})

add_library(hyperloglog src/hyperloglog.cpp)
target_include_directories(hyperloglog PUBLIC include)
target_compile_options(hyperloglog PRIVATE ${CXX_WARNINGS})

add_library(countmin src/countmin.cpp)
target_include_directories(countmin PUBLIC include)
target_compile_options(countmin PRIVATE ${CXX_WARNINGS})

# Unified sketchlog library
add_library(sketchlog src/sketchlog.cpp)
target_include_directories(sketchlog PUBLIC include)
target_link_libraries(sketchlog PUBLIC ddsketch hyperloglog countmin)
target_compile_options(sketchlog PRIVATE ${CXX_WARNINGS})

# Scale benchmark
add_executable(benchmark_scale benchmarks/benchmark_scale.cpp)
target_link_libraries(benchmark_scale PRIVATE sketchlog)
target_include_directories(benchmark_scale PRIVATE include)
target_compile_options(benchmark_scale PRIVATE ${CXX_WARNINGS})

# Tests
enable_testing()
add_executable(tests_cpp tests/test_cpp.cpp)
target_link_libraries(tests_cpp PRIVATE sketchlog)
target_include_directories(tests_cpp PRIVATE include)
target_compile_options(tests_cpp PRIVATE ${CXX_WARNINGS})
add_test(NAME cpp_tests COMMAND tests_cpp)
