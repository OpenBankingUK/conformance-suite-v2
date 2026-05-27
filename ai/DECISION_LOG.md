# Decision Log

This is the durable decision register for the FCS rebuild. Use it for choices that affect architecture, security, public interfaces, data formats, certification behaviour, or migration cost.

## Status Labels

- Proposed: suggested but not yet accepted.
- Accepted: current project direction.
- Superseded: replaced by a later entry.
- Deferred: intentionally postponed.

## DL-0001: Use `ai/` As The Adaptive Agent Source Of Truth

Date: 2026-05-16
Status: Accepted

Decision: Use the `ai/` workspace as the living source for AI-agent project context, handovers, development logs, and decision logs. The generated `docs/` folder remains reference material, but it should not constrain future decisions unless a human promotes a point into `ai/` or `.github/copilot-instructions.md`.

Rationale: The project is moving quickly and many early docs were generated from design material. Agents need a stable restart surface without freezing incidental AI interpretations as permanent requirements.

## DL-0002: Keep Security And Compliance In Repo Instructions

Date: 2026-05-16
Status: Accepted

Decision: Keep non-negotiable engineering rules in `.github/copilot-instructions.md`, including security, Django, testing, Docker, typing, and code-review requirements. Keep evolving design choices in this decision log.

Rationale: Regulated-context rules should be loaded consistently for every agent session. Evolving implementation direction needs a lighter process with rationale and status.

## DL-0003: Treat PR #1 And PR #2 As Early Foundation, Not Final Architecture

Date: 2026-05-16
Status: Accepted

Decision: Treat PR #1 project setup and PR #2 Ozone model-bank hello-world as confirmed progress and useful scaffolding. Do not assume their module boundaries are final for manifest execution, FAPI flows, reporting, REST API, or UI.

Rationale: The repository has working setup and a first Ozone path, but the source sprint plan still places manifest schema, assertion engine, FAPI client, orchestrator, reports, API, CLI, and UI in later milestones.

## DL-0004: Prefer Async Engine Direction

Date: 2026-05-16
Status: Accepted

Decision: Prefer an async-first execution engine using `asyncio` and `httpx.AsyncClient` for ASPSP calls and future independent group execution.

Rationale: The engine is I/O-bound, the FCS Q&A recommends async for concurrent groups, and Django can expose async views under ASGI for HTMX/SSE progress.

## DL-0005: Use Source Documents Plus Living Logs Over Generated Docs

Date: 2026-05-16
Status: Accepted

Decision: When generated documentation in `docs/` disagrees with original design material, user direction, or the `ai/` workspace, prefer the original design material, user direction, and the `ai/` workspace.

Rationale: The user explicitly wants the new AI-agent assistance files to become the flexible development guide and decision log so the project can adapt without being stuck to trivial generated choices.

## DL-0006: Use Configuration For Masking And Certification Criteria

Date: 2026-05-16
Status: Proposed

Decision: Sensitive field masking and certification eligibility criteria should be configuration-driven, not hardcoded in business logic.

Rationale: The FCS Q&A identifies masking by configured path, and the sprint plan requires certification eligibility assessment to be driven by configuration.

## DL-0007: Define Manifest v0 Schema Contract Via Parser

Date: 2026-05-16
Status: Accepted

Decision: Introduce manifest schema version `v0` as a JSON-only contract whose shape is defined and validated by a strict parser, represented by frozen dataclasses. Execution is intentionally decoupled from parsing but ships alongside it. The first supported shape is intentionally narrow: HTTPS `GET` OpenID discovery requests, a small allowlist of response assertions, and an optional JWKS follow-up sourced from `response.body.jwks_uri`.

Rationale: M2 needs an explicit data contract that can describe the current Ozone discovery/JWKS smoke path without replacing the runner or prematurely designing the full assertion engine, FAPI/OIDC token flows, REST API, or UI. Keeping unsupported request, assertion, and follow-up shapes rejected by the loader makes future schema evolution explicit.

