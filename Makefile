.PHONY: check lint test secrets

export DJANGO_SECRET_KEY ?= local-check-dummy-key

check: secrets lint test ## Run all checks (mirrors CI)

secrets: ## Scan for leaked secrets
	@git ls-files -z | xargs -0 uv run detect-secrets-hook --baseline .secrets.baseline

lint: ## Ruff + mypy
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy .

test: ## Run unit/integration tests
	uv run pytest -m "not e2e" -v --cov

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'
