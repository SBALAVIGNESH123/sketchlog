# SketchLog Go client

```bash
go get github.com/SBALAVIGNESH123/sketchlog/clients/go@v1.2.4
```

```go
client := sketchlog.NewClient(sketchlog.ClientOptions{
    Endpoint:  "https://metrics.example",
    AuthToken: os.Getenv("SKETCHLOG_TOKEN"),
})

err := client.IngestEvents(ctx, "api", sketchlog.EventBatch{
    Latencies: []float64{42.5},
    Events:    map[string]int64{"request": 1},
})
defer client.Close()
```

`AuthTokenProvider` supports rotated credentials. GET, PUT, and DELETE requests
retry transport failures, HTTP 429, and 5xx responses with bounded exponential
backoff and jitter. Ingestion POSTs are not retried automatically because replay
could double-count events.

See the [SDK documentation](https://sbalavignesh123.github.io/sketchlog/sdks/)
for the complete configuration and conformance contract.
