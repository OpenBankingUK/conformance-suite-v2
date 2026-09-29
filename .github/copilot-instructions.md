# Copilot PR Review Instructions

This file guides the GitHub Copilot PR reviewer when commenting on pull requests in this repository. It is not a general engineering handbook — it is a review rubric.

## Project Framing

This repository is the **Open Banking UK Conformance Test Tool**, distributed as a Docker container to participants of the Open Banking UK ecosystem. It operates in a regulated financial services context. Security, correctness, and reliability are non-negotiable. Apply the rules below rigorously.

## Scope of review

CI already runs Ruff (lint + format), mypy strict, and pytest with coverage on every PR. **Do not re-report what those tools already catch mechanically** — import ordering, formatting, unused imports/variables, missing type annotations, or a missing module/class/function docstring (Ruff `D100`–`D104` already fails the build on these). A comment is only worth raising if it identifies something a human reviewer can see but a mechanical tool cannot:

- Material correctness or conformance defects (wrong behaviour, wrong spec conformance judgement).
- Security, privacy, or credential-handling defects.
- Reliability, concurrency, lifecycle, or error-handling defects.
- Compatibility problems or gaps in structured evidence/result output.
- Meaningful test gaps (behaviour that is genuinely untested, not just under a coverage number).

Avoid subjective style preferences, speculative refactors ("this could be cleaner"), and bare heuristics ("this function feels too long"). If you can't point to a concrete, actionable defect, don't comment.

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
- **Suppressions**: `# noqa:` and `# type: ignore` must carry an inline justification — this is not caught mechanically, so flag any bare suppression.
- **Dependencies**: New entries in `pyproject.toml` must be accompanied by a regenerated `uv.lock`. Flag unmaintained packages, known CVEs, or overly broad dependencies. Snyk runs on PRs; `high` or `critical` findings must not be merged.
- **Docker**: `Dockerfile` must use a non-root `USER`, pin the base image to a specific version tag, and avoid `COPY . .` without a comprehensive `.dockerignore`. No secrets in `ENV`.

## 2. Testing

- New business logic must have tests. Flag behaviour that is genuinely untested, not just a coverage-percentage shortfall.
- The supported suite is entirely deterministic and offline (`unit` and `component` categories only — no live-network or container-level pytest tier). `tests/conftest.py` enforces this with a session-wide socket guard that fails any connection to a non-loopback address, so a PR cannot quietly reintroduce live-network tests. Test behaviour through public interfaces; mock at external boundaries only (HTTP, file system, external services). A loopback fixture is only justified where socket, HTTP framing, TLS/mTLS, or certificate behaviour is itself under test.
- Coverage must not drop below 80%.
- CLI-facing and other result-producing tests must assert on the structured result file, not on incidental side effects.

## 3. Django

- Django ORM only. Raw queries require explicit justification.
- Development, test, and production settings must remain separated.
- Model changes must include migrations.
- User input must flow through a Django `Form` or DRF `Serializer` — no direct `request.POST` / `request.GET` access.
- Templates rely on auto-escaping. `mark_safe()` must never wrap user-supplied content.

## 4. Type Annotations & Python Version

- `Any` requires explicit justification — mypy strict requires annotations to exist, but does not stop someone from typing everything `Any`, so this is a real review call, not a mechanical one.

### Python version (`requires-python = ">=3.14.4"`)

This project targets **Python 3.14+**, so the following modern syntax is valid and MUST NOT be flagged as a SyntaxError or pre-3.x style:

- **PEP 758** — `except` and `except*` without parentheses around the exception tuple:
  ```python
  try:
      ...
  except ValueError, TypeError:   # valid on 3.14+
      ...
  ```
  This is the accepted PEP 758 syntax and is semantically identical to `except (ValueError, TypeError):`. It is NOT the Python 2 `except Exception, name:` binding form (which was removed in 3.x). Do not request a change purely on the basis that the syntax looks unfamiliar.
- **PEP 695** — `type` statements for generic aliases, and `class Foo[T]:` / `def f[T](x: T)` syntax.
- `from __future__ import annotations` is in use; string-form annotations and PEP 604 unions (`X | Y`) are expected.

If a syntax construct only became valid in a recent Python version, verify against `requires-python` in `pyproject.toml` before flagging it.

## 5. Documentation

Ruff's `D100`–`D104` already fail the build when a module, package, public class, function, or method is missing a docstring — don't re-report bare absence. Only comment when the *content* matters:

- Code implementing Open Banking, OAuth 2.0, OIDC, FAPI, JWKS, JWS, report, certification, or masking behaviour should name the relevant standard concept in its docstring or an adjacent comment, so a reviewer can trace behaviour back to the spec.
- Inline comments should explain security, compliance, or non-obvious design intent. Don't restate the next line of code.

There is no requirement for a specific docstring structure (Args/Returns/Raises sections) and no requirement for private (`_`-prefixed) helpers to carry docstrings — do not block a PR on either.

## 6. Changelog & Release Traceability

Branch naming, PR size, and push-vs-PR workflow are contributor/process guidance, not review-rubric material — branch protection rules already enforce the PR requirement mechanically. The one concrete release/traceability defect to flag here:

- `feat`, `fix`, `hotfix`, or `security` PRs must update `CHANGELOG.md` under `[Unreleased]` following [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). `docs`, `ci`, `test`, `chore` PRs are exempt unless behaviour changes. A missing entry is a real defect: it breaks the ability to reconstruct what changed in a release.

## 7. Docker

- Base image pinned to a specific version tag (e.g. `python:3.14.4-alpine3.22`).
- Prefer multi-stage builds.
- Application runs as non-root.
- `COPY` ordered to maximise layer cache (dependency files before source).
- Healthchecks recommended for interactive use.

## 8. Open Banking Domain

- Endpoint paths must match the Open Banking UK API specification exactly (case-sensitive).
- HTTP status codes in test assertions must match the specification's expected responses.
- OAuth 2.0 and OIDC flows must not deviate from the specification — flag shortcuts.
- JWT claim names must use the exact names from the specification.
- Domain terms (endpoint names, claim names, grant types) must match the Open Banking UK glossary.
- Hardcoded timeouts, retry counts, or date ranges affecting conformance judgement must be clearly documented.

---

## Always Approve

- Well-structured tests with clear assertions
- Dependency updates that have passed Snyk
- Documentation improvements

## Always Block

- `DEBUG = True` outside development settings
- Hardcoded secrets, tokens, or credentials
- `# noqa` or `# type: ignore` without inline justification
- Raw SQL constructed from user input
- `@csrf_exempt` without documented rationale
- Docker images running as root
- PRs that reduce test coverage below 80% without justification
- New dependencies not present in `uv.lock`
- Merges to `main` without a passing CI run (lint, offline unit/component tests, Docker build and health check)
- `feat` or `fix` PRs with no `CHANGELOG.md` entry under `[Unreleased]`