## DL-0008: Adopt Google-Style Docstrings Enforced By Ruff

Date: 2026-05-21
Status: Accepted

Decision: Require Google-style docstrings for non-test modules and public Python APIs, and enforce the convention with ruff pydocstyle rules. Agents must add or update docstrings whenever they create or modify public modules, classes, functions, or methods.

Rationale: This regulated conformance tool must remain understandable to human maintainers, reviewers, and future agents without relying on chat history. Google-style docstrings are concise, IDE-friendly, and compatible with automated linting.

Consequences: Existing `conformance/` modules are backfilled now. Tests are exempt from docstring rules because their names and assertions document behaviour. Framework boilerplate may use concise module docstrings or narrow per-file ignores when docstrings would add ceremony rather than clarity.

## DL-0009: Attribute-Docstring Convention For Type Aliases

Date: 2026-05-21
Status: Accepted

Decision: Document module-level type aliases, `Literal` assignments, and `Final` constants using the attribute-docstring convention (a bare `"""..."""` string literal immediately following the assignment). Do not use `#` comments or fold descriptions into the module docstring.

Rationale: PEP 695 `type` statements cannot carry a function-style docstring, so the attribute-docstring form is the only way to satisfy this repository's section-6 requirement that module-level type aliases be documented. The Google Python Style Guide is silent on type aliases; this is an explicit project extension. Ruff B018 explicitly exempts the pattern. A Copilot PR review false-positive flagged these as useless expressions; the fix is to teach the reviewer (via `.github/copilot-instructions.md`) rather than change the code.

## DL-0010: Require Docstrings On Private Functions And Methods

Date: 2026-05-22
Status: Accepted (amended)

Decision: Require full Google-style docstrings on every private (`_`-prefixed) module-level function and method — a one-line summary plus `Args:` and `Returns:` sections whenever the signature has parameters or a non-`None` return type, and a `Raises:` section whenever the helper raises an exception directly. There is no carve-out for trivial helpers. Enforcement is mechanical: `interrogate` (configured in `pyproject.toml` with `fail-under = 100`, `ignore-private = false`, `ignore-semiprivate = false`) runs as a required CI step and fails the build if any definition is missing a docstring. Ruff's Google pydocstyle convention does not enforce `D1xx` on private names, so `interrogate` fills that gap.

Rationale: The project goal for documentation is human readability *and* IDE hover support. A developer navigating `conformance/manifest.py` hovers private helpers far more often than public entry points — the parser's complexity lives in its validators and sub-parsers, not its two public functions. Without docstrings on privates, the IDE shows only a bare signature, defeating the stated goal. Mechanical enforcement via `interrogate` removes ambiguity about which helpers need docstrings and prevents regressions without relying solely on code review.

## DL-0011: Keep Manifest HTTP Fetches Status-Agnostic

Date: 2026-05-22
Status: Accepted

Decision: Manifest execution must treat HTTP status codes as conformance evidence for `http_status` assertions, not as transport failures. JSON fetch helpers may return well-formed JSON object responses with 4xx or 5xx status codes; higher-level callers that require 2xx-only behaviour must enforce that policy explicitly.

Rationale: Manifest v0 accepts expected HTTP status codes from 100 to 599, so raising before assertion evaluation would make valid negative-response conformance tests impossible. Keeping status policy at the caller boundary lets the manifest engine evaluate standards behaviour while legacy smoke-check code can still fail fast for model-bank request errors.

## DL-0012: Introduce Manifest v1 Sequential Steps With Context Carry-Forward

Date: 2026-05-26
Status: Accepted

Decision: Introduce manifest schema version `v1` alongside v0. v1 replaces the `tests` + `followUp` shape with a flat `steps` array of sequential steps. Each step has an `id`, `name`, `request`, and `assertions`. Later steps can reference earlier step responses via `${steps.<id>.response.body.<path>}` placeholder syntax. v0 remains supported: the executor desugars v0 `followUp` to an equivalent v1 step internally, preserving v0 skip-on-fail semantics.

