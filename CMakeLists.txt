cmake_minimum_required(VERSION 3.14)
project(SketchLog CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

if(MSVC)
    set(CXX_WARNINGS /W4 /EHsc)
else()
    set(CXX_WARNINGS -Wall -Wextra)
endif()

# Unified sketchlog library (header-only)
add_library(sketchlog INTERFACE)
target_include_directories(sketchlog INTERFACE include)
target_compile_options(sketchlog INTERFACE ${CXX_WARNINGS})

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

# Fuzzing
option(ENABLE_FUZZING "Build fuzz targets" OFF)
if(ENABLE_FUZZING)
    if(NOT MSVC)
        add_executable(fuzz_cpp tests/fuzz_cpp.cpp)
        target_link_libraries(fuzz_cpp PRIVATE sketchlog)
        target_include_directories(fuzz_cpp PRIVATE include)
        target_compile_options(fuzz_cpp PRIVATE ${CXX_WARNINGS} -fsanitize=fuzzer)
        target_link_options(fuzz_cpp PRIVATE -fsanitize=fuzzer)
    else()
        message(WARNING "Fuzzing is not supported on MSVC without explicit Clang integration.")
    endif()
endif()
