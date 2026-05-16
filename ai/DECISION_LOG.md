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

## Open Decisions

- How manifest v0 evolves into later assertion evaluation, context carry-forward, and orchestration contracts.
- TestPlan schema boundaries: what belongs in the plan versus manifest versus reusable test data.
- Report JSON schema and certification summary structure.
- Exact Docker secure base image once the community secure image choice is confirmed.
- Whether local SQLite is required for the Phase 1 Django runtime or can be avoided.
- JWT/JWS library choice for the FAPI flow.
