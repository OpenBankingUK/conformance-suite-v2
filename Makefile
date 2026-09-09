.PHONY: check lint test unit component secrets audit dev dev-unmasked serve docker help

check: secrets lint test ## Run all local checks (secrets + lint + complete offline test suite)

secrets: ## Scan for leaked secrets
	@printf '%s\n' "==> Scanning tracked files for secrets (this may take about 15 seconds)"
	@git ls-files -z | xargs -0 uv run detect-secrets-hook \
		--baseline .secrets.baseline \
		--exclude-files '^conformance/standards/ob_read_write/v[^/]+/[^/]+-openapi\.json$$' --
	@printf '%s\n' "==> Secret scan passed"

audit: ## Audit secrets baseline for unreviewed entries
	uv run detect-secrets audit .secrets.baseline

lint: ## Ruff + mypy
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy .

test: ## Run the complete offline suite (unit + component) with aggregate coverage
	DJANGO_DEBUG=true uv run pytest -m "unit or component" -v --cov

unit: ## Run only unit tests (no coverage — focused iteration)
	DJANGO_DEBUG=true uv run pytest -m unit -v

component: ## Run only component tests (no coverage — focused iteration)
	DJANGO_DEBUG=true uv run pytest -m component -v

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
