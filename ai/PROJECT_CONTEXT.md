# Project Context For AI Agents

Last updated: 2026-05-16

## Product Summary

The FCS rebuild is the Open Banking UK Conformance Test Tool rebuilt as a local hardened Docker container. Participants will run it against their own implementations to verify conformance with Open Banking UK API standards.

Phase 1 targets a local, secure, participant-run tool with manifest-driven tests, FAPI/OIDC flows, structured reports, certification eligibility assessment, REST API, CLI, and a minimal web UI.

Phase 2 is expected later and should not distort Phase 1 unless a design choice would obviously block future hosted or multi-run operation.

## Current Repository Progress

Current branch observed: `develop`.

PR #1, `Feature/project setup`, established the Python/Django scaffold, CI and E2E workflows, Dockerfile, Makefile, uv lockfile, pre-commit hooks, detect-secrets baseline, Django settings, and initial smoke/health tests.

PR #2, `Feature/ozone model bank hello world`, added the first conformance package modules: CLI, model-bank config parsing, Ozone client, result modelling, runner, model-bank example config, README usage, and unit tests around those behaviours.

This means the project is beyond empty scaffold but still early. It has a model-bank hello-world path, not the full manifest schema, assertion engine, FAPI flow, report generator, REST API, or UI.

## Sprint Plan Anchor

The sprint plan targets Phase 1 code complete by December 2026 and beta with the first ASPSP in January 2027. Key review gates are July, October, and December.

Near-term milestones:

- M1: Project scaffolding and Docker security baseline.
- M2: Config and manifest schema, JSON schemas, loader, manifest parser, migration script design.
- M3: Assertion engine and context carry-forward.
- M4: FAPI HTTP client with mTLS, OAuth2/OIDC hybrid flow, JWS, token exchange, callback.
- M5: Engine orchestrator, group sequencing, consent collection, first Ozone end-to-end test.

Decision logs are expected review-gate artefacts, especially by the July and October reviews.

## Design Intent From Source Documents

- Phase 1 is local-first and containerised.
- Security baseline is a day-one concern, not a late hardening retrofit.
- The manifest schema should be a clean, versioned design that can evolve through real migration and Standards feedback.
- The system should be domain-aware but avoid overfitting to generated docs or early incidental scaffolding.
- The JSON report is the certification artefact. HTML is useful, but lower priority if schedule pressure appears.
- Mandatory tests, optional tests, eligibility, masking, and result integrity are certification-critical.
- Ozone integration is available and should be used early to validate behaviour.

## Current Architectural Direction

- Python 3.14.x (>=3.14.4), Django, HTMX, pytest, ruff, mypy, uv.
- Async-first engine direction using `asyncio` and `httpx.AsyncClient` for I/O-bound ASPSP calls.
- Django should run under ASGI for async views and SSE progress.
- Use local SQLite only if Django requires a database in Phase 1.
- UI should call the engine in-process for Phase 1; REST API is a separate programmatic surface, not an internal UI dependency.
- Use SSE for live progress updates rather than polling or Django Channels unless future requirements force a queue-backed design.
- Use PEM file paths directly for transport TLS where possible. Use `cryptography` for programmatic certificate/key parsing when needed.
- Use JSON structured logging for security-relevant events and operational traceability.

## Important Non-Goals And Cautions

- Do not let generated `/docs` decisions override source design documents or the living `ai/` workspace.
- Do not treat the current hello-world Ozone client as the final FAPI client architecture.
- Do not build Phase 2 hosting complexity into Phase 1 unless it reduces clear future migration risk without increasing current risk.
- Do not hardcode certification criteria or masking rules that should be configuration-driven.
- Do not write secrets, credentials, tokens, or participant-sensitive details into logs, reports, examples, or tests.

## What To Read Before Starting Work

1. `.github/copilot-instructions.md`
2. `ai/README.md`
3. `ai/DECISION_LOG.md`
4. `ai/HANDOVER.md`
5. The code and tests directly affected by the task
