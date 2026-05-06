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
uv sync --frozen --all-extras

# Activate the pre-commit hook (one-time setup per clone)
git config core.hooksPath .githooks
```

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

All checks require `DJANGO_SECRET_KEY` to be set. The Makefile provides a dummy value automatically — no `.env` sourcing needed for checks.

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
| **Docker Build** | Builds the container image (no push) |

CI uses a hardcoded dummy `DJANGO_SECRET_KEY` — this is intentional and not a security concern (it's never used in production).

## Environment Variables

| Variable | Required | Purpose |
|----------|----------|---------|
| `DJANGO_SECRET_KEY` | Yes | Django cryptographic signing key |
| `DJANGO_DEBUG` | No | Set to `"true"` for debug mode (default: `"false"`) |
| `DJANGO_ALLOWED_HOSTS` | No | Comma-separated allowed hosts |

For local development, create a `.env` file (git-ignored):
```bash
export DJANGO_SECRET_KEY="your-local-dev-key-here"
export DJANGO_DEBUG="true"
export DJANGO_ALLOWED_HOSTS="localhost,127.0.0.1"
```

Source it before running Django directly:
```bash
source .env
uv run python manage.py runserver
```
