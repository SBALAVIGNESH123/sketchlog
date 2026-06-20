# Fuzzer Regression Corpus

This directory is passed to the C++ fuzzer (uzz_cpp.cpp) during every CI run.
The fuzzer treats every file in this directory as a seed input and will execute it deterministically before beginning its randomized fuzzing loop.

## Handling Fuzzer Crashes
If a pull request introduces a regression and causes libFuzzer to crash (e.g. Segfault, Heap-buffer-overflow, OOM, Timeout):
1. Navigate to the failed GitHub Actions CI run.
2. Download the uzzer-crashes artifact zip attached to the run.
3. Extract the zip to find the minimized crash payload (e.g., crash-12345abcde).
4. Commit the crash file directly into this 	ests/corpus/ directory along with your bug fix.

By keeping historical crash seeds in this directory, we deterministically prevent regressions of known edge cases forever.
