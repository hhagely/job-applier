.PHONY: setup api web dev build-web app-dev desktop-setup sidecar electron electron-dev dist ingest prune dedupe-jd diagnose-filter clean lint test test-api test-web help

setup: ## Install backend + frontend dependencies (+ Chromium for PDF rendering)
	uv sync
	uv run playwright install chromium
	cd web && npm install

api: ## Run FastAPI backend on :8000
	uv run uvicorn job_applier.api.app:app --reload --port 8000

web: ## Run SvelteKit dev server on :5174
	cd web && npm run dev

dev: ## Run backend + frontend together (requires GNU parallel or two terminals)
	@echo "Run 'make api' in one terminal and 'make web' in another."
	@echo "Or: (make api &) && make web"

build-web: ## Build the SvelteKit frontend (adapter-node -> web/build/index.js)
	cd web && npm run build

desktop-setup: ## Install the Electron shell's dependencies
	cd desktop && npm install

electron: build-web ## Run the Electron desktop shell from source (dev version-testing loop)
	cd desktop && npm start

electron-dev: ## Hot-reload dev shell: backend (--reload) + Vite HMR renderer + auto-restart Electron on main-process edits
	cd desktop && npm run dev

sidecar: ## Freeze the Python backend into a standalone binary (dist/job-applier-backend/)
	uv run pyinstaller --noconfirm --clean desktop/sidecar/job-applier-backend.spec

dist: build-web sidecar ## Build the unsigned installable desktop app (desktop/dist/)
	cd desktop && npm run dist

app-dev: build-web ## Boot API + built web server on free ports and open the browser (no make api/web dance)
	uv run job-applier app-dev

ingest: ## Pull jobs from configured sources
	uv run job-applier ingest

diagnose-filter: ## Dry-run every source and report what the hard filter is dropping
	uv run job-applier diagnose-filter

prune: ## Lighten old/archived postings (clears description + raw, keeps dedupe hashes)
	uv run job-applier prune

dedupe-jd: ## Backfill JD SimHash fingerprints and soft-link near-duplicate postings
	uv run job-applier dedupe-jd

refresh-slugs: ## Discover new Greenhouse/Lever/Workable/SmartRecruiters slugs from the SimplifyJobs feed
	uv run job-applier refresh-slugs

refresh-slugs-full: ## Discover new slugs and re-verify existing ones across all per-company sources (auto-disables dead boards)
	uv run job-applier refresh-slugs --reverify

clean: ## Remove build artifacts and caches
	rm -rf dist build *.egg-info
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	rm -rf web/.svelte-kit web/node_modules/.vite

lint: ## Run linter
	uv run ruff check src/

test-api: ## Run backend tests (pytest)
	uv run pytest

test-web: ## Run frontend tests (vitest)
	cd web && npm test

test: test-api test-web ## Run all tests

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-15s\033[0m %s\n", $$1, $$2}'
