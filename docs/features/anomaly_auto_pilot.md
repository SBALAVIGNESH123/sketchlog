# Anomaly comparison

SketchLog compares a current latency sketch with an explicit baseline using the
approximate two-sample Kolmogorov–Smirnov distance. The score is between 0 and
1. This is distribution drift detection, not causal diagnosis or a statistical
significance test.

```python
from sketchlog import StreamLog

baseline = StreamLog()
current = StreamLog()
baseline.add_batch([10, 11, 12, 13])
current.add_batch([90, 100, 110, 120])

score = current.anomaly_score(baseline)
changed = current.is_anomalous(baseline, sensitivity=0.2)
```

The HTTP endpoint requires the same explicit baseline:

```bash
curl "http://localhost:8000/v1/streams/current/anomaly?baseline_stream_id=baseline&sensitivity=0.2"
```

`SKETCHLOG_ANOMALY_SENSITIVITY` sets the server default and must be in `(0, 1]`.
Callers should calibrate it with representative traffic. Sparse samples,
quantization, and changing traffic mix can produce false positives or false
negatives. Sketch state is persisted only when `SKETCHLOG_DB_URI` is configured.
