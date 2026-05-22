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

## DL-0007: Start Manifest v0 As A Parser-Only Contract

Date: 2026-05-16
Status: Accepted

Decision: Introduce manifest schema version `v0` as a JSON-only, parser-only contract represented by frozen dataclasses. The first supported shape is intentionally narrow: HTTPS `GET` OpenID discovery requests, a small allowlist of response assertions, and an optional JWKS follow-up sourced from `response.body.jwks_uri`. Manifest parsing remains separate from execution.

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
Status: Accepted

Decision: Broaden section 6 to require Google-style docstrings on every private (`_`-prefixed) module-level function and method, not just public APIs. Trivial private helpers (fewer than ~10 lines with an obvious signature) may use a one-line summary; `Raises:` is still mandatory when the helper raises directly. The rule is enforced by code review, not by ruff (Google pydocstyle convention exempts privates from D1xx).

Rationale: The project goal for documentation is human readability *and* IDE hover support. A developer navigating `conformance/manifest.py` hovers private helpers far more often than public entry points — the parser's complexity lives in its validators and sub-parsers, not its two public functions. Without docstrings on privates, the IDE shows only a bare signature, defeating the stated goal. The one-line allowance prevents ceremonial bloat on trivial helpers where the signature speaks for itself.

## DL-0011: Keep Manifest HTTP Fetches Status-Agnostic

Date: 2026-05-22
Status: Accepted

Decision: Manifest execution must treat HTTP status codes as conformance evidence for `http_status` assertions, not as transport failures. JSON fetch helpers may return well-formed JSON object responses with 4xx or 5xx status codes; higher-level callers that require 2xx-only behaviour must enforce that policy explicitly.

Rationale: Manifest v0 accepts expected HTTP status codes from 100 to 599, so raising before assertion evaluation would make valid negative-response conformance tests impossible. Keeping status policy at the caller boundary lets the manifest engine evaluate standards behaviour while legacy smoke-check code can still fail fast for model-bank request errors.

## Open Decisions

- How manifest v0 evolves into later assertion evaluation, context carry-forward, and orchestration contracts.
- TestPlan schema boundaries: what belongs in the plan versus manifest versus reusable test data.
- Report JSON schema and certification summary structure.
- Exact Docker secure base image once the community secure image choice is confirmed.
- Whether local SQLite is required for the Phase 1 Django runtime or can be avoided.
- JWT/JWS library choice for the FAPI flow.
