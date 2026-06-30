# Changelog

## 1.2.1

### Fixed

- Prevent Windows checkpoint writers from being starved by concurrent same-path
  readers, while retaining bounded retries for readers in other processes.

## 1.2.0

Python 3.10–3.14 are supported. Python 3.9 reached upstream end of life and is
no longer an install target.

### Security

- Canonically validate all serialized state before native allocation.
- Restrict mesh peers to canonical allowlisted origins, disable redirects, cap
  membership, and require a cluster secret.
- Add namespace-scoped HTTP, SQL, aggregate, delete, and WebSocket authorization.
- Persist and gossip versioned deletion tombstones to prevent stale resurrection.
- Bound authenticated mesh requests and responses, split oversized anti-entropy
  exchanges into progressive chunks, and reject malformed/non-finite peer state.
- Cap durable local tombstones and atomically commit SQL state deletion with its
  tombstone so capacity or storage failure cannot create an unsafe delete.
- Scan containers before publication and sign image and Helm digests.

### Correctness and durability

- Replace unbounded DDSketch dense spans with bounded sparse stores.
- Make native batch ingestion transactional and correct for strided NumPy views.
- Align integer event keys and cross-backend merges.
- Use conservative threshold counts for SLO alert safety.
- Make file checkpoints flush/fsync/atomically replace and tolerate Windows
  reader-sharing windows.
- Bound storage locks and make eviction wait for durable persistence.
- Bound checkpoint and database decompression, reject trailing/truncated state,
  and retain the prior checkpoint when a replacement exceeds the state limit.

### Features

- Add executable anomaly comparison, SLO recommendation, sketch diffing, live
  Streaming SQL, and precision-safe WASM merge contracts.
- Publishable TypeScript, React, WASM, and in-repository Go modules.
- Authenticated SDKs with credential providers, redirect and response-size
  defenses, sanitized errors, close methods, and real transport retry tests.
- Runnable Linux/BCC collector with merge export and health/readiness endpoints.
- StatefulSet-based Helm mesh mode and an `autoscaling/v2` HPA.

### Quality and operations

- Generate and verify the OpenAPI contract.
- Serialize WebSocket 64-bit counters without JavaScript precision loss and
  drive dashboard cardinality from the HyperLogLog estimate.
- Add web lint/build/component gates, branch coverage and critical subsystem
  floors, fail-closed benchmark gates, Helm variants, WASM Node/browser smoke
  tests, immutable Action pins, SBOM/provenance, and public-registry smoke tests.
- Synchronize version 1.2.0 across all coupled artifacts and rewrite operations
  and feature documentation to match executable behavior.
