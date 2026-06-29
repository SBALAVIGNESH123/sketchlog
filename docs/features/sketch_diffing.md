# Sketch diffing

`SketchDiff` compares the occupied latency buckets of two streams. It reports
an approximate KS statistic, approximate 1D Wasserstein distance, both CDFs,
and an ASCII plot. Complexity is `O((b1 + b2) log(b1 + b2))` in occupied
buckets, not event count.

```python
from sketchlog import StreamLog, SketchDiff

baseline = StreamLog.load("baseline.json")
current = StreamLog()
current.add_batch([20, 30, 40])

report = current.diff(baseline)
print(report.ks_statistic)
print(report.wasserstein_distance)
print(report.to_dict())
```

HTTP supports the canonical `baseline_stream_id` parameter and the backwards
compatible `baseline` alias:

```bash
curl "http://localhost:8000/v1/namespaces/default/streams/current/diff?baseline_stream_id=baseline"
```

Both streams must contain latency observations.
