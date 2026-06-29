# WebAssembly runtime

Install the versioned runtime:

```bash
npm install @sketchlog/wasm
```

Initialization is asynchronous and methods use camelCase:

```javascript
import { StreamLog } from "@sketchlog/wasm";

await StreamLog.init();
const log = new StreamLog();
log.addLatency(42);
log.addBatch([10, 20, 30]);

const response = await fetch(
  "/v1/namespaces/frontend/streams/client-telemetry/merge",
  {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-SketchLog-Auth-Token": token,
    },
    body: JSON.stringify(log.serialize()),
  },
);
if (!response.ok) throw new Error(`merge failed: ${response.status}`);
log.reset();
```

Serialized 64-bit counters are decimal strings so JavaScript cannot round them.
The authenticated merge endpoint validates the complete state, dimensions,
counter sums, and compatibility before merging. Its normal request-size limit
still applies.

CI builds the Emscripten output and runs the packaged Node smoke test. Browser
deployments must provide the generated `.wasm` file through the package's
`locateFile` mechanism when their bundler does not do so automatically.
