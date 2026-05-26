# Handover

Last updated: 2026-05-26

## Current State

The repository is on `develop` with PRs #1–#6 merged. The project has a working Python/Django scaffold, CI/E2E setup, Dockerfile, and a conformance engine that supports two manifest schema versions (v0 and v1).

M2 delivered manifest v0 (parser + executor). M3 (in progress) adds manifest v1 with sequential steps and context carry-forward via `${...}` placeholders. The execution context module (`conformance/context.py`) accumulates step records so later steps can resolve earlier responses.

## What Was Just Added (M3 — Manifest v1)

- `conformance/context.py`: `ExecutionContext`, `record_step`, `resolve_placeholders`, `PlaceholderResolutionError`. Immutable step-record accumulation with dot-path resolution.
- `conformance/manifest.py`: extended to accept `schemaVersion: "v1"` with `steps` array. v1 step parsing with placeholder syntax validation, duplicate-id detection, and forward-reference rejection.
- `conformance/executor.py`: refactored to dispatch v0/v1. v0 desugars `followUp` to v1 steps internally (preserving skip-on-fail semantics). v1 executes all steps sequentially with context carry-forward.
- `config/manifest-v1-openid-jwks-example.json`: same discovery + JWKS flow expressed as two v1 steps.
- `tests/test_context.py`: full coverage for resolution (happy and error paths).
- `tests/test_manifest.py`: v1 parser tests (multi-step, duplicates, forward refs, malformed placeholders, HTTPS deferral).
- `tests/test_executor.py`: v1 execution tests (substitution, fail-and-continue, transport errors).
- `ai/DECISION_LOG.md`: DL-0012 — manifest v1 contract and v0 desugaring.
- `CHANGELOG.md`: updated under `[Unreleased]`.

## Next Recommended Work

1. Header and body templating for POST requests (M4 prerequisite).
2. Non-GET HTTP methods in manifest v1 (needed for token exchange in M4 FAPI flow).
3. Groups / two-phase setup+execution model (M5 orchestrator).
4. Report JSON schema and certification summary.
5. REST API layer over the engine for the HTMX/Django UI.

## Files To Read First In A New Session

1. `.github/copilot-instructions.md`
2. `ai/README.md`
3. `ai/PROJECT_CONTEXT.md`
4. `ai/DECISION_LOG.md`
5. This file

## Open Questions

- Should manifest v0 gain JSON Schema files before engine code, or should schema and parser continue to evolve together?
- Which JWT/JWS library should be adopted for the FAPI flow?
- What should the report JSON schema look like at the top level?
- What exact secure Docker base image should be used for the final community image?
- Should placeholder substitution extend to assertion `path`/`expected` values?
