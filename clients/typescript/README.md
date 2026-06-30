# `@sketchlog/client`

TypeScript HTTP client for SketchLog.

```bash
npm install @sketchlog/client
```

```typescript
import { SketchLogClient } from "@sketchlog/client";

const client = new SketchLogClient({
  endpoint: "https://metrics.example",
  authTokenProvider: async () => obtainRotatedToken(),
  timeoutMs: 5000,
  maxRetries: 3,
});

await client.ingestEvents("api", {
  latencies: [42.5],
  events: { request: 1 },
});

await client.close();
```

Static credentials can be supplied with `authToken`. Authentication is sent in
`X-SketchLog-Auth-Token`, never in the URL.

GET, PUT, and DELETE requests retry transport failures, HTTP 429, and 5xx
responses with bounded exponential backoff and jitter. Ingestion POSTs are not
retried automatically because replay could double-count events.

See the [SDK documentation](https://sbalavignesh123.github.io/sketchlog/sdks/)
for configuration and conformance details.
