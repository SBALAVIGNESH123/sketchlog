# Alerting & Drift Detection

SketchLog provides a state-of-the-art anomaly detection engine called **DriftSketch**, paired with a robust background **AlertEngine**. Together, they form a zero-configuration pipeline to detect statistical regressions in your metrics streams and alert your team via webhooks.

## The Problem with Thresholds

Traditional monitoring relies on static thresholds (e.g., "Alert if API latency > 500ms"). However, traffic patterns change over time, and static thresholds lead to alert fatigue (false positives) or missed degradations (false negatives).

SketchLog solves this by comparing distributions mathematically.

## DriftSketch: Statistical Anomaly Detection

`DriftSketch` continuously compares the *current* time window of a sketch against its *previous* time window. It reconstructs the Cumulative Distribution Functions (CDFs) of both windows and calculates the Wasserstein distance to quantify how far the distribution has shifted.

If your p50, p90, and p99 latencies all jump by 40%, `DriftSketch` detects this as a 40% drift, regardless of whether the absolute values are 10ms or 10,000ms.

### Basic Usage

You can query the drift manually without triggering any webhooks:

```python
from sketchlog.drift import DriftSketch

ds = DriftSketch(window="1m")

# Ingest data...
ds.add_batch("api_latency", [10, 12, 11])

# Check for drift (returns a list of DriftResult dictionaries)
drifts = ds.drift(threshold=0.1)  # Only return drifts > 10%

for d in drifts:
    print(f"Dimension {d['dimension']} drifted {d['direction']} by {d['drift_pct'] * 100}%!")
```

## AlertEngine: Reliable Notifications

While `DriftSketch` provides the mathematical anomaly detection, the `AlertEngine` orchestrates state tracking, deduplication, and reliable delivery.

### Alert Rules

You can configure `AlertRule` objects to define exactly when and how you want to be notified.

```python
from sketchlog.alerts import AlertEngine, AlertRule
from sketchlog.server import global_drift_sketch

engine = AlertEngine(global_drift_sketch, poll_interval=10.0)

rule = AlertRule(
    name="API Latency Spike",
    dimension="api_latency",
    min_drift_pct=20.0,       # Trigger if distribution shifts > 20%
    min_samples=100,          # Don't trigger if we have < 100 samples (prevents sparse data noise)
    sustained_windows=2,      # Require the drift to persist for 2 consecutive polls before alerting
    direction_filter="up",    # Only alert if latency gets worse (use None for bidirectional)
    webhook_url="https://hooks.slack.com/services/T000/B000/XXX",
    webhook_secret="super-secret-hmac-key"  # Optional: Signs the request payload
)

engine.add_rule(rule)
```

### State Machine & Reliability

The `AlertEngine` tracks the lifecycle of every rule:

1. **Deduplication**: If a metric continues to drift, you won't get spammed. A rule enters the `FIRING` state and goes on a 1-hour cooldown before it can alert again.
2. **Recovery Notifications**: When the distribution normalizes and the metric stops violating the rule, the engine automatically sends a "RESOLVED" webhook.
3. **Exponential Backoff**: If your webhook receiver is down (e.g., HTTP 503) or the network drops, the engine will retry delivery 3 times using exponential backoff.
4. **HMAC Signing**: If you provide a `webhook_secret`, the engine will sign the payload payload using HMAC-SHA256 and include it in the `X-Signature` HTTP header, ensuring you can verify the alert came from SketchLog.
