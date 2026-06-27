# Live Dashboard SDK

The **Live Dashboard SDK** is a suite of pre-built React components and JavaScript utilities that allow you to embed real-time observability widgets directly into your own applications.

Instead of paying for heavy external BI tools or Grafana Cloud, you can integrate SketchLog's O(1) metrics directly into your internal admin panels, customer-facing dashboards, or developer portals.

## Installation

```bash
npm install @sketchlog/react
```

## Available Components

- `<LatencyHeatmap />`: Visualizes the latency distribution over time.
- `<PercentileGauge />`: A real-time speedometer for p99/p95 SLAs.
- `<TrafficHistogram />`: Shows frequency rates and counts.

## Usage Example

```javascript
import { SketchProvider, PercentileGauge } from '@sketchlog/react';

function App() {
  return (
    <SketchProvider endpoint="https://api.your-sketchlog.com" namespace="default">
      <div className="dashboard">
        <h2>API Performance</h2>
        <PercentileGauge 
          stream="api-gateway" 
          percentile={0.99} 
          warningThreshold={200} 
          criticalThreshold={500} 
        />
      </div>
    </SketchProvider>
  );
}
```

The components automatically connect to the SketchLog server via WebSockets, rendering live 60fps updates as new metrics flow in.
