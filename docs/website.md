# Marketing landing page

The public SketchLog marketing website lives at `website-standalone/` and is a
self-contained, dependency-free static site deployed to the GitHub Pages root.
A React/Vite prototype also exists under `website/`, but the production public
page at `https://sbalavignesh123.github.io/sketchlog/` is built from
`website-standalone/index.html`.

## Structure

```text
website-standalone/
├── index.html          # Single-page public marketing site
└── logo.png            # Landing-page logo
```

The GitHub Pages workflow builds MkDocs into `site/docs`, then copies
`website-standalone/*` into the site root and the interactive playground into
`site/demo`.

## Sections

| Section | Anchor | Purpose |
| --- | --- | --- |
| Hero | `#top` | Headline, sub-copy, key stats, primary calls to action |
| Why SketchLog | `#why` | Raw-event storage problem and sketch-based answer |
| Platform | `#platform` | Core capabilities |
| Playground | `#playground` | Hosted no-install browser demo |
| Workflow | `#workflow` | Local demo and server workflow |
| Storage proof | `#storage-proof` | Backend positioning, proof commands, and deterministic evidence |
| Positioning | `.comparison` | SketchLog vs traditional raw-event approaches |
| Integrations | `#integrations` | Prometheus, Grafana, OpenTelemetry, Docker, Kubernetes, WASM, SDKs |
| Product workflows | — | How sketches drive canary, SLO, anomaly, namespace, and mesh decisions |
| CTA | — | Final call to action |
| Footer | — | Docs, GitHub, and security links |

## Storage proof section

The storage proof section is intentionally evidence-based. It should:

- explain that SketchLog stores compact stream summaries, not raw telemetry
  rows;
- position in-memory, PostgreSQL, and OmniKV without overclaiming;
- link to `docs/storage-backends/`, `docs/db-hardening/`, and
  `docs/omnikv-storage/`;
- show deterministic proof-fixture numbers as reproducible evidence, not as
  universal performance guarantees.

## Local development

Serve the public landing page with any static file server:

```bash
cd website-standalone
python -m http.server 8000
# Open http://localhost:8000
```

Or with Node:

```bash
npx serve website-standalone
```

## Validate the combined Pages artifact

From the repository root:

```bash
mkdocs build --strict --site-dir site/docs
cp -r website-standalone/* site/
mkdir -p site/demo
cp demo/index.html site/demo/index.html
cp -r demo/assets site/demo/assets
```

The workflow also creates legacy redirects for old pre-`/docs/` links.

## Netlify / Vercel

Set the publish directory to `website-standalone/` if deploying only the
landing page. Use the GitHub Pages workflow when you want the landing page,
documentation, and playground together.

## Docker / nginx

```dockerfile
FROM nginx:alpine
COPY website-standalone/ /usr/share/nginx/html/
```

## Design tokens

The landing page defines its design tokens in the `website-standalone/index.html`
`<style>` block:

| Variable | Value | Usage |
| --- | --- | --- |
| `--purple` | `#632ca6` | Primary brand actions |
| `--violet` | `#8c4de8` | Gradient accent |
| `--blue` | `#3267d6` | Secondary chart line |
| `--green` | `#0f8f68` | Healthy status and proof signal |
| `--soft` | `#f7f5fb` | Soft section background |
| `--line` | `#e7e1ef` | Borders |
| `--muted` | `#625b6f` | Secondary text |

## Accessibility

- Semantic headings and landmarks are used for main sections.
- Navigation links jump directly to page sections.
- Body text uses high-contrast colors on light backgrounds.
- No JavaScript is required for core content visibility.

## Performance

- Zero external dependencies: no CDN, no web fonts, no analytics.
- System font stack for instant render.
- Static HTML/CSS copied directly into the GitHub Pages artifact.

## Tests

```bash
pytest tests/test_website.py -v
python -m mkdocs build --strict
```

Tests verify required website files, metadata, sections, public links, and the
storage proof content. MkDocs strict mode catches broken internal documentation
links.
