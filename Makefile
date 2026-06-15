.PHONY: check lint test integration secrets audit dev dev-unmasked serve docker help

PYTEST_XDIST ?= -n auto --dist loadfile
PYTEST_ARGS ?=
COV_FAIL_UNDER ?= $(if $(strip $(PYTEST_ARGS)),0,80)

check: secrets lint test ## Run all local checks (secrets + lint + offline tests)

secrets: ## Scan for leaked secrets
	@git ls-files -z | xargs -0 uv run detect-secrets-hook --baseline .secrets.baseline --

audit: ## Audit secrets baseline for unreviewed entries
	uv run detect-secrets audit .secrets.baseline

lint: ## Ruff + mypy + docstring coverage + docstring structure
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy .
	uv run interrogate -c pyproject.toml .
	uv run pydoclint .

test: ## Run unit + offline Django integration tests (excludes live-network Ozone and Docker e2e tiers)
	DJANGO_DEBUG=true uv run pytest -m "not e2e and not ozone" -v --cov --cov-fail-under=$(COV_FAIL_UNDER) $(PYTEST_XDIST) $(PYTEST_ARGS)

integration: ## Run live-network Ozone integration tests (skipped unless tier env vars are set)
	DJANGO_DEBUG=true uv run pytest -m ozone -v tests/integration

dev: ## Run local dev server (auto-reload, debug)
	@mkdir -p local-config/certs
	@test -f local-config/certs/dev-server.crt -a -f local-config/certs/dev-server.key || \
		openssl req -x509 -newkey rsa:2048 -nodes -days 365 \
			-keyout local-config/certs/dev-server.key \
			-out local-config/certs/dev-server.crt \
			-subj "/CN=0.0.0.0" \
			-addext "subjectAltName=IP:0.0.0.0,IP:127.0.0.1,DNS:localhost"
	DJANGO_DEBUG=true uv run uvicorn config.asgi:application --host 0.0.0.0 --port 8443 --reload --ssl-keyfile local-config/certs/dev-server.key --ssl-certfile local-config/certs/dev-server.crt

dev-unmasked: ## Run local dev server with unmasked execution logs
	@mkdir -p local-config/certs
	@test -f local-config/certs/dev-server.crt -a -f local-config/certs/dev-server.key || \
		openssl req -x509 -newkey rsa:2048 -nodes -days 365 \
			-keyout local-config/certs/dev-server.key \
			-out local-config/certs/dev-server.crt \
			-subj "/CN=0.0.0.0" \
			-addext "subjectAltName=IP:0.0.0.0,IP:127.0.0.1,DNS:localhost"
	CONFORMANCE_DEVELOPER_MODE=true DJANGO_DEBUG=true uv run uvicorn config.asgi:application --host 0.0.0.0 --port 8443 --reload --ssl-keyfile local-config/certs/dev-server.key --ssl-certfile local-config/certs/dev-server.crt

serve: ## Run local prod server (uvicorn, no reload)
	DJANGO_ALLOWED_HOSTS="localhost,127.0.0.1,0.0.0.0" uv run uvicorn config.asgi:application --host 0.0.0.0 --port 8443

docker: ## Build and run Docker container (requires DJANGO_SECRET_KEY and DJANGO_ALLOWED_HOSTS)
ifndef DJANGO_SECRET_KEY
	$(error DJANGO_SECRET_KEY must be set to run Docker container)
endif
ifndef DJANGO_ALLOWED_HOSTS
	$(error DJANGO_ALLOWED_HOSTS must be set to run Docker container)
endif
	docker build -t conformance-suite .
	docker run --rm -p 8443:8443 \
		-e DJANGO_SECRET_KEY="$(DJANGO_SECRET_KEY)" \
		-e DJANGO_ALLOWED_HOSTS="$(DJANGO_ALLOWED_HOSTS),0.0.0.0" \
		conformance-suite

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'
