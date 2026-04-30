# GitHub Copilot Instructions

## Project Context

This repository contains the **Open Banking UK Conformance Test Tool** — a standards testing application distributed as a Docker container to participants of the Open Banking UK ecosystem. Participants run the container against their own implementations to verify conformance with Open Banking UK API standards.

**This project operates in a regulated financial services context. Security, correctness, and reliability are non-negotiable.**

### Technology Stack

- **Language**: Python 3.12
- **Web framework**: Django (with HTMX for frontend interactions)
- **Package manager**: `uv` (lockfile-based, reproducible installs)
- **Container**: Docker (non-root, minimal base image)
- **Test framework**: pytest + pytest-django
- **Linting / formatting**: ruff
- **Type checking**: mypy (strict mode)
- **CI/CD**: GitHub Actions
- **Security scanning**: Snyk (linked via Snyk portal — runs automatically on PRs as a GitHub status check)

---

## Table of Contents

- [Project Context](#project-context)
- [Code Review Instructions](#code-review-instructions)
  - [1. Security — Highest Priority](#1-security--highest-priority)
  - [2. Testing Standards](#2-testing-standards)
  - [3. Django-Specific Standards](#3-django-specific-standards)
  - [4. Type Annotations](#4-type-annotations)
  - [5. Code Quality Standards](#5-code-quality-standards)
  - [6. Git Flow & PR Hygiene](#6-git-flow--pr-hygiene)
  - [7. Docker & Container Standards](#7-docker--container-standards)
  - [8. Open Banking Domain Standards](#8-open-banking-domain-standards)
  - [9. What to Always Approve](#9-what-to-always-approve)
  - [10. What to Always Block](#10-what-to-always-block)
- [General Coding Preferences](#general-coding-preferences)

---

## Code Review Instructions

When reviewing pull requests in this repository, apply the following standards rigorously.

---

### 1. Security — Highest Priority

This project is used by financial services participants. Security flaws can have serious downstream consequences.

**Always check for:**

- **Injection vulnerabilities** (OWASP A03): No raw SQL queries. Use Django ORM exclusively. No `str.format()` or f-strings constructing SQL, shell commands, or file paths from user input.
- **Broken Authentication** (OWASP A07): Verify that any authentication-required views have `@login_required` or equivalent. Check for missing `LoginRequiredMixin`.
- **Sensitive Data Exposure** (OWASP A02): No secrets, API keys, tokens, or credentials in code or config files. `.env` files must never be committed. Check that any data written to the result file does not include internal system details.
- **Security Misconfiguration** (OWASP A05): `DEBUG = False` in production settings. `ALLOWED_HOSTS` must be explicitly set. `SECRET_KEY` must come from environment variables.
- **CSRF** (OWASP A01 / Django specific): All POST views must use `{% csrf_token %}`. HTMX POSTs must include CSRF headers. `@csrf_exempt` must never be used without strong justification and a comment.
- **Open Redirect**: `HttpResponseRedirect` must only redirect to safe internal paths. Never redirect to user-supplied URLs without validation.
- **Path Traversal**: Any file path derived from user input must be validated and restricted to an allowed directory. Use `pathlib.Path.resolve()` and verify it is within the expected root.
- **Hardcoded secrets**: Flag any string literals that look like tokens, keys, or passwords. `SECRET_KEY` and similar values must come from environment variables or GitHub Secrets.
- **Dependency concerns**: If new packages are added to `pyproject.toml`, check that the `uv.lock` file has been regenerated and committed. Flag any packages that are unmaintained, have known CVEs, or are overly broad in scope. Note that Snyk will automatically flag dependency vulnerabilities on the PR via the Snyk portal integration — code with `high` or `critical` findings must not be merged. If uncertain how to resolve a security issue, consult the Security team.
- **Docker security**: The `Dockerfile` must use a non-root `USER`. No `COPY . .` without a comprehensive `.dockerignore`. No secrets in `ENV` instructions. Pin the base image to a specific digest or version tag.

---

### 2. Testing Standards

- Every new function, view, or method with meaningful business logic **must** have accompanying tests.
- Tests must use appropriate pytest markers: `@pytest.mark.unit`, `@pytest.mark.integration`, or `@pytest.mark.e2e`.
- Tests must exercise behaviour through public interfaces, not internal implementation details.
- Avoid mocking internal collaborators; mock at external boundaries (HTTP, file system, DB calls to external services).
- Test data must use `factory_boy` factories, not hardcoded fixtures.
- Coverage must not drop below 80%. Flag any PR that reduces coverage without justification.
- E2E tests must assert on the structured result file produced by the tool, not on side effects.

---

### 3. Django-Specific Standards

- **ORM over raw SQL**: Always use Django ORM query methods. Raw queries require explicit justification.
- **Settings split**: Development, test, and production settings must remain separated. Never merge production settings into development.
- **Migrations**: Model changes must include accompanying migrations. Check that migrations are reviewed for correctness and don't cause data loss.
- **Forms and serialisers**: All user input must pass through a Django `Form` or DRF `Serializer` before processing. No direct access to `request.POST` or `request.GET` without validation.
- **Template safety**: Templates should use Django's auto-escaping. `mark_safe()` must never be used with user-supplied content.

---

### 4. Type Annotations

- All public functions, methods, and class attributes **must** have complete type annotations.
- No `Any` type unless unavoidable and explicitly justified with a `# type: ignore[type-arg]` comment.
- Ensure mypy would pass with `strict = true` on any new code.

---

### 5. Code Quality Standards

- **ruff**: Code must comply with the project's `ruff` configuration. Flag any disabled rules (`# noqa:`) that don't have a clear inline justification.
- **Function length**: Functions over ~50 lines are a red flag — suggest decomposition.
- **Deep nesting**: More than 3 levels of nesting is a red flag — suggest early returns or extraction.
- **Naming**: Follow Python conventions (PEP 8). Domain terms must match the Open Banking UK glossary (endpoint names, claim names, grant types, etc.).
- **No dead code**: Flag unused imports, variables, functions, and commented-out code.

---

### 6. Git Flow & PR Hygiene

- **Branch naming**: Verify the source branch follows the naming convention: `feature/`, `bugfix/`, `release/`, or `hotfix/` prefixes. Flag non-conforming branch names.
- **PR scope**: PRs should be focused. A PR that touches more than 500 lines across unrelated concerns should be questioned.
- **PR title**: Should be clear and reference the issue number (e.g. `feat(tests): add OAuth2 PKCE conformance tests (#42)`).
- **No direct pushes to main or develop**: If the PR bypassed the normal flow, flag it.
- **Changelog**: Every PR that introduces a `feat`, `fix`, `hotfix`, or `security` change **must** include an entry in `CHANGELOG.md` under the `[Unreleased]` section, in the appropriate subsection (`Added`, `Changed`, `Fixed`, `Security`, etc.), following [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) format. `docs`, `ci`, `test`, and `chore` PRs are exempt unless they change observable behaviour. Flag any PR of type `feat` or `fix` that does not update `CHANGELOG.md`.

---

### 7. Docker & Container Standards

- Base image must be pinned to a specific version tag (e.g. `python:3.12-slim-bookworm`, not `python:latest`).
- Multi-stage builds are preferred to keep the final image minimal.
- The application process must run as a non-root user.
- `COPY` instructions should be ordered to maximise layer cache efficiency (copy dependency files first, then source code).
- Healthcheck instructions are recommended for interactive container use.

---

### 8. Open Banking Domain Standards

When reviewing code that implements or tests Open Banking standards:

- Endpoint paths must match the Open Banking UK API specification exactly (case-sensitive).
- HTTP status codes in test assertions must match the specification's expected responses.
- OAuth 2.0 and OIDC flows must not deviate from the specification — flag any shortcuts.
- Claim names in JWT handling must use the exact names from the specification.
- Any hardcoded timeout values, retry counts, or date ranges that affect conformance judgement must be clearly documented.

---

### 9. What to Always Approve

- Well-structured tests with clear assertions
- Smaller, focused PRs that do one thing well
- Dependency updates that have passed Snyk scanning
- Documentation improvements

---

### 10. What to Always Block

- Any `DEBUG = True` outside of development settings
- Any hardcoded secret, token, or credential
- `# noqa` or `# type: ignore` without an inline justification comment
- Raw SQL string construction from user input
- `@csrf_exempt` without a documented security rationale
- Docker images running as root
- PRs that reduce test coverage below 80% without justification
- New dependencies not present in `uv.lock`
- Merges to `main` without a passing E2E test run
- `feat` or `fix` PRs with no entry added to `CHANGELOG.md` under `[Unreleased]`

---

## General Coding Preferences

- Prefer `pathlib.Path` over `os.path` for file operations.
- Prefer `httpx` over `requests` for HTTP calls (async-compatible).
- Use `dataclasses` or `pydantic` for data transfer objects; avoid untyped dictionaries for structured data.
- Configuration should flow through environment variables or a config file; never through default mutable arguments.
- Follow the principle of least privilege: functions should receive only what they need.
- Prefer explicit over implicit. Side effects should be obvious from function signatures.
