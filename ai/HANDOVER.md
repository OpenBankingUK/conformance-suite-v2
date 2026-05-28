# Handover

Last updated: 2026-05-28

## Current State

The repository is on `develop` with PRs #1–#6 merged. The project has a working Python/Django scaffold, CI/E2E setup, Dockerfile, and a conformance engine that supports two manifest schema versions (v0 and v1).

M2 delivered manifest v0 (parser + executor). M3 delivered manifest v1 with sequential steps and context carry-forward via `${...}` placeholders. M4 prerequisite work (non-GET methods, headers, body templating) is complete, and v1 step bodies now carry a tagged encoding (JSON or form-urlencoded) for OAuth 2.0 token-exchange flows.

## What Was Just Added (Tagged Form-Urlencoded Body On Manifest v1 — DL-0014)

- `conformance/manifest.py`: Introduced `FormBody` (frozen dataclass; `fields: Mapping[str, str]` exposed via `MappingProxyType` so the parsed body is read-only after parse). New `ManifestBody = JsonBody | FormBody` union on `ManifestRequest.body`. Parser accepts two shapes: bare JSON value (DL-0013 back-compat) and tagged dict `{"encoding": "json"|"form", ...}`. Tagged JSON requires `value`; tagged form requires a non-empty `fields` mapping of string→string. Placeholder syntax is validated in every form field value and recursively in every JSON string leaf.
- `conformance/executor.py`: v1 step dispatch encodes `FormBody` as `application/x-www-form-urlencoded` via httpx, and only sets the `Content-Type` header when the manifest has not supplied one (case-insensitive per RFC 7230).
- Tests: Full coverage for the tagged shape — bare vs. tagged dispatch, JSON vs. form encoding, manifest-supplied Content-Type override, empty/missing/non-string `fields` rejection, unknown `encoding` rejection, placeholder validation inside form fields, and runtime immutability of `FormBody.fields`.
- `ai/DECISION_LOG.md`: DL-0014.

## Previously Added (M4 Prerequisite — Non-GET Methods + Header/Body Templating, DL-0013)

- `conformance/manifest.py`: Widened `RequestMethod` to `GET|POST|PUT|PATCH|DELETE`. Added `headers` and `body` optional fields to `ManifestRequest`. Helpers `_required_v1_method`, `_parse_v1_headers`, `_parse_v1_body`, `_validate_placeholders_in_structure`. RFC 7230 token validation on header names. Body rejected on GET.
- `conformance/context.py`: `resolve_in_structure` recursively applies placeholder resolution to all string leaves of a JSON structure.
- `conformance/http.py`: `send_json` dispatches arbitrary HTTP method with optional headers and JSON body; `get_json` delegates to it.
- `conformance/executor.py`: `_execute_v1_step` resolves placeholders in URL, headers, and body before dispatch.
- `config/manifest-v1-token-exchange-example.json`: Two-step discovery + POST token exchange example.

## Next Recommended Work

1. Tier 1 Ozone integration test: wire the existing v1 discovery + JWKS manifest against the real Ozone discovery URL, gated by `OZONE_DISCOVERY_URL` and `@pytest.mark.integration`.
2. mTLS client-certificate wiring in `conformance/http.py` (already scaffolded but unused by manifest executor). Unlocks tier 2 Ozone integration (token endpoint).
3. JWS request-object signing for FAPI flows.
4. Callback/redirect handling for authorization code flow.
5. Groups / two-phase setup+execution model (M5 orchestrator) — also tier 3 Ozone integration milestone.
6. Report JSON schema and certification summary.
7. REST API layer over the engine for the HTMX/Django UI.

## Files To Read First In A New Session

1. `.github/copilot-instructions.md`
2. `ai/README.md`
3. `ai/PROJECT_CONTEXT.md`
4. `ai/DECISION_LOG.md`
5. This file

## Open Questions

- When masking arrives (PRD §"Result Outcomes"), should it apply to the resolved request recorded in the step record, the raw manifest, or both?
- Does response-body recording need a size cap before report work begins? Currently unbounded.
- Should manifest v0 gain JSON Schema files before engine code, or should schema and parser continue to evolve together?
- Which JWT/JWS library should be adopted for the FAPI flow?
- What should the report JSON schema look like at the top level?
