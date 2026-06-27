# WASM Runtime

Because the core of SketchLog is written in highly portable C++, it has been compiled to WebAssembly (WASM) via Emscripten. The **WASM Runtime** allows you to execute the exact same O(1) compression engine directly at the edge or in the user's browser.

## Browser Deployment

Instead of sending thousands of raw interaction events over the network to a central backend (costing bandwidth and battery), you can sketch them on the client and periodically flush the compressed 93KB sketch.

### Installation

```bash
npm install @sketchlog/wasm
```

### Usage (React/JS)

```javascript
import { StreamLog } from '@sketchlog/wasm';

const log = new StreamLog();

// Ingest user interaction latencies locally
document.addEventListener('click', (e) => {
    const latency = calculateInteractionDelay();
    log.add_latency(latency);
});

// Flush to the backend every 60 seconds
setInterval(async () => {
    const payload = log.serialize();
    await fetch('https://api.yourdomain.com/v1/namespaces/frontend/streams/client-telemetry/merge', {
        method: 'POST',
        body: payload
    });
    log.reset();
}, 60000);
```

## Edge Compute (Cloudflare Workers / Vercel Edge)

The WASM runtime is fully compatible with Edge runtimes that lack traditional Node.js/Python file system access, making it perfect for aggregating CDN metrics before they hit your origin servers.
