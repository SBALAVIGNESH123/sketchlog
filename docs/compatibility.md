# Compatibility Guarantees

SketchLog is infrastructure software. Production users rely on stability across the Python API, serialized state, and HTTP endpoints. This document defines our strict compatibility guarantees.

## Semantic Versioning

SketchLog follows [Semantic Versioning 2.0.0](https://semver.org/).

Given a version number `MAJOR.MINOR.PATCH`:

1.  **MAJOR** version increments indicate incompatible API or schema changes.
2.  **MINOR** version increments add functionality in a backward-compatible manner.
3.  **PATCH** version increments make backward-compatible bug fixes.

## Public vs. Internal API

Our public Python API is defined explicitly in `python/sketchlog/__init__.py` using the `__all__` export list.

**Public Symbols:**
*   `StreamLog`
*   `ThreadSafeStreamLog`
*   `WindowedStreamLog`
*   `DriftSketch`
*   `SQLStreamEngine`
*   `SketchDiff`
*   `Stats`
*   `DDSketch`
*   `HyperLogLog`
*   `CountMinSketch`
*   `EBPFCollector`

These symbols are covered by Semantic Versioning. Any breaking change to their signatures or documented behaviors requires a **MAJOR** version bump.

**Internal Symbols:**
Any symbol prefixed with an underscore (e.g., `_PythonStreamLog`, `_cpp`) is **internal**. We may break, rename, or remove internal symbols in **PATCH** or **MINOR** releases without warning. Do not rely on them in production code.

### Deprecation Timeline

If a public feature must be removed or fundamentally changed:
1.  We will issue a `DeprecationWarning` in Python for at least **one full MINOR release cycle**.
2.  The documentation will clearly indicate the deprecation and provide migration steps.
3.  The feature will only be removed in the subsequent **MAJOR** release.

## Serialization Contract

A core feature of SketchLog is saving and loading compressed metric state. Our
compatibility policy is that newer versions continue to read state serialized
by supported older versions (backward schema compatibility), subject to the
following rules:

1.  **Version Identifier:** All serialized JSON/dict payloads contain a `"version"` field (currently `1`).
2.  **Backward Compatibility:** If the schema evolves (e.g., to `"version": 2`), the library will seamlessly parse `"version": 1` payloads and upgrade them in memory.
3.  **Strict Rejection:** The library will explicitly reject payloads with an unsupported or missing `"version"` to prevent data corruption.
4.  **Breaking Schema Changes:** Dropping support for reading an older version of the schema is a **MAJOR** breaking change.

## OpenAPI Server Contract

The standalone SketchLog server provides an HTTP API documented via OpenAPI.

1.  **Stable Endpoints:** All documented endpoints are treated as public API.
2.  **Error Models:** Error responses follow a stable schema.
3.  **Breaking Changes:** Changing route paths, removing fields from responses, or adding strictly required fields to requests will trigger a **MAJOR** version bump.

## Platform Support Matrix

We guarantee compatibility and provide pre-compiled wheels for the following targets:

| Component | Support Level |
| :--- | :--- |
| **Python** | 3.10, 3.11, 3.12, 3.13, 3.14 |
| **OS/architecture** | Linux x86_64/aarch64, macOS x86_64/arm64, Windows AMD64 |
| **C++ ABI** | C++17 compliant compiler (GCC 9+, Clang 10+, MSVC 19.2X+) |

Dropping support for a Python minor version will only occur in a **MAJOR**
release or after that Python version reaches its official end of life. Python
3.9 support ended in SketchLog 1.2.0 after upstream security support ended.

32-bit Windows (`win32`) is not supported or published. Release CI constrains
Windows wheels to AMD64 and smoke-tests that every selected wheel imports the
native extension.
