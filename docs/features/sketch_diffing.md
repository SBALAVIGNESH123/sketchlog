# Sketch Diffing

**Sketch Diffing** is a powerful feature that allows you to mathematically compare two statistical distributions (sketches) in O(1) time. This is particularly useful for A/B testing, Canary deployments, and performance regression testing.

## Visual & Programmatic Comparison

Instead of comparing raw averages, Sketch Diffing evaluates the entire probability distribution of the two datasets.

### API Usage

You can compare two streams directly via the HTTP API:

```bash
curl -X GET "http://localhost:8000/v1/namespaces/default/streams/api-v1/diff?baseline=api-v2"
```

The response provides a distribution delta, indicating exactly where the new version has regressed (e.g., "The p99 tail is 14% slower, but the p50 median is unchanged").

### Python SDK

```python
from sketchlog import StreamLog

v1_log = StreamLog.load("v1_baseline.sketch")
v2_log = StreamLog()
# ... ingest v2 test data ...

# Compare the distributions
diff_report = v2_log.diff(v1_log)

print(f"Distribution overlap: {diff_report.overlap_percentage}%")
print(f"Regression detected at p99: {diff_report.p99_regression}")
```

## Canary Analysis

In a CI/CD pipeline, you can use Sketch Diffing as an automated quality gate. By running traffic through both the Canary and the Baseline, you can dynamically abort deployments if the sketches drift by more than 5%.
