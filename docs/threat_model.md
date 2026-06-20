# Sketchlog Threat Model

This document outlines the security assumptions, trust boundaries, and mitigations implemented in Sketchlog.

## 1. Python and C++ Core Library

### Trust Boundaries
The core library operates entirely within the host application's memory space. It trusts the application developer to provide valid numeric inputs to the sketching algorithms.

### Threats & Mitigations
*   **Integer Overflow**: Sketch algorithms (like Count-Min Sketch) rely on fixed-size counters.
    *   *Mitigation*: The C++ backend enforces strict signed and unsigned bounds. Event insertion counts are pre-flighted against `UINT64_MAX` and `INT64_MAX` constraints before mutation to prevent undefined behavior or state wrapping.
*   **Out-of-Bounds Memory Access**: Sketches allocate specific capacities (e.g., hash arrays).
    *   *Mitigation*: Internal C++ arrays are bounded and indexed via safe modulo arithmetic on seeded hash values (MurmurHash3).
*   **Malicious Serialized State**: When restoring a sketch from disk or network, the state could be crafted to exploit deserializers.
    *   *Mitigation*: We do not use unsafe deserialization formats like Python's `pickle`. Sketches are serialized to raw byte streams or JSON structures with strict schema validation in C++.

## 2. Standalone Server

### Trust Boundaries
The FastAPI standalone server exposes a REST API over a network. **All incoming HTTP requests and network payloads are considered completely untrusted.**

### Threats & Mitigations
*   **Payload Exhaustion (DoS)**: An attacker sends a massive JSON payload (e.g., 5GB) to exhaust RAM.
    *   *Mitigation*: The `LimitUploadSize` ASGI middleware intercepts the request stream and enforces a strict `MAX_REQUEST_BYTES` (default 1MB). It returns `413 Payload Too Large` and drops the connection before the body is parsed.
*   **Hash Collision (DoS)**: An attacker crafts thousands of event keys designed to collide in the stream registry or sketching hash tables.
    *   *Mitigation*: The `StreamRegistry` enforces a strict LRU maximum capacity (default 1000). The underlying sketches use seeded MurmurHash3 to distribute keys uniformly.
*   **State Corruption (Atomicity)**: A batch of events contains a value that causes an integer overflow halfway through processing, leaving the stream in an inconsistent state.
    *   *Mitigation*: The `/ingest` endpoint pre-flights the total capacity of the stream against the sum of the incoming batch. If the batch would exceed the limit, it rejects the entire request with `422 Unprocessable Entity` *before* any state mutation occurs.

## 3. Software Supply Chain

### Threats & Mitigations
*   **Dependency Compromise**: A third-party Python library (e.g., `fastapi`, `pydantic`) is compromised.
    *   *Mitigation*: We strictly pin upper bounds for all dependencies in `pyproject.toml`. We use `dependabot` and the GitHub Dependency Review action to scan for known vulnerabilities in third-party trees.
*   **Release Artifact Compromise**: An attacker intercepts or alters the PyPI wheels or source distribution.
    *   *Mitigation*: All releases are built on ephemeral GitHub Actions runners. We generate SPDX SBOMs for every release, and use `actions/attest-build-provenance` to sign the artifacts using GitHub's OIDC identity provider, publishing signed build provenance attestations using GitHub artifact attestations and a SLSA provenance predicate.
*   **CI/CD Hijacking**: A malicious PR alters a GitHub Action script.
    *   *Mitigation*: All third-party GitHub Actions in our workflows are pinned to explicit, immutable commit SHAs. OpenSSF Scorecard continuously monitors our repository settings and workflow configurations.
