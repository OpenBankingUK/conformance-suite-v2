.PHONY: check lint test secrets dev serve docker help

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
	DJANGO_DEBUG=true uv run python manage.py runserver

serve: ## Run local prod server (uvicorn, no reload)
	DJANGO_ALLOWED_HOSTS="localhost,127.0.0.1" uv run uvicorn config.asgi:application --host 0.0.0.0 --port 8000

docker: ## Build and run Docker container (requires DJANGO_SECRET_KEY and DJANGO_ALLOWED_HOSTS)
ifndef DJANGO_SECRET_KEY
	$(error DJANGO_SECRET_KEY must be set to run Docker container)
endif
ifndef DJANGO_ALLOWED_HOSTS
	$(error DJANGO_ALLOWED_HOSTS must be set to run Docker container)
endif
	docker build -t conformance-suite .
	docker run --rm -p 8000:8000 \
		-e DJANGO_SECRET_KEY="$(DJANGO_SECRET_KEY)" \
		-e DJANGO_ALLOWED_HOSTS="$(DJANGO_ALLOWED_HOSTS)" \
		conformance-suite

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'
