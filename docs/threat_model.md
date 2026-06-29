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
*   **Malicious Serialized State**: Disk, database, mesh, and WASM merge state can be attacker-controlled.
    *   *Mitigation*: JSON state passes one canonical Python validator before any native allocation. Dimensions, bucket counts, register counts, table shape, extrema, totals, integer domains, and aggregate invariants are bounded and rechecked in C++.

## 2. Standalone Server

### Trust Boundaries
The FastAPI standalone server exposes a REST API over a network. **All incoming HTTP requests and network payloads are considered completely untrusted.**

### Threats & Mitigations
*   **Payload Exhaustion (DoS)**: An attacker sends a massive JSON payload (e.g., 5GB) to exhaust RAM.
    *   *Mitigation*: The `LimitUploadSize` ASGI middleware intercepts the request stream before JSON parsing. Public requests use `SKETCHLOG_MAX_REQUEST_BYTES` (1 MiB by default); authenticated mesh digest/sync requests use a separate bounded limit (40 MiB by default, 64 MiB maximum). Oversized bodies receive `413 Payload Too Large`.
*   **Hash Collision (DoS)**: An attacker crafts thousands of event keys designed to collide in the stream registry or sketching hash tables.
    *   *Mitigation*: The `StreamRegistry` enforces a strict process-wide LRU capacity (default 1000). Python's registry map uses process-randomized string hashing; Count-Min rows use independent deterministic seeds and strong avalanche hashing.
*   **State Corruption (Atomicity)**: A batch of events contains a value that causes an integer overflow halfway through processing, leaving the stream in an inconsistent state.
    *   *Mitigation*: Event-ingestion routes and both backends pre-flight counter and bucket capacity. A rejected batch returns `422 Unprocessable Entity` without partial state mutation.
*   **Cross-tenant access**: A valid caller attempts another namespace.
    *   *Mitigation*: Namespace-scoped tokens are enforced for HTTP, aggregate, SQL, delete, and WebSocket paths. An explicitly configured administrator token is the only built-in cross-namespace principal.
*   **Mesh SSRF/state resurrection**: Gossip advertises internal addresses, sends oversized state, or relays stale deleted state.
    *   *Mitigation*: Peer origins require an exact allowlist match, redirects and URL credentials are rejected, requests/responses and membership/state maps are capped, and versioned durable tombstones dominate stale snapshots.

## 3. Software Supply Chain

### Threats & Mitigations
*   **Dependency Compromise**: A third-party Python library (e.g., `fastapi`, `pydantic`) is compromised.
    *   *Mitigation*: We apply compatibility upper bounds to runtime server dependencies where appropriate, and use Dependabot plus GitHub Dependency Review to scan known vulnerabilities in third-party dependency trees.
*   **Release Artifact Compromise**: An attacker intercepts or alters the PyPI wheels or source distribution.
    *   *Mitigation*: All releases are built on ephemeral GitHub Actions runners. We generate SPDX SBOMs and GitHub OIDC provenance. Container images are scanned before publication, emitted with provenance/SBOM data, and image and Helm digests are keylessly signed with cosign.
*   **CI/CD Hijacking**: A malicious PR alters a GitHub Action script.
    *   *Mitigation*: All third-party GitHub Actions in our workflows are pinned to explicit, immutable commit SHAs. OpenSSF Scorecard continuously monitors our repository settings and workflow configurations.
