# Developer Experience — One-Command Setup

SketchLog ships with a **Makefile** and a **VS Code Dev Container** so any
contributor or evaluator can be fully productive in under 5 minutes.

---

## Quick start

```bash
git clone https://github.com/SBALAVIGNESH123/sketchlog.git
cd sketchlog
make dev-install   # install Python + Node + Go deps
make dev           # start SketchLog server on :7700
```

That's it. No manual pip, npm, or go commands required.

---

## Prerequisites

| Tool | Minimum version |
|---|---|
| Python | 3.10+ |
| pip | 22+ |
| Node.js | 18+ |
| npm | 9+ |
| Go | 1.21+ |
| Docker | 20+ (for `make demo`) |
| Git | 2.40+ |

Run `make env-check` to verify all tools are present.

---

## Makefile targets

### Setup

| Target | What it does |
|---|---|
| `make dev-install` | Install Python (`[dev,server]`), Node (`npm ci`), and Go deps |
| `make install` | Install Python package only (no Node/Go) |

### Development servers

| Target | What it does |
|---|---|
| `make dev` | Start SketchLog server on `:7700` with auto-reload |
| `make dev-full` | Start server `:7700` **and** React dashboard `:3000` concurrently |
| `make demo` | Start the full Docker Compose demo stack |
| `make demo-down` | Stop and clean up the demo stack |

### Testing

| Target | What it does |
|---|---|
| `make test` | Run all Python tests |
| `make test-cov` | Run Python tests with branch coverage HTML report |
| `make test-go` | Run Go client tests |
| `make test-ts` | Run TypeScript/Node conformance tests |
| `make test-all` | Run Python + Go + TypeScript tests |
| `make bench` | Run benchmark lab and write `bench-report.md` |

### Code quality

| Target | What it does |
|---|---|
| `make lint` | Run ruff linter |
| `make fmt` | Format Python code with ruff |
| `make fmt-check` | Check formatting without writing |
| `make typecheck` | Run mypy + pyright |
| `make security` | Run bandit security audit |
| `make check` | Run all quality checks (no tests) |
| `make ci` | Full local CI simulation — quality + tests |

### Docs

| Target | What it does |
|---|---|
| `make docs` | Build MkDocs site (`--strict`) |
| `make docs-serve` | Serve docs locally at `http://localhost:8000` |

### Build

| Target | What it does |
|---|---|
| `make build` | Build Python wheel and sdist |
| `make build-wasm` | Build WASM package |
| `make build-cpp` | Build C++ extension (requires cmake) |

### Operations

| Target | What it does |
|---|---|
| `make doctor` | Run `sketchlog-doctor` readiness check |
| `make cost-estimate` | Run cost savings calculator with example inputs |
| `make mesh-status` | Check Sketch Mesh cluster health (demo mode) |
| `make bench-lab` | Run full benchmark lab |
| `make db-check` | Run database health check (set `SKETCHLOG_DB_URL`) |
| `make clean` | Remove build artifacts and caches |
| `make version` | Print installed SketchLog version |
| `make env-check` | Verify all required tools are installed |

---

## Dev Container (VS Code / GitHub Codespaces)

The `.devcontainer/devcontainer.json` configuration gives you a
fully-provisioned environment with zero local setup.

### Open in VS Code

1. Install the [Dev Containers extension](https://marketplace.visualstudio.com/items?itemName=ms-vscode-remote.remote-containers)
2. Clone the repo and open it in VS Code
3. Click **"Reopen in Container"** when prompted
4. Wait ~2 minutes for the container to build and `make dev-install` to run

### Open in GitHub Codespaces

1. Click **"Code" → "Codespaces" → "Create codespace on main"**
2. Wait ~2 minutes for the environment to provision
3. Run `make dev` in the integrated terminal

### What the container includes

| Tool | Version |
|---|---|
| Python | 3.12 |
| Go | 1.22 |
| Node.js | 20 LTS |
| Docker-in-Docker | Latest |

### Pre-installed VS Code extensions

- `ms-python.python` — Python language support
- `ms-python.mypy-type-checker` — mypy inline type errors
- `charliermarsh.ruff` — linter + formatter
- `golang.go` — Go language support
- `dbaeumer.vscode-eslint` — TypeScript/JavaScript linting
- `esbenp.prettier-vscode` — TypeScript/JSON formatting
- `ms-azuretools.vscode-docker` — Docker management
- `github.vscode-github-actions` — Workflow editing
- `yzhang.markdown-all-in-one` — Docs editing

### Forwarded ports

| Port | Service |
|---|---|
| `7700` | SketchLog server |
| `9090` | Prometheus metrics |
| `3000` | React dashboard |

---

## Common workflows

### First-time contributor

```bash
git clone https://github.com/SBALAVIGNESH123/sketchlog.git
cd sketchlog
make dev-install
make ci              # full quality + test pass before you touch anything
make dev             # verify the server starts
```

### Before opening a PR

```bash
make ci              # quality + tests must pass
make docs            # docs must build clean
```

### Running the full demo

```bash
make demo            # starts Docker Compose demo stack
# Open http://localhost:3000 in your browser
make demo-down       # clean up
```

### Checking a production deployment

```bash
export SKETCHLOG_DB_URL=postgresql+asyncpg://user:pass@host/sketchlog
make doctor
make db-check
make mesh-status
```

---

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `SKETCHLOG_ENV` | `production` | Set to `development` to enable hot-reload defaults |
| `PYTHONPATH` | `python` | Set by Makefile automatically |
| `SKETCHLOG_DB_URL` | — | PostgreSQL/MySQL URL for `make db-check` |
| `SKETCHLOG_AUTH_TOKEN` | — | Bearer token for authenticated server endpoints |

---

## Caveats

1. `make dev-full` uses bash `trap` — works on Linux/macOS; on Windows use WSL2 or the Dev Container.
2. `make build-wasm` requires the Emscripten toolchain — install via `emsdk`.
3. `make build-cpp` requires `cmake` 3.20+ and a C++17 compiler.
4. `make demo` requires Docker Engine with Compose v2 (`docker compose`, not `docker-compose`).
5. Coverage reports require `pytest-cov` — included in `python/[dev]` extras.
