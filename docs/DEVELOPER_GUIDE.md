# Developer Guide

## Prerequisites

- Python 3.14+ (managed via `.python-version`)
- [uv](https://docs.astral.sh/uv/) package manager
- Docker (for container builds)
- GNU Make

## Getting Started

```bash
# Clone the repo
git clone <repo-url>
cd conformance-suite-v2

# Install all dependencies (including dev tools)
# uv includes the [dependency-groups] dev group by default.
# Production builds (Dockerfile) use --no-dev to exclude dev tooling.
# --no-install-project: this is a container-deployed app, not a distributable package.
uv sync --frozen --no-install-project

# Activate the pre-commit hook (one-time setup per clone)
git config core.hooksPath .githooks
```

## Running the Application

```bash
make dev         # Django dev server (auto-reload, debug error pages)
make serve       # Uvicorn locally (mirrors production, no reload)
make docker      # Build and run the Docker container
```

| Command | Server | Auto-reload | Use case |
|---------|--------|-------------|----------|
| `make dev` | Django `runserver` | Yes | Day-to-day development |
| `make serve` | Uvicorn | No | Test production behaviour locally |
| `make docker` | Uvicorn (container) | No | Full production-like environment (requires `DJANGO_SECRET_KEY` and `DJANGO_ALLOWED_HOSTS`) |

`make dev` and `make serve` work with zero configuration. `make docker` requires environment variables (see [Environment Variables](#environment-variables)).

All targets serve on `http://localhost:8000`.

## Local Checks

Run `make check` before pushing. This mirrors the CI pipeline and runs:

1. **Secret scanning** — detects leaked credentials, tokens, API keys
2. **Linting** — ruff lint + format checks
3. **Type checking** — mypy in strict mode
4. **Tests** — pytest (unit + integration, excluding E2E)

```bash
make check       # Full check (secrets → lint → test)
make secrets     # Secret scanning only
make lint        # Ruff + mypy only
make test        # Tests only
make help        # Show all targets
```

No environment variables are needed for `make check` — `settings.py` provides a safe `django-insecure-` fallback when `DJANGO_SECRET_KEY` is absent, allowing tooling (mypy, pytest) to boot Django without external configuration.

## Pre-Commit Hook

The `.githooks/pre-commit` hook runs automatically on every `git commit`. It scans **staged files only** for secrets using `detect-secrets`.

If a secret is detected:
- The commit is **blocked**
- You'll see which file and line triggered the detection
- Fix the code (move the value to an environment variable or `.env`)

To bypass in an emergency (e.g. confirmed false positive):
```bash
git commit --no-verify
```

### Setup

The hook is activated by pointing git at the `.githooks/` directory:

```bash
git config core.hooksPath .githooks
```

This is a per-clone setting. Every developer must run this once after cloning.

## Secret Scanning

We use [detect-secrets](https://github.com/Yelp/detect-secrets) to prevent credentials from being committed.

### How It Works

- **`.secrets.baseline`** — tracks scanner configuration and audited findings. Committed to the repo so the team shares the same state.
- **`detect-secrets-hook`** — the checking command. Compares staged files against the baseline and fails if new unaudited secrets are found.

### Handling False Positives

If `detect-secrets` flags something that isn't a real secret:

```bash
# Regenerate the baseline (picks up the new finding)
uv run detect-secrets scan --exclude-files '\.env$' --exclude-files 'uv\.lock$' > .secrets.baseline

# Audit — interactively mark findings as true/false positives
uv run detect-secrets audit .secrets.baseline

# Commit the updated baseline
git add .secrets.baseline
git commit -m "chore: update secrets baseline"
```

Alternatively, add an inline comment to suppress a specific line:
```python
KNOWN_PUBLIC_VALUE = "not-a-secret"  # pragma: allowlist secret
```

### What Gets Scanned

The scanner checks for: AWS keys, Azure storage keys, GitHub/GitLab tokens, JWTs, high-entropy strings, private keys, Stripe/Twilio/SendGrid keys, hardcoded passwords (via keyword detection), and more.

Excluded from scanning: `.env` files, `uv.lock`.

## CI Pipeline

The GitHub Actions CI workflow (`.github/workflows/ci.yml`) runs the same checks on every PR and push to protected branches:

| Job | What it does |
|-----|--------------|
| **Lint & Type Check** | `ruff check`, `ruff format --check`, `mypy` |
| **Unit & Integration Tests** | `pytest` with coverage reporting |
| **Docker Build & Smoke Test** | Builds the container image and verifies it starts and passes a health check |

CI uses a hardcoded dummy `DJANGO_SECRET_KEY` — this is intentional and not a security concern (it's never used in production).

## Environment Variables

| Variable | Required | Purpose |
|----------|----------|---------|
| `DJANGO_SECRET_KEY` | Production only | Django cryptographic signing key. Falls back to a safe `django-insecure-` value for local tooling. |
| `DJANGO_DEBUG` | No | Set to `"true"` for debug mode (default: `"false"`) |
| `DJANGO_ALLOWED_HOSTS` | Production only | Comma-separated allowed hosts. Enforced when `DJANGO_SECRET_KEY` is explicitly set and `DEBUG` is off. |

### How environment variables are managed per context

| Context | Who provides env vars | Notes |
|---------|----------------------|-------|
| `make check` (lint/test) | No env vars needed | `settings.py` uses a safe `django-insecure-` fallback for `SECRET_KEY`; production guards are skipped |
| `make dev` | Makefile sets `DJANGO_DEBUG=true` | Django ignores `ALLOWED_HOSTS` when `DEBUG=True` |
| `make serve` | Makefile sets `DJANGO_ALLOWED_HOSTS` | Allows `localhost` and `127.0.0.1` for local Uvicorn |
| `make docker` | Caller must provide both vars | Simulates production — fails fast if misconfigured |
| Production | Orchestrator (K8s, ECS, Compose) | Must set a real `SECRET_KEY` and `ALLOWED_HOSTS` |

**Design principle:** `settings.py` is declarative — it requires correct configuration and fails loudly when misconfigured. The Makefile provides developer ergonomics per context. An unconfigured production deployment will reject all requests (not silently degrade).

**For local Docker** (production simulation):
```bash
DJANGO_SECRET_KEY="my-local-key" DJANGO_ALLOWED_HOSTS="localhost" make docker  # pragma: allowlist secret
```