Rationale: The v0 `followUp` shape hardcodes `response.body.jwks_uri` as the only carry-forward mechanism. Every future flow (token endpoint from discovery, JWKS keys for JWS verification, etc.) needs generalised context. A sequential step model with explicit dot-path substitution gives a domain-agnostic engine that serves M3 (assertion engine + context), M4 (FAPI flow), and M5 (orchestrator) without introducing groups or concurrency prematurely.

Consequences:
- `conformance/context.py` provides `ExecutionContext`, `record_step`, and `resolve_placeholders`.
- Placeholder grammar is a simple regex. Supported: `request.method`, `request.url`; `response.status_code`, `response.body.<path>` (at least one sub-segment required). No header templating. No jinja, no jsonpath.
- Substitution applies only to the request `url` field in this milestone. Header/body templating arrives with M4/POST.
- Parser validates placeholder syntax and rejects forward references and duplicate step ids.
- HTTPS URL validation is deferred to execution time for URLs containing placeholders.
- v0 is retained for at least one more milestone.

## Open Decisions

- TestPlan schema boundaries: what belongs in the plan versus manifest versus reusable test data.
- Report JSON schema and certification summary structure.
- Exact Docker secure base image once the community secure image choice is confirmed.
- Whether local SQLite is required for the Phase 1 Django runtime or can be avoided.
- JWT/JWS library choice for the FAPI flow.
- Groups / two-phase setup+execution model (M5 orchestrator concern).
- Form-urlencoded body encoding for OAuth2 token exchange (next slice after DL-0013).
- Body size caps on response recording before report work begins.

## DL-0013: Manifest v1 Non-GET Methods With Header And Body Templating

Date: 2026-05-27
Status: Accepted

Decision: Extend the v1 manifest step request shape to support `POST`, `PUT`, `PATCH`, and `DELETE` methods alongside `GET`. Add two new optional fields to v1 step requests: `headers` (dict of string-valued headers validated against RFC 7230 token names) and `body` (arbitrary JSON value sent as `application/json`). Placeholder substitution (`${steps.<id>...}`) applies to header values and all string leaves of the body structure using the same grammar as URL substitution. Body is rejected on GET requests. The executor dispatches via `httpx.Client.request()` and records the fully-resolved request (post-substitution) in the step record.

Rationale: The M4 OAuth2 token-exchange step requires POST with a body built from the discovery response and a consent code. This is the smallest engine change that unblocks that step without committing to mTLS, JWS, form-urlencoded encoding, or callback handling. The engine remains domain-agnostic: it learns HTTP semantics, not OAuth2.

Key choices:
- **Methods supported:** GET, POST, PUT, PATCH, DELETE. Others (OPTIONS, HEAD) are intentionally excluded.
- **Body templating:** Substitution applies only to string leaves. Non-string leaves (numbers, booleans, null) pass through unchanged. A string leaf containing multiple placeholders and literal text is concatenated as a string.
- **Headers:** Single-valued strings only. RFC 7230 token validation on names. Empty values rejected. No list-valued headers in this slice.
- **Content-Type:** `application/json` is the only natively-formatted body type. Form-urlencoded and multipart are deferred to the next slice.
- **Masking:** Not yet applied to substituted secrets in the step record. Masking is a later milestone (PRD §"Result Outcomes").
- **GET body:** Rejected at parse time. Headers on GET are allowed.
- **DELETE body:** Allowed (some APIs use it for soft-delete payloads).

Open consequence: The real OAuth2 token exchange needs `application/x-www-form-urlencoded` encoding. This will require either a content-type discriminator on the body field or a typed body shape (e.g. `{"encoding": "form", "fields": {...}}`). Deferred to the next slice.
