# SketchLog — one-command developer experience
# Run `make help` to see all available targets.

.DEFAULT_GOAL := help
PYTHONPATH    := python
export PYTHONPATH

# ──────────────────────────────────────────────────────────────────────────────
# Help
# ──────────────────────────────────────────────────────────────────────────────
.PHONY: help
help: ## Show this help message
	@grep -E '^[a-zA-Z_/-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-28s\033[0m %s\n", $$1, $$2}'

# ──────────────────────────────────────────────────────────────────────────────
# Setup
# ──────────────────────────────────────────────────────────────────────────────
.PHONY: dev-install
dev-install: ## Install all dev dependencies (Python + Node + Go)
	pip install -e ".[dev,server]" --quiet
	cd frontend/react-sketchlog && npm ci --silent
	cd clients/go && go mod download
	@echo "✅  Dev install complete — run 'make dev' to start"

.PHONY: install
install: ## Install SketchLog Python package only
	pip install -e ".[server]" --quiet

# ──────────────────────────────────────────────────────────────────────────────
# Development servers
# ──────────────────────────────────────────────────────────────────────────────
.PHONY: dev
dev: ## Start SketchLog server in dev mode (port 7700, auto-reload)
	sketchlog-server --host 0.0.0.0 --port 7700 --reload

.PHONY: dev-full
dev-full: ## Start SketchLog server + React dashboard (ports 7700 + 3000)
	@echo "Starting SketchLog server on :7700 and React on :3000"
	@trap 'kill 0' SIGINT; \
		sketchlog-server --host 0.0.0.0 --port 7700 & \
		cd frontend/react-sketchlog && npm run dev -- --port 3000 & \
		wait

.PHONY: demo
demo: ## Start the full demo stack with Docker Compose
	docker compose -f demo/compose.yml up --build

.PHONY: demo-down
demo-down: ## Stop and clean up the demo stack
	docker compose -f demo/compose.yml down -v

# ──────────────────────────────────────────────────────────────────────────────
# Testing
# ──────────────────────────────────────────────────────────────────────────────
.PHONY: test
test: ## Run all Python tests
	python -m pytest tests/ -q --tb=short

.PHONY: test-cov
test-cov: ## Run Python tests with branch coverage report
	python -m pytest tests/ -q --tb=short \
		--cov=sketchlog --cov-branch \
		--cov-report=term-missing --cov-report=html:htmlcov

.PHONY: test-go
test-go: ## Run Go client tests
	cd clients/go && go test ./... -v

.PHONY: test-ts
test-ts: ## Run TypeScript/Node conformance tests
	cd clients/node && npm test

.PHONY: test-all
test-all: test test-go test-ts ## Run all tests (Python + Go + TypeScript)

.PHONY: bench
bench: ## Run the SketchLog benchmark lab
	sketchlog-bench-lab --report bench-report.md
	@echo "Report written to bench-report.md"

# ──────────────────────────────────────────────────────────────────────────────
# Code quality
# ──────────────────────────────────────────────────────────────────────────────
.PHONY: lint
lint: ## Run ruff linter
	python -m ruff check python/ tests/

.PHONY: fmt
fmt: ## Format Python code with ruff
	python -m ruff format python/ tests/

.PHONY: fmt-check
fmt-check: ## Check Python formatting (no writes)
	python -m ruff format --check python/ tests/

.PHONY: typecheck
typecheck: ## Run mypy + pyright type checks
	python -m mypy python/sketchlog/ --ignore-missing-imports
	python -m pyright python/sketchlog/

.PHONY: security
security: ## Run bandit security audit
	python -m bandit -r python/sketchlog/ -ll -q

.PHONY: check
check: lint fmt-check typecheck security ## Run all quality checks (no tests)

.PHONY: ci
ci: check test ## Full local CI simulation (quality + tests)

# ──────────────────────────────────────────────────────────────────────────────
# Docs
# ──────────────────────────────────────────────────────────────────────────────
.PHONY: docs
docs: ## Build MkDocs documentation
	python -m mkdocs build --strict

.PHONY: docs-serve
docs-serve: ## Serve docs locally with live reload (port 8000)
	python -m mkdocs serve

# ──────────────────────────────────────────────────────────────────────────────
# Build
# ──────────────────────────────────────────────────────────────────────────────
.PHONY: build
build: ## Build Python wheel and sdist
	python -m build python/

.PHONY: build-wasm
build-wasm: ## Build WASM package
	cd bindings/wasm && npm run build

.PHONY: build-cpp
build-cpp: ## Build C++ extension (requires cmake)
	cmake -B build -S bindings/cpp && cmake --build build --parallel

# ──────────────────────────────────────────────────────────────────────────────
# Database
# ──────────────────────────────────────────────────────────────────────────────
.PHONY: db-check
db-check: ## Run database health check (set SKETCHLOG_DB_URL first)
	sketchlog-db-check --db-url "$(SKETCHLOG_DB_URL)" --format text

.PHONY: db-schema
db-schema: ## Print SketchLog schema version DDL
	@echo "CREATE TABLE IF NOT EXISTS sketchlog_schema_version (version INTEGER NOT NULL);"

# ──────────────────────────────────────────────────────────────────────────────
# Operations
# ──────────────────────────────────────────────────────────────────────────────
.PHONY: doctor
doctor: ## Run sketchlog-doctor readiness check
	sketchlog-doctor

.PHONY: cost-estimate
cost-estimate: ## Run cost savings calculator with example inputs
	sketchlog-cost-estimate \
		--events-per-day 1000000 \
		--avg-event-bytes 256 \
		--retention-days 30 \
		--sketch-accuracy 0.01 \
		--streams 50 \
		--namespaces 5

.PHONY: mesh-status
mesh-status: ## Check Sketch Mesh cluster health (demo mode)
	sketchlog-mesh-viz --demo

.PHONY: bench-lab
bench-lab: ## Run full benchmark lab and write report
	sketchlog-bench-lab --report bench-report.md --output bench-results.json
	@echo "Results: bench-results.json  |  Report: bench-report.md"

# ──────────────────────────────────────────────────────────────────────────────
# Utilities
# ──────────────────────────────────────────────────────────────────────────────
.PHONY: clean
clean: ## Remove build artifacts, caches, and temp files
	rm -rf build dist htmlcov .pytest_cache .mypy_cache .ruff_cache
	rm -rf python/*.egg-info python/sketchlog/__pycache__
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	@echo "✅  Clean complete"

.PHONY: version
version: ## Print installed SketchLog version
	@python -c "import sketchlog; print(sketchlog.__version__)"

.PHONY: env-check
env-check: ## Verify required tools are installed
	@echo "Checking required tools..."
	@python --version
	@pip --version
	@node --version
	@npm --version
	@go version
	@docker --version
	@git --version
	@echo "✅  All required tools found"
