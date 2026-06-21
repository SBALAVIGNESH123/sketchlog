# SketchLog Client SDKs

SketchLog provides officially supported, production-ready client SDKs for TypeScript/JavaScript and Go.
Our SDKs are built entirely by hand to guarantee optimal connection pooling, jittered retry logic, and strict typed error handling—they are **not** blindly generated wrappers.

## Supported Languages

- **TypeScript / Node.js**: `@sketchlog/client`
- **Go**: `github.com/SBALAVIGNESH123/sketchlog-go`

## Quickstart

### TypeScript
```bash
npm install @sketchlog/client
```

```typescript
import { SketchLogClient, SketchLogError } from '@sketchlog/client';

const client = new SketchLogClient({
  endpoint: 'http://localhost:8999',
  maxRetries: 3,
  timeoutMs: 5000,
});

async function run() {
  try {
    await client.ingestEvents('my-stream', {
      latencies: [42.5, 55.1],
      uniques: ['user_abc'],
      events: { 'checkout_started': 1 }
    });
  } catch (err) {
    if (err instanceof SketchLogError) {
      console.error(`API Error ${err.status}: ${err.message}`);
    }
  }
}
```

### Go
```bash
go get github.com/SBALAVIGNESH123/sketchlog-go
```

```go
package main

import (
	"context"
	"log"

	"github.com/SBALAVIGNESH123/sketchlog-go"
)

func main() {
	client := sketchlog.NewClient(sketchlog.ClientOptions{
		Endpoint:   "http://localhost:8999",
		MaxRetries: 3,
	})

	ctx := context.Background()
	batch := sketchlog.EventBatch{
		Latencies: []float64{42.5, 55.1},
		Uniques:   []string{"user_abc"},
		Events:    map[string]int64{"checkout_started": 1},
	}

	err := client.IngestEvents(ctx, "my-stream", batch)
	if err != nil {
		if sketchErr, ok := err.(*sketchlog.SketchLogError); ok {
			log.Fatalf("API Error %d: %s", sketchErr.StatusCode, sketchErr.Message)
		}
		log.Fatalf("Network Error: %v", err)
	}
}
```

## Production Requirements

### 1. Exponential Backoff and Jitter
The SDKs automatically retry idempotent requests (like metrics aggregations and uniquely-keyed ingestions) upon receiving a `429 Too Many Requests` or any `5xx Server Error`. Retries are scheduled using an exponential backoff curve with fully randomized jitter to prevent thundering herds.

### 2. Timeouts and Cancellation
- **TypeScript**: The SDK supports `AbortController` signals to cancel requests mid-flight. If a request surpasses `timeoutMs`, an `AbortError` is automatically thrown and translated to a `408 Request Timeout` SketchLogError.
- **Go**: The SDK strictly honors `context.Context` for propagation, timeouts, and deadlines.

### 3. Connection Pooling
High-throughput data planes require persistent connections to avoid TLS handshake penalties.
- The TypeScript SDK utilizes `undici.Agent` for robust Keep-Alive pooling in Node.js.
- The Go SDK utilizes a custom-tuned `http.Transport` optimized for high `MaxIdleConnsPerHost`.

### 4. Typed Error Handling
SketchLog SDKs wrap raw network faults and HTTP failures into typed exception structs (`SketchLogError`). This enables reliable branching based on specific status codes (e.g. 413 PayloadTooLarge, 401 Unauthorized).

## Migration & Versioning Policy
SketchLog SDKs strictly adhere to **Semantic Versioning**.
- **v0.x**: The API and protocol are subject to minor breakages as we stabilize the data plane.
- **v1.x**: The OpenAPI contract is completely frozen. Backwards compatibility is fully guaranteed.
