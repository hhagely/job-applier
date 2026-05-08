.PHONY: setup api web dev ingest clean lint test test-api test-web help

setup: ## Install backend + frontend dependencies
	uv sync
	cd web && npm install

api: ## Run FastAPI backend on :8000
	uv run uvicorn job_applier.api.app:app --reload --port 8000

web: ## Run SvelteKit dev server on :5174
	cd web && npm run dev

dev: ## Run backend + frontend together (requires GNU parallel or two terminals)
	@echo "Run 'make api' in one terminal and 'make web' in another."
	@echo "Or: (make api &) && make web"

ingest: ## Pull jobs from configured sources
	uv run job-applier ingest

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
