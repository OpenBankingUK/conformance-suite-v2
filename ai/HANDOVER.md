# Handover

Last updated: 2026-05-28

## GitHub Actions — Variables, Forks & `workflow_dispatch` (Gotchas)

While wiring `.github/workflows/ozone-integration.yml` (PR #14), several non-obvious GitHub behaviours cost time. Record them so future agents don't loop:

- **Timing is the most common cause of empty `vars.X` in logs (confirmed on PR #14).** A variable created *after* a job has already started will not appear in that job's environment — `vars` are resolved at job-startup time and not re-evaluated. **First diagnostic step is always to re-run the job** after creating/changing a variable. Only investigate scoping or fork issues if a fresh run still shows an empty value.
- **Repository-level vs. Environment-level variables are separate namespaces.** `${{ vars.X }}` only reads the variable at the scope the job is bound to. A job with no `environment:` directive reads **only** repository variables (Settings → Secrets and variables → Actions → **Variables** tab). A job with `environment: foo` reads the environment-scoped value (which can be empty even if a repo-level one with the same name exists). The current workflow has no `environment:` directive — `OZONE_DISCOVERY_URL` **must** be at repo scope.
- **`workflow_dispatch` "Run workflow" button only appears once the workflow file is on the repository's default branch.** While the workflow lives only on a feature branch, the manual-trigger button is invisible in the Actions UI. This is intentional GitHub behaviour. Merging to `develop` makes the button appear.
- **This repo is a fork of an upstream.** Fork-specific Actions controls:
  - Settings → Actions → General → **"Fork pull request workflows from outside collaborators"** governs whether PRs from *forks of this repo* run with secrets/vars. It does **not** apply to same-repo branch PRs (PRs from a branch pushed directly to `OpenBankingUK/conformance-suite-v2`), which always receive secrets/vars. Fork status was *not* the cause of the PR #14 empty-vars symptom.
- **Approval gate as last resort.** If the integration test must run on fork PRs, use an `environment:` with required reviewers so secrets/vars are gated by a human approval, rather than relying on contributor-list permissions.

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

1. Tier 1 Ozone integration test: wire the existing v1 discovery + JWKS manifest against the real Ozone discovery URL, gated by `OZONE_DISCOVERY_URL` and `@pytest.mark.ozone`.
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
