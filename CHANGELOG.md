# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

### Added

- `SKIPPED` outcome state for step results. A v1 manifest step now reports `status="skipped"` (rather than `failed`) when a `${steps.<id>.response...}` placeholder references a prerequisite step that produced no response (transport failure, earlier placeholder error, URL validation failure, etc.). Implements the PRD outcome: _"SKIPPED: test could not run because a prerequisite setup step failed."_ Skips propagate transitively: a step skipped this way is itself recorded with no response, so downstream steps referencing it also skip. The result-file `summary` block now includes a `skipped` count alongside `passed`/`failed`. Aggregate run status remains `failed` whenever any step is non-passed, since a `skipped` always implies an earlier failure. The narrower `MissingPredecessorResponseError` subclass of `PlaceholderResolutionError` is the executor's signal; genuine resolution failures (missing JSON field, malformed path, non-primitive value) on an otherwise-successful predecessor continue to surface as `failed`.
- Tier 1 Ozone integration tests under `tests/integration/`: parameterised v1 OpenID-discovery run plus a status-agnostic 4xx assertion (DL-0011), gated on `OZONE_DISCOVERY_URL` via the `requires_ozone` helper in `tests/_ozone.py`. Tests carry a dedicated `ozone` pytest marker (distinct from the existing `integration` marker for offline Django/DB tests). `make integration` runs the tier when env vars are set; `make test` (and therefore `make check`) excludes the `ozone` marker so default runs stay offline while continuing to execute the offline Django integration tests.
- `Ozone Integration` GitHub Actions workflow (`.github/workflows/ozone-integration.yml`): runs on every PR into `main`/`develop`/`release/**`/`hotfix/**`, plus a nightly schedule and `workflow_dispatch` (with an optional discovery-URL override). Reads the discovery URL from the repository-level `vars.OZONE_DISCOVERY_URL` variable (the URL is publishable, not a secret); higher tiers will introduce a scoped GitHub Environment when mTLS material is added. Fork PRs without variable access skip with a clear notice rather than fail. Surfaces a per-run summary on the PR check page with the tail of the pytest output, and uploads `pytest-json-report` test metadata as an artefact. Non-blocking on PRs by design — response bodies are deliberately not captured until the result-masking milestone lands.
- Manifest v1 supports a tagged request body shape `{"encoding": "json" | "form", ...}`. A `form` body declares a `fields` mapping of string→string that the executor dispatches as `application/x-www-form-urlencoded` (encoded by `httpx`, never hand-rolled) via the `send_json(form_body=...)` path. Placeholder substitution applies to each field value. Bare-body manifests (no `encoding` tag) continue to mean JSON for backwards compatibility (DL-0014).
- HTTP helper `send_json` now supports `application/x-www-form-urlencoded` request bodies via a `form_body` parameter (encoded by httpx's native form encoder; default `Content-Type` only set when the caller has not supplied one; mutually exclusive with `json_body`). Unlocks OAuth 2.0 token-exchange manifest steps without leaking transport details into the executor.
- Manifest v1 supports POST, PUT, PATCH, and DELETE methods with optional `headers` and JSON `body` fields, including `${...}` placeholder substitution in header values and body string leaves
- HTTP helper `send_json` for dispatching arbitrary-method requests with JSON body through the conformance engine
- Context helper `resolve_in_structure` for recursive placeholder resolution in nested JSON structures
- Example manifest `config/manifest-v1-token-exchange-example.json` demonstrating a two-step discovery + POST token exchange flow
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

- Bumped pinned third-party GitHub Actions to Node 24-compatible releases to clear GitHub's Node 20 deprecation warnings: `actions/checkout` → v6.0.2, `actions/upload-artifact` → v7.0.1, `astral-sh/setup-uv` → v8.1.0, `docker/setup-buildx-action` → v4.1.0, `docker/build-push-action` → v7.2.0. All pins remain full-SHA with the version tag in a trailing comment.
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
