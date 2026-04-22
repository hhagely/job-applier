.PHONY: setup run dev clean lint

setup: ## Install dependencies
	uv sync

run: ## Run the application
	uv run python -m job_applier

dev: ## Run with auto-reload (for development)
	uv run python -m job_applier

clean: ## Remove build artifacts and caches
	rm -rf __pycache__ .venv dist build *.egg-info
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

lint: ## Run linter
	uv run ruff check src/

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-15s\033[0m %s\n", $$1, $$2}'
