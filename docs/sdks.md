# HTTP SDKs

## TypeScript

```bash
npm install @sketchlog/client@1.2.2
```

```typescript
import { SketchLogClient } from "@sketchlog/client";

const client = new SketchLogClient({
  endpoint: "https://metrics.example",
  authTokenProvider: async () => obtainRotatedToken(),
  maxRetries: 3,
  timeoutMs: 5000,
});
await client.ingestEvents("api", { latencies: [42.5] });
await client.close();
```

`authToken` is also available for static credentials. The token is sent in
`X-SketchLog-Auth-Token` and is never included in SDK-generated error text.

## Go

The Go module lives in this repository:

```bash
go get github.com/SBALAVIGNESH123/sketchlog/clients/go@v1.2.2
```

```go
client := sketchlog.NewClient(sketchlog.ClientOptions{
    Endpoint: "https://metrics.example",
    AuthTokenProvider: func(ctx context.Context) (string, error) {
        return obtainRotatedToken(ctx)
    },
})
defer client.Close()
err := client.IngestEvents(ctx, "api", sketchlog.EventBatch{
    Latencies: []float64{42.5},
})
```

Both SDKs pool connections, honor cancellation/timeouts, and retry idempotent
GET/PUT/DELETE operations after transport errors, 429, or 5xx using exponential
backoff and jitter. Ingestion POSTs are not automatically retried because the
protocol has no idempotency key and replay could double-count.

Conformance CI runs with authentication enabled and covers valid, missing, and
invalid credentials, HTTP retries, refused connections, cancellation, and
basic ingestion.
