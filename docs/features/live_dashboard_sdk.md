# React dashboard package

```bash
npm install @sketchlog/react
```

The published package exports `SketchLogProvider`, `useSketchLog`,
`QuantileHeatmap`, `CDFCurve`, and `CardinalitySparkline`.

```tsx
import {
  SketchLogProvider,
  QuantileHeatmap,
  CardinalitySparkline,
} from "@sketchlog/react";

export function Dashboard() {
  return (
    <SketchLogProvider url="wss://metrics.example/v1/streams/api/ws">
      <QuantileHeatmap />
      <CardinalitySparkline />
    </SketchLogProvider>
  );
}
```

The browser WebSocket API cannot set the SDK's HTTP authentication header.
For browser deployments, an HTTPS gateway should set a `sketchlog_auth` cookie
with `Secure`, `HttpOnly`, a narrow path/domain, and an appropriate `SameSite`
policy. Do not put long-lived credentials in WebSocket URLs. The server also
accepts `X-SketchLog-Auth-Token` from non-browser WebSocket clients.

The provider reconnects with bounded attempts and exposes connection and error
state. WebSocket counters are decimal strings so values above JavaScript's
safe-integer range are not silently truncated; chart components convert them
to approximate numbers only for rendering. `CardinalitySparkline` plots the
server's HyperLogLog cardinality estimate. CI tests successful state updates,
server error transitions, and the WebSocket contract.
