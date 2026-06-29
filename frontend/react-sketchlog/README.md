# `@sketchlog/react`

React components for a live SketchLog stream.

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

For authenticated browser connections, have a same-site gateway set the
`sketchlog_auth` cookie with `HttpOnly`, `Secure`, and an appropriate
`SameSite` policy before opening the WebSocket. Tokens are not placed in URLs.
