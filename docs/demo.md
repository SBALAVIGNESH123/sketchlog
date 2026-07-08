# Hosted Demo & Interactive Playground

SketchLog ships with a zero-dependency interactive playground that lets you explore core features in any browser — no installation required.

👉 **Open the Playground**: [https://sbalavignesh123.github.io/sketchlog/demo/](https://sbalavignesh123.github.io/sketchlog/demo/)

> You can also open `demo/index.html` directly in any browser — no server required.

---

## Features

### DDSketch Quantile Estimation
- Add arbitrary positive values (latencies, request sizes, durations)
- See **p50 / p95 / p99** estimates update in real time
- Adjust the relative accuracy parameter (α) and see the accuracy guarantee change
- Visual bucket distribution chart

### Stream Operations
- Simulate writing JSON records to any stream path
- Read them back and see the log output
- Demonstrates the core SketchLog stream abstraction

### Export Payload Preview
- **Loki** — Preview the JSON body sent to `/loki/api/v1/push`
- **Datadog** — Preview the Metrics API v2 `series` payload
- **New Relic** — Preview the Insights Events API payload (US and EU regions)

### Python API Cheatsheet
- Copyable code snippets for basic sketch, stream write, agent config, and Loki export

---

## Running Locally

The playground is a single-page static site — no build step required.

```bash
# From the repo root
python -m http.server 8080 --directory demo
# Then open http://localhost:8080
```

Or simply open `demo/index.html` directly in your browser.

---

## Deploying to GitHub Pages

```yaml
# .github/workflows/deploy-demo.yml
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

---

## Deploying to Netlify

```toml
# netlify.toml
[build]
  publish = "demo/"
```

No build command needed — the site is pure HTML/CSS/JS.

---

## Deploying to Vercel

```json
{
  "outputDirectory": "demo",
  "routes": [{ "src": "/(.*)", "dest": "/$1" }]
}
```

---

## Architecture

The playground is intentionally minimal:

| File | Role |
|---|---|
| `demo/index.html` | Single-page app — Nav, Hero, 4 interactive sections, CTA, Footer |
| `demo/assets/demo.css` | Full dark design system, CSS custom properties, responsive |
| `demo/assets/demo.js` | DDSketch implementation, stream simulation, export preview, snippets |

**Zero external dependencies.** Zero JS frameworks. Zero build step. Works offline.

---

## DDSketch in the Browser

The playground includes a faithful browser implementation of DDSketch:

```javascript
const sketch = new DDSketch(0.01); // 1% relative accuracy
sketch.add(42.5);
sketch.add(150.0);
const p95 = sketch.quantile(0.95); // guaranteed ±1% relative error
```

This is the same algorithm used in the Python `sketchlog` package — the browser version demonstrates the same accuracy guarantees interactively.

---

## Security

- No external CDN dependencies
- No API keys or secrets in the frontend
- No cookies or tracking
- No `eval()` or dynamic code execution
