# Formal Error Guarantees

SketchLog combines three algorithms with published mathematical bounds. Those
bounds apply under the preconditions below; implementation limits, hash
assumptions, and windowing semantics are part of the contract.

---

## DDSketch — Percentile Estimation

**Guarantee:** For any quantile q, the estimated value v' satisfies:

$$|v' - v| \leq \alpha \cdot |v|$$

where alpha is the `relative_accuracy` parameter (default 0.01 = 1%).

**Source:** Theorem 1 in [Masson et al., 2019](https://arxiv.org/abs/1908.10693)

**Boundary conditions:**
- Holds for all distributions (no distributional assumptions)
- Holds for any quantile q in (0, 1)
- Does NOT hold for q = 0 (min) or q = 1 (max) — those are tracked exactly
- Value must be nonzero (zero values stored separately)
- Error is *relative*, not absolute — small values have small absolute error
- Each positive and negative store accepts at most 1,024 occupied buckets.
  Ingestion or merge fails transactionally rather than silently collapsing
  buckets when that limit would be exceeded.

**Memory:** At most 1,024 positive and 1,024 negative bucket entries, plus
constant metadata. Actual byte usage depends on the backend and allocator.

**Merge:** Bucket-wise addition preserves the relative error bound.
Proven in Theorem 2 of the same paper. No accuracy degradation under merge.

---

## HyperLogLog — Cardinality Estimation

**Guarantee:** The estimated cardinality n' satisfies:

$$\text{Standard Error} = \frac{1.04}{\sqrt{m}}$$

where m = 2^p registers (p = precision parameter, default 10, so m = 1024).

**Default configuration:** SE = 1.04 / sqrt(1024) = 3.25%

**Source:** Theorem 1 in [Flajolet et al., 2007](https://algo.inria.fr/flajolet/Publications/FlFuGaMe07.pdf)

**Boundary conditions:**
- Assumes hash function approximates uniform distribution (we use FNV-1a + murmur finalizer)
- Small range correction (linear counting) applied when many registers are zero
- Large range correction removed — not needed for 64-bit hashes (per HLL++ paper)
- Error is *probabilistic*, not worst-case — ~68% of estimates fall within 1 SE

**Memory:** The register payload is exactly `m = 2^p` bytes (1,024 bytes by
default). Container and object overhead is additional and backend-dependent.

**Merge:** Register-wise max. Standard HLL merge operation.
Does not degrade accuracy — merged estimate has same SE as single-stream estimate.

---

## Count-Min Sketch — Frequency Estimation

**Guarantee:** For any item, the estimated frequency f' satisfies:

$$f \leq f' \leq f + \varepsilon \cdot N$$

where:
- f = true frequency
- N = total count of all items
- epsilon = e / width (e = Euler's number)
- This holds with probability >= 1 - delta, where delta = e^(-depth)

**Default configuration:**
- width = 2048, depth = 5
- epsilon = 2.718 / 2048 = 0.00133
- delta = e^(-5) = 0.0067 (99.3% confidence)

**Source:** Theorem 1 in [Cormode & Muthukrishnan, 2005](https://dimacs.rutgers.edu/~graham/pubs/papers/cm-full.pdf)

**Boundary conditions:**
- CMS does not underestimate for accepted positive updates
- CMS **may overestimate** by up to epsilon * N
- Overestimation increases with total event count N
- Works for any item type (strings, integers)
- Hash quality matters — we use murmur3 finalizer with deterministic seeds
- Counters are signed 64-bit values. An update or merge that would overflow is
  rejected transactionally.

**Memory:** width * depth * 8 bytes. Default: 2048 * 5 * 8 = 81,920 bytes = 80 KB.

**Merge:** Cell-wise addition. Preserves epsilon/delta guarantees exactly.

---

## Combined Memory Budget

| Sketch | Bound or formula | Default payload |
|--------|------------------|-----------------|
| DDSketch | At most 2 × 1,024 occupied bucket entries | Depends on occupied buckets and backend |
| HyperLogLog | `2^p` register bytes | 1 KiB |
| Count-Min Sketch | `width × depth × 8` counter bytes | 80 KiB |

`memory_breakdown()` reports SketchLog's backend-specific estimate. Memory is
bounded by configuration and occupied DDSketch buckets, not by the number of
accepted events. Process RSS also includes interpreter/runtime, allocator,
server, and SDK overhead that this method does not report.

---

## Merge Safety

Within matching configurations and representable counter/bucket limits, the
three merge operations are:
- **Commutative:** merge(A, B) = merge(B, A)
- **Associative:** merge(merge(A, B), C) = merge(A, merge(B, C))
- **Accuracy-preserving:** merged result has same error bounds as single-stream

This means you can safely:
- Merge across distributed workers
- Merge in any order
- Chain merges repeatedly

**Requirement:** All instances must have identical configuration
(alpha, precision, width, depth). Mismatched configs raise `ValueError`.
Capacity or counter overflow raises without mutating the destination.

---

## Merge Algebra (Commutative Monoid)

The underlying bucket addition and register maximum operations have the usual
commutative-monoid algebra. The concrete API is a **checked partial operation**:
configuration mismatch, occupied-bucket capacity, or integer overflow can
reject a merge.

**Definition:** Let `S` be the set of all StreamLog instances with identical
configuration. Define `⊕` as the merge operation. Then:

For merges that remain in the representable domain:

1. **Associativity:** `(A ⊕ B) ⊕ C = A ⊕ (B ⊕ C)`
2. **Commutativity:** `A ⊕ B = B ⊕ A`
3. **Identity:** An empty StreamLog `ε` satisfies `A ⊕ ε = A`

**Per-sketch proof:**

- **DDSketch:** Bucket-wise addition is commutative and associative over integers.
  Min/max tracking uses min/max, which are commutative and associative.
  Proven in Theorem 2 of Masson et al., 2019.

- **HyperLogLog:** Register-wise max is commutative and associative.
  `max(max(a, b), c) = max(a, max(b, c))`. Standard HLL merge property.

- **Count-Min Sketch:** Cell-wise addition inherits commutativity and
  associativity from integer addition. Total count is additive.

**Why this matters:** A distributed system may reduce compatible, representable
states in any order. It must still surface and handle rejected merges; the
implementation never wraps counters or silently drops excess buckets.

Property and integration tests exercise identity, associativity,
commutativity, cross-backend merge, overflow, and capacity rejection. Tests are
evidence about this implementation, not a mathematical proof.

---

## Known Bias and Edge Cases

These are the situations where each sketch produces less accurate results.
Understanding these is essential for production use.

### DDSketch Bias

| Condition | Effect | Severity |
|-----------|--------|----------|
| Very small values (< 1e-9) | Bucket index becomes very negative | Accuracy remains bounded while occupied-bucket capacity remains available |
| Zero values | Stored separately in zero_count, not in logarithmic buckets | None — exact |
| All-duplicate stream | Single bucket, accuracy depends on bucket boundary | Low — tested at 100K duplicates, error < 2% |
| More than 1,024 occupied buckets per sign | Update or merge is rejected transactionally | Increase relative accuracy or partition streams |
| Negative latencies | Stored in separate negative bucket store | None — handled correctly |

### HyperLogLog Bias

| Condition | Effect | Severity |
|-----------|--------|----------|
| Very low cardinality (< 2.5m) | Linear counting correction activates | Low — bias-corrected |
| High cardinality (> 2^32) | Not applicable with 64-bit hashes (no large range correction needed) | None |
| Hash collisions | Different items mapping to same register index | Low — probabilistic, averaged out |
| Skewed input (many duplicates) | Does not affect accuracy — duplicates don't change registers | None |
| Low precision (p=4, 16 registers) | SE = 1.04/√16 = 26% error | High — use p≥10 |

### Count-Min Sketch Bias

| Condition | Effect | Severity |
|-----------|--------|----------|
| Many distinct items vs narrow width | Overestimation increases | Medium — use wider table |
| Skewed frequency (Zipf) | Heavy hitters accurate, rare items overestimated | Expected behavior |
| Hash collisions across rows | Still overestimates (CMS is always ≥ true count) | By design |
| Very large total count N | Additive error bound εN grows with N | Expected — increase width to compensate |

**Key rule:** For accepted positive updates, CMS estimates are never below the
true count. Tests exercise this invariant with deliberately collision-prone
tables.

---

## Windowed Correctness

`WindowedStreamLog` maintains N rotating sub-sketches (buckets), each covering
`window/N` seconds. Correctness properties:

### Time Expiry

Events added at time `t` are guaranteed to expire by time `t + window + bucket_duration`.
The maximum staleness is one bucket duration (`window/N`), not the full window.

**Proof:** When `_rotate()` advances, it resets the oldest bucket. The oldest
bucket's age is at most `window` (N buckets × bucket_duration). After rotation,
the expired bucket's data is permanently gone.

### Memory Bound

Total memory = `N × single_sketch_size`. This is constant regardless of:
- Total events processed
- Burst traffic patterns
- Distribution changes over time

**Verified:** `test_chaos_windowing` bounds memory after bursts, expiry, and
reactivation; deterministic window tests cover idle reset and ring wraparound.

### Merge Correctness Across Windows

Queries merge all active (non-expired) buckets into a temporary sketch.
For compatible states that remain within the checked representable domain,
merge order does not change the result. Bucket rotation still defines which
events are active at the instant of the query.

### Thread Safety

All write and read operations hold a lock. The lock is per-WindowedStreamLog
instance, not global. This means:
- Multiple WindowedStreamLog instances can operate independently
- A single instance serializes writes correctly under multi-thread access

**Verified:** `test_concurrency_profiling` covers 1, 2, 4, 8, and 16 writer
threads; `test_thread_safety_built_in` covers concurrent windowed writes.

---

## DriftSketch — Anomaly Drift Detection

`DriftSketch` is a windowed correlation drift detector built on top of StreamLog.
It is **not** a causal debugging system.

### What it detects

| Query | Answer | Supported |
|-------|--------|-----------|
| "Did redis latency increase?" | Current vs previous window p99 | Yes |
| "Did error_rate and redis move together?" | Drift correlation score | Yes |
| "Did redis *cause* the error spike?" | Causal attribution | No |
| "What was the request path for error X?" | Trace reconstruction | No |

### What it guarantees

- **Drift inputs** use DDSketch percentile estimates with configured relative
  accuracy. Detection itself remains threshold- and sample-dependent; there is
  no universal false-positive or false-negative guarantee.
- **Correlation scores** measure co-occurrence of drift direction and magnitude.
  Score of 1.0 = both metrics drifted identically. Score of -1.0 = opposite drift.
- **Memory** scales as `O(dimensions)` with configuration-dependent per-stream
  memory.
- **Thread safety** — all operations are lock-protected.

### What it does NOT guarantee

- **Causality.** Correlation does not imply causation. Two metrics drifting
  together does not mean one caused the other.
- **Event identity.** Individual events are destroyed. You cannot reconstruct
  "what happened at 3:42am."
- **Trace reconstruction.** No parent-child spans, no request lineage,
  no correlation IDs.
- **Window sensitivity.** Results depend on window size. A 1-minute window
  catches short spikes; a 1-hour window smooths them out. Choose carefully.

### Correct positioning

**What DriftSketch is:**
- Windowed correlation drift detector over compressed metrics
- Detects co-occurring metric changes in constant memory

**What DriftSketch is not:**
- Not a causal debugging system
- Not a root cause analysis engine
- Not a tracing replacement
