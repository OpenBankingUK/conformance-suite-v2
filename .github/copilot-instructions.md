# Copilot PR Review Instructions

This file guides the GitHub Copilot PR reviewer when commenting on pull requests in this repository. It is not a general engineering handbook — it is a review rubric.

## Project Framing

This repository is the **Open Banking UK Conformance Test Tool**, distributed as a Docker container to participants of the Open Banking UK ecosystem. It operates in a regulated financial services context. Security, correctness, and reliability are non-negotiable. Apply the rules below rigorously.

---

## 1. Security — Highest Priority

- **Injection** (OWASP A03): No raw SQL. Use Django ORM. No `str.format()` or f-strings building SQL, shell commands, or file paths from user input.
- **Broken Authentication** (OWASP A07): Auth-required views must have `@login_required` or `LoginRequiredMixin`.
- **Sensitive Data Exposure** (OWASP A02): No secrets, API keys, tokens, or credentials in code or config. `.env` files must never be committed. Result files must not include internal system details.
- **Security Misconfiguration** (OWASP A05): `DEBUG = False` in production settings. `ALLOWED_HOSTS` explicitly set. `SECRET_KEY` from environment.
- **CSRF**: POST views must use `{% csrf_token %}`; HTMX POSTs must include CSRF headers. `@csrf_exempt` requires strong, documented justification.
- **Open Redirect**: `HttpResponseRedirect` must only target validated internal paths.
- **Path Traversal**: File paths derived from user input must be validated against an allowed root using `pathlib.Path.resolve()`.
- **Hardcoded secrets**: Flag any string literals that look like tokens, keys, or passwords.
- **Dependencies**: New entries in `pyproject.toml` must be accompanied by a regenerated `uv.lock`. Flag unmaintained packages, known CVEs, or overly broad dependencies. Snyk runs on PRs; `high` or `critical` findings must not be merged.
- **Docker**: `Dockerfile` must use a non-root `USER`, pin the base image to a specific version tag, and avoid `COPY . .` without a comprehensive `.dockerignore`. No secrets in `ENV`.

## 2. Testing

- New business logic must have tests.
- Tests must use one of the pytest markers declared in `pyproject.toml`: `@pytest.mark.unit`, `@pytest.mark.integration`, `@pytest.mark.ozone`, or `@pytest.mark.e2e`.
- Test behaviour through public interfaces. Mock at external boundaries only (HTTP, file system, external services).
- Coverage must not drop below 80%.
- E2E tests must assert on the structured result file, not on side effects.

## 3. Django

- Django ORM only. Raw queries require explicit justification.
- Development, test, and production settings must remain separated.
- Model changes must include migrations.
- User input must flow through a Django `Form` or DRF `Serializer` — no direct `request.POST` / `request.GET` access.
- Templates rely on auto-escaping. `mark_safe()` must never wrap user-supplied content.

## 4. Type Annotations

- Public functions, methods, and class attributes must have complete type annotations.
- `Any` requires explicit justification.
- Code must pass mypy strict.

## 5. Code Quality

- Code must comply with the project's `ruff` config. `# noqa:` requires an inline justification.
- Functions over ~50 lines or nesting deeper than 3 levels are red flags — suggest decomposition or early returns.
- Naming follows PEP 8. Domain terms must match the Open Banking UK glossary (endpoint names, claim names, grant types).
- No dead code: flag unused imports/variables/functions and commented-out code.

## 6. Documentation

- Every non-test Python module must have a docstring explaining its role.
- Every public class, dataclass, function, method, and module-level type alias must have a Google-style docstring (`Args:`, `Returns:`, `Raises:` where useful).
- Every private (`_`-prefixed) module-level function and method must also have a full Google-style docstring. No carve-out for "trivial" helpers. CI runs `interrogate` at 100% (`ignore-private = false`); missing docstrings fail the build.
- Code implementing Open Banking, OAuth 2.0, OIDC, FAPI, JWKS, JWS, report, certification, or masking behaviour should name the relevant standard concept in the docstring or an adjacent comment.
- Inline comments should explain security, compliance, or non-obvious design intent. Don't restate the next line of code.

**Attribute docstrings for type aliases and module-level constants** are required and B018-exempt:

```python
type JsonValue = str | int | float | bool | None | list[JsonValue] | dict[str, JsonValue]
"""Recursive JSON value accepted from config files and HTTP responses."""

CheckStatus = Literal["passed", "failed"]
"""Outcome values emitted by smoke-check steps and summaries."""
```

Do not flag these as B018 violations or request their removal.

## 7. Git Flow & PR Hygiene

- Source branch must use `feature/`, `bugfix/`, `release/`, or `hotfix/` prefix.
- PRs touching more than ~500 lines across unrelated concerns should be questioned.
- PR title should reference the issue number (e.g. `feat(tests): add OAuth2 PKCE conformance tests (#42)`).
- Flag any direct push to `main` or `develop`.
- `feat`, `fix`, `hotfix`, or `security` PRs must update `CHANGELOG.md` under `[Unreleased]` following [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). `docs`, `ci`, `test`, `chore` PRs are exempt unless behaviour changes.

## 8. Docker

- Base image pinned to a specific version tag (e.g. `python:3.14-alpine3.22`).
- Prefer multi-stage builds.
- Application runs as non-root.
- `COPY` ordered to maximise layer cache (dependency files before source).
- Healthchecks recommended for interactive use.

## 9. Open Banking Domain

- Endpoint paths must match the Open Banking UK API specification exactly (case-sensitive).
- HTTP status codes in test assertions must match the specification's expected responses.
- OAuth 2.0 and OIDC flows must not deviate from the specification — flag shortcuts.
- JWT claim names must use the exact names from the specification.
- Hardcoded timeouts, retry counts, or date ranges affecting conformance judgement must be clearly documented.

---

## Always Approve

- Well-structured tests with clear assertions
- Smaller, focused PRs
- Dependency updates that have passed Snyk
- Documentation improvements
- Attribute docstrings (`"""..."""`) immediately following a module-level assignment or `type` statement

## Always Block

- `DEBUG = True` outside development settings
- Hardcoded secrets, tokens, or credentials
- `# noqa` or `# type: ignore` without inline justification
- Raw SQL constructed from user input
- `@csrf_exempt` without documented rationale
- Docker images running as root
- New or modified Python modules, classes, functions, or methods — public or private — without a Google-style docstring
- PRs that reduce test coverage below 80% without justification
- New dependencies not present in `uv.lock`
- Merges to `main` without a passing E2E run
- `feat` or `fix` PRs with no `CHANGELOG.md` entry under `[Unreleased]`
