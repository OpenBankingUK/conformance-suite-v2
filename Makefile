.PHONY: check lint test secrets dev serve docker

export DJANGO_SECRET_KEY ?= local-check-dummy-key
export DJANGO_ALLOWED_HOSTS ?= localhost,127.0.0.1

check: secrets lint test ## Run all checks (mirrors CI)

secrets: ## Scan for leaked secrets
	@git ls-files -z | xargs -0 uv run detect-secrets-hook --baseline .secrets.baseline

lint: ## Ruff + mypy
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy .

test: ## Run unit/integration tests
	uv run pytest -m "not e2e" -v --cov

dev: ## Run local dev server (auto-reload, debug)
	uv run python manage.py runserver

serve: ## Run local prod server (uvicorn, no reload)
	uv run uvicorn config.asgi:application --host 0.0.0.0 --port 8000

docker: ## Build and run Docker container
	docker build -t conformance-suite .
	docker run --rm -p 8000:8000 conformance-suite

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'
