# Handover

Last updated: 2026-05-27

## Current State

The repository is on `develop` with PRs #1–#6 merged. The project has a working Python/Django scaffold, CI/E2E setup, Dockerfile, and a conformance engine that supports two manifest schema versions (v0 and v1).

M2 delivered manifest v0 (parser + executor). M3 delivered manifest v1 with sequential steps and context carry-forward via `${...}` placeholders. M4 prerequisite work (non-GET methods, headers, body templating) is now complete.

## What Was Just Added (M4 Prerequisite — Non-GET Methods + Header/Body Templating)

- `conformance/manifest.py`: Widened `RequestMethod` to `GET|POST|PUT|PATCH|DELETE`. Added `headers` and `body` optional fields to `ManifestRequest`. New parser helpers: `_required_v1_method`, `_parse_v1_headers`, `_parse_v1_body`, `_validate_placeholders_in_structure`. RFC 7230 token validation on header names. Body rejected on GET. Placeholder validation recurses into headers and body leaves.
- `conformance/context.py`: Added `resolve_in_structure` — recursively applies placeholder resolution to all string leaves of a JSON structure.
- `conformance/http.py`: Added `send_json` — dispatches arbitrary HTTP method with optional headers and JSON body. `get_json` now delegates to `send_json`.
- `conformance/executor.py`: `_execute_v1_step` resolves placeholders in URL, headers, and body before dispatch. Uses `send_json` for all methods. Defence-in-depth guard rejects methods outside the supported set.
- `config/manifest-v1-token-exchange-example.json`: Two-step discovery + POST token exchange example.
- Tests: Full coverage for new parser cases (methods, headers, body, placeholder validation), `resolve_in_structure`, and executor POST dispatch.
- `ai/DECISION_LOG.md`: DL-0013.

## Next Recommended Work

1. Form-urlencoded body encoding for the OAuth2 token exchange (`application/x-www-form-urlencoded`).
2. mTLS client-certificate wiring in `conformance/http.py` (already scaffolded but unused by manifest executor).
3. JWS request-object signing for FAPI flows.
4. Callback/redirect handling for authorization code flow.
5. Groups / two-phase setup+execution model (M5 orchestrator).
6. Report JSON schema and certification summary.
7. REST API layer over the engine for the HTMX/Django UI.

## Files To Read First In A New Session

1. `.github/copilot-instructions.md`
2. `ai/README.md`
3. `ai/PROJECT_CONTEXT.md`
4. `ai/DECISION_LOG.md`
5. This file

## Open Questions

- Should the OAuth2 token-exchange step use form-urlencoded encoding via a content-type discriminator on the body field, or via a typed body shape? (Next slice.)
- When masking arrives (PRD §"Result Outcomes"), should it apply to the resolved request recorded in the step record, the raw manifest, or both?
- Does response-body recording need a size cap before report work begins? Currently unbounded.
- Should manifest v0 gain JSON Schema files before engine code, or should schema and parser continue to evolve together?
- Which JWT/JWS library should be adopted for the FAPI flow?
- What should the report JSON schema look like at the top level?
