# Marketing Landing Page

The SketchLog marketing website lives at `website/` and is a self-contained,
dependency-free static site.

## Structure

```
website/
├── index.html          # Single-page marketing site
└── assets/
    ├── style.css       # All styles (CSS custom properties, responsive)
    └── app.js          # Minimal vanilla JS (nav, scroll animations)
```

## Sections

| Section | Anchor | Purpose |
|---|---|---|
| Hero | `#hero` | Headline, sub-copy, key stats |
| Code demo | — | Syntax-highlighted live example |
| Features | `#features` | Six feature cards |
| How it works | `#how-it-works` | Four-step pipeline explanation |
| Integrations | `#integrations` | Prometheus, Datadog, Loki, New Relic, Kubernetes, asyncio |
| Quickstart | `#quickstart` | pip install + code sample + doc links |
| FAQ | `#faq` | Six frequently asked questions |
| CTA | — | Final call to action |
| Footer | — | Nav links, copyright |

## Local development

Serve the site with any static file server:

```bash
# Python (no install required)
cd website
python -m http.server 8000
# Open http://localhost:8000
```

Or with Node:

```bash
npx serve website
```

## Deployment

The site is pure HTML/CSS/JS with no build step required.
Deploy to any static hosting provider:

### GitHub Pages

```bash
# From repo root
git subtree push --prefix website origin gh-pages
```

### Netlify / Vercel

Set **publish directory** to `website/` — no build command needed.

### Docker / nginx

```dockerfile
FROM nginx:alpine
COPY website/ /usr/share/nginx/html/
```

## Design tokens

All colours and spacing are defined as CSS custom properties in `style.css`:

| Variable | Value | Usage |
|---|---|---|
| `--brand` | `#6366f1` | Primary indigo |
| `--brand-dark` | `#4f46e5` | Hover state |
| `--accent` | `#a78bfa` | Violet highlight text |
| `--bg` | `#0d0d14` | Page background |
| `--surface` | `#1e1e30` | Card background |
| `--text` | `#e2e2f0` | Body text |
| `--muted` | `#8b8ba8` | Secondary text |
| `--green` | `#34d399` | Code string colour |

## Accessibility

- All interactive elements are keyboard-accessible.
- Mobile burger nav is accessible with `aria-label`.
- Colour contrast ratio ≥ 4.5:1 for body text on all backgrounds.
- No JavaScript required for core content visibility (JS only adds enhancements).

## Performance

- Zero external dependencies (no CDN, no Google Fonts, no analytics).
- System font stack — instant render, no FOIT.
- IntersectionObserver used for scroll animations (no scroll event polling).
- Total page weight (HTML + CSS + JS) < 40 KB uncompressed.

## Tests

```bash
pytest tests/test_website.py -v
```

Tests verify:
- All three files exist.
- Required HTML sections and meta tags are present.
- No placeholder text (`TODO`, `Lorem ipsum`, etc.).
- CSS variables, responsive breakpoints, and component selectors are present.
- JS uses IntersectionObserver, handles nav burger, and avoids `eval`.
