# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

### Added

- Project scaffolding: Django application with health endpoint, ASGI server (uvicorn), and environment-based configuration
- CI pipeline: lint (ruff), type checking (mypy strict), unit/integration tests, detect-secrets scanning, Docker build with container smoke test
- E2E workflow: end-to-end test runner with manual dispatch and model bank configuration
- Docker: multi-stage build with pinned Python 3.14.4, non-root user, and healthcheck
- Developer tooling: Makefile, pre-commit secret scanning (detect-secrets), ruff, mypy (strict)
- Developer guide: environment variable requirements and local development setup
- Repository governance documentation: branch rulesets, actions allowlist, advanced security settings
- Model-bank smoke-check core: JSON config loading, Ozone discovery fetch, JWKS follow-up request, structured result output, and manual runner
- Manifest v0 parser: typed JSON manifest contract for OpenID discovery requests with optional JWKS follow-up checks
- Manifest v0 executor: configuration-driven JSON request execution, assertion evaluation, JWKS follow-up handling, and CLI `--manifest` support
- Manifest v1 schema: sequential multi-step manifest format with `${...}` placeholder substitution for context carry-forward between steps
- Execution context module (`conformance/context.py`): immutable step-record accumulation and dot-path placeholder resolution

### Changed

- Enforced Google-style docstrings via ruff pydocstyle and backfilled the `conformance/` package
- Manifest v0 `followUp` is now internally desugared to v1 sequential steps at execution time (no external behaviour change for v0 consumers)
- Manifest v0 test IDs now enforce `[A-Za-z0-9][A-Za-z0-9_-]*` validation (dots are rejected) to keep placeholder step-path parsing unambiguous

### Fixed

- Defaulted model-bank smoke-check output to `out/test-results.json` and ignored generated `out/` contents
- Pre-commit secret scanning hook now includes renamed files (`--diff-filter=ACMR`)
- Corrected coverage omit comment for `config/settings.py` in `pyproject.toml`
- Removed undeclared `--model-bank-url` pytest flag from testing strategy docs
- Added `--no-install-project` to all `uv sync` calls — this is a container-deployed app, not a distributable Python package

### Security

- Manifest URLs now reject IP-literal hostnames to harden against SSRF in manifest-driven HTTP execution

---

## [1.9.8] - Unreleased (in beta)

See release notes from conformance-suite repo for more details

---

[Unreleased]: https://github.com/OpenBankingUK/ob-conformance-tool/compare/v1.9.8...HEAD
[1.9.8]: https://github.com/OpenBankingUK/ob-conformance-tool/releases/tag/v1.9.8
