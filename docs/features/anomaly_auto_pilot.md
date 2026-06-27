# Anomaly Auto-Pilot

The **Anomaly Auto-Pilot** is a zero-config statistical anomaly detection engine built directly into SketchLog. It constantly monitors your ingested sketches for meaningful statistical drift without requiring you to manually set thresholds, standard deviations, or rolling windows.

## How It Works

Traditional alerting requires static thresholds (e.g., `latency > 500ms`). But what if your baseline shifts because of a new deployment? Auto-Pilot compares the current temporal sketch against the historically decayed sketch using distribution overlap metrics (such as the Kolmogorov-Smirnov distance approximation for sketches).

If the overlap drops below a critical confidence bound, it fires an anomaly event.

## Usage

You can query the anomaly status of any stream directly through the API or SDK:

### Python SDK

```python
from sketchlog import StreamLog

log = StreamLog()
# ... ingest data ...

# Check for anomalies
is_anomalous = log.is_anomalous()
anomaly_score = log.anomaly_score()

print(f"Anomaly Detected: {is_anomalous} (Score: {anomaly_score})")
```

### HTTP API

```bash
curl -X GET "http://localhost:8000/v1/namespaces/default/streams/my-service/anomaly"
```

## Configuration

You can tune the sensitivity of the Auto-Pilot by adjusting the `anomaly_sensitivity` parameter when initializing a `StreamLog` or when starting the SketchLog server:

```bash
SKETCHLOG_ANOMALY_SENSITIVITY=0.95 uvicorn sketchlog.server:app
```

- `0.99`: Very strict. Only fires on massive distribution shifts.
- `0.95`: Recommended default. Balances false positives with fast detection.
- `0.90`: Very sensitive. Will fire on minor deviations.
