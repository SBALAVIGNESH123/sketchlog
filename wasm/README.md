# `@sketchlog/wasm`

Bounded-memory SketchLog runtime compiled to WebAssembly.

```bash
npm install @sketchlog/wasm
```

```javascript
const { StreamLog } = require("@sketchlog/wasm");

await StreamLog.init();
const log = new StreamLog();
log.addBatch([10, 20, 30]);
console.log(log.p99);
log.destroy();
```

Call `StreamLog.init()` before constructing a log and call `destroy()` when the
instance is no longer needed. Serialized 64-bit counters are decimal strings so
they remain precise across JavaScript runtimes and can be merged through the
SketchLog server.

Browser bundlers must serve the packaged `dist/sketchlog.wasm` asset and make
its URL available to the Emscripten loader. See the
[WASM documentation](https://sbalavignesh123.github.io/sketchlog/features/wasm_runtime/)
for browser and server-merge examples.

## Source development

Docker is the only build prerequisite. From `wasm/`, run:

```bash
npm ci
npm test
```

`npm test` builds the runtime with a digest-pinned Emscripten image before
running the Node smoke test, so a clean checkout does not depend on a hidden
prebuilt `dist/` directory.
