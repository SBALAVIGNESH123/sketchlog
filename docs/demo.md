# Hosted Demo and Interactive Playground

SketchLog ships with a zero-dependency interactive playground that lets visitors
explore core features in any browser with no installation required.

Open the playground:
[https://sbalavignesh123.github.io/sketchlog/demo/](https://sbalavignesh123.github.io/sketchlog/demo/)

You can also open `demo/index.html` directly in a browser. The playground is a
single-page static app and does not require a server.

## Features

### DDSketch quantile estimation

- Add arbitrary positive values such as latencies, request sizes, and durations.
- See p50, p95, and p99 estimates update in real time.
- Adjust the relative accuracy parameter and see the accuracy guarantee change.
- Inspect the visual bucket distribution chart.

### Stream operations

- Simulate writing JSON records to any stream path.
- Read the records back and inspect the log output.
- Demonstrate the core SketchLog stream abstraction without running a backend.

### Export payload preview

- Loki: preview the JSON body sent to `/loki/api/v1/push`.
- Datadog: preview the Metrics API v2 `series` payload.
- New Relic: preview the Insights Events API payload for US and EU regions.

### Python API cheatsheet

- Copy snippets for basic sketch usage.
- Copy stream write examples.
- Copy agent configuration examples.
- Copy Loki export examples.

## Running locally

The playground is pure HTML, CSS, and JavaScript.

```bash
python -m http.server 8080 --directory demo
```

Then open <http://localhost:8080>.

You can also open `demo/index.html` directly from the filesystem.

## Deploying to GitHub Pages

```yaml
name: Deploy Demo
on:
  push:
    branches: [main]
    paths: [demo/**]

jobs:
  deploy:
    runs-on: ubuntu-latest
    permissions:
      pages: write
      id-token: write
    steps:
      - uses: actions/checkout@v4
      - uses: actions/configure-pages@v4
      - uses: actions/upload-pages-artifact@v3
        with:
          path: demo/
      - uses: actions/deploy-pages@v4
```

## Deploying to Netlify

```toml
[build]
  publish = "demo/"
```

No build command is required.

## Deploying to Vercel

```json
{
  "outputDirectory": "demo",
  "routes": [{ "src": "/(.*)", "dest": "/$1" }]
}
```

## Architecture

| File | Role |
| --- | --- |
| `demo/index.html` | Single-page app with navigation, hero, interactive sections, call to action, and footer |
| `demo/assets/demo.css` | Dark design system, CSS custom properties, and responsive layout |
| `demo/assets/demo.js` | DDSketch implementation, stream simulation, export preview, and snippets |

The playground has zero external dependencies, zero JavaScript frameworks, zero
build step, and works offline.

## DDSketch in the browser

The playground includes a browser implementation of DDSketch:

```javascript
const sketch = new DDSketch(0.01); // 1% relative accuracy
sketch.add(42.5);
sketch.add(150.0);
const p95 = sketch.quantile(0.95); // guaranteed +/-1% relative error
```

This demonstrates the same accuracy model used by the Python `sketchlog`
package.

## Security

- No external CDN dependencies.
- No API keys or secrets in the frontend.
- No cookies or tracking.
- No `eval()` or dynamic code execution.
