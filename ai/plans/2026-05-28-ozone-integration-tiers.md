# Plan: Ozone Model-Bank Integration Tiers

Date: 2026-05-28
Status: Proposed
Author: AI agent triage (Claude Opus 4.7 via GitHub Copilot)

## Goal

Define a tiered, low-risk ramp for exercising the conformance engine against the real Ozone model bank, starting with what is possible today and ending at the M5 "first Ozone end-to-end test" milestone. Capture the env-var gating pattern and CI policy so live-network tests cannot accidentally break developer or CI workflows.

## Non-Goals

- Building the M5 orchestrator (groups, two-phase setup+execution).
- Adding mTLS, JWS, or callback handling — these are independent slices.
- Changing the Ozone hello-world smoke flow shipped in PR #2.
- Procurement / contract work on Ozone (out of engineering scope; see PRD critical-path blockers).
- Choosing the JWT/JWS library (deferred).

## Source Documents And Decisions Used

- `docs/FCS Rebuild - PRD v3 [DRAFT].md` §"Testing Approach" ("Integration tests will be run against the Ozone model bank."), §"Spec Version Support" (Ozone dependency), §"Critical Path Blockers" (Ozone contract).
- `ai/PROJECT_CONTEXT.md` — Ozone integration should be used early; M5 names "first Ozone end-to-end test".
- `ai/DECISION_LOG.md` — DL-0003 (hello-world is scaffolding, not final), DL-0011 (HTTP fetches are status-agnostic — important for negative-conformance Ozone responses).
- `ai/plans/2026-05-28-form-urlencoded-body.md` — Tier 2 depends on this slice landing.
- `conformance/ozone_client.py`, `conformance/runner.py`, `tests/test_ozone_runner.py` — current Ozone touchpoints.
- `docs/CICD_STRATEGY.md` — admin override pattern when Ozone misbehaves.

## Proposed Design

### The four tiers

| Tier | What runs against real Ozone | Prerequisite work | Status today |
| --- | --- | --- | --- |
| **0** | Model-bank hello-world smoke flow (PR #2). | None. | **Live** — already shipped. |
| **1** | v1 manifest: OpenID discovery + JWKS follow-up against Ozone's real discovery URL. | None — engine is ready. | **Available now**, not wired. |
| **2** | v1 manifest: discovery + OAuth2 token exchange against Ozone's token endpoint. | Form-urlencoded body slice + mTLS wiring in `conformance/http.py` (transport cert + key from config). | Blocked on those two slices. |
| **3** | Full FAPI 1 Advanced hybrid flow end-to-end: discovery → JWS request object → PSU auth (manual redirect) → callback → token exchange → AIS smoke call. | JWS signing + callback handler + minimal orchestrator. | M5 milestone. |

### Gating pattern (all tiers)

- All live-network tests use `@pytest.mark.ozone` (distinct from the existing `integration` marker used for offline Django/DB tests).
- Each tier opts in via a tier-specific env var so a developer can run tier 1 without provisioning mTLS material:
  - Tier 0: `OZONE_MODEL_BANK_URL` (already exists in the runner config).
  - Tier 1: `OZONE_DISCOVERY_URL`.
  - Tier 2: `OZONE_DISCOVERY_URL` + `OZONE_MTLS_CERT` + `OZONE_MTLS_KEY` + `OZONE_MTLS_CA_BUNDLE` + `OZONE_CLIENT_ID`.
  - Tier 3: tier-2 vars + `OZONE_REDIRECT_URI` + `OZONE_SIGNING_KEY` + `OZONE_SIGNING_KID`.
- A small `tests/_ozone.py` helper provides `requires_ozone(tier: int)` returning `pytest.mark.skipif(...)` so the gating reason is consistent and self-documenting. Tests skip (not error) when env vars are absent — never silently pass.
- Live-network tests live under `tests/integration/` to keep `pytest -m "not ozone"` (the default for local runs) free of network calls while still exercising the existing offline Django integration tests.

### CI policy

- Unit job (existing): never runs live-network tests.
- A new optional **Ozone Integration** workflow runs on a schedule (nightly) and on manual `workflow_dispatch`, with Ozone credentials provided via GitHub Environment secrets. It is non-blocking for PR merges (matches the admin-override allowance already in `docs/CICD_STRATEGY.md` for Ozone misbehaviour).
- Tier 0 and tier 1 jobs can run without secrets if a public Ozone discovery URL is available — to be confirmed during tier 1 implementation. If yes, run on every PR. If no, nightly only.
- No live-network test runs in the local-default `make check`. A `make integration` target runs tier 0 + tier 1 when env vars are present.

### Test shape (tier 1, illustrative)

- Reuse the existing `config/manifest-v1-openid-jwks-example.json` shape but parameterise the discovery URL from `OZONE_DISCOVERY_URL`.
- Assert: HTTP 200 on discovery, JSON body, presence of `issuer` / `jwks_uri` / `token_endpoint` / `authorization_endpoint`, JWKS follow-up returns a JWKS document with at least one `keys[*]` entry. Do **not** assert exact issuer / key id — those are environment-specific.
- One additional negative test: assert status-agnostic behaviour (DL-0011) by pointing the manifest at a deliberately invalid path under the Ozone host and confirming the engine records the 4xx without raising.

### Reporting back to PRD open todos

- Tier 3 work will produce the evidence needed to close PRD's "Headless PSU auth feasibility" open todo for Ozone specifically.

## Security And Certification Implications

- mTLS material (tier 2+) must come from environment / volume-mounted PEM files. Never commit. CI must source from GitHub Environment secrets, not repo variables.
- Live-network responses may contain real-looking PII shapes even in a sandbox. Treat response bodies in integration tests as masking-sensitive: do not log raw response bodies in CI artefacts until the masking milestone (PRD §"Result Outcomes") lands.
- DL-0011 already mandates status-agnostic HTTP fetches — tier 1's negative test pins this behaviour against real network conditions.

## Test Strategy

- Tier 0 / 1 / 2 / 3 each get a top-level test module (`tests/integration/test_ozone_tier1_discovery.py`, etc.) marked `@pytest.mark.ozone`.
- Coverage requirements (≥ 80%) are measured on unit tests only — integration tests are exempt because they require network and credentials.
- A short fixture in `tests/integration/conftest.py` validates env vars are present and well-formed (e.g. `OZONE_DISCOVERY_URL` parses as HTTPS), skipping the whole module otherwise with a clear reason.

## Rollout / Sequencing

1. **Tier 1 in the same PR as the form-urlencoded slice** (or immediately before it). Minimal code: one integration test module + `requires_ozone` helper + Makefile target + a documentation paragraph in `docs/TESTING_STRATEGY.md`.
2. **Tier 2** after form-urlencoded **and** mTLS wiring land — both are independently planned. Order doesn't matter; whichever lands second triggers tier 2.
3. **Tier 3** as part of M5.
4. Optional Ozone Integration CI workflow lands with tier 1 but starts in `workflow_dispatch`-only mode until tier 1 has been green locally for a few days.

## Open Questions

1. Is Ozone's discovery endpoint publicly reachable without mTLS? If yes, tier 1 can run in every PR. If no, nightly only. **Action:** confirm during tier 1 implementation.
2. Should integration-test artefacts (raw responses, captured headers) be uploaded by CI? Recommendation: no, until masking is in place.
3. Should we record the Ozone API spec version under test as a manifest field, or as CI matrix dimensions? Defer to tier 2.
4. Does the existing PR #2 hello-world flow already count as "tier 0 live" in CI, or is it currently local-only? **Action:** audit in tier 1 PR; if local-only, fold into the nightly workflow.

## Decision-Log Impact

If accepted, add `DL-0015: Tiered Ozone Integration Ramp With Env-Var Gating` (Accepted) capturing:
- The four-tier ramp.
- The `@pytest.mark.ozone` + tier-specific env var gating pattern (kept distinct from the offline `integration` marker).
- The CI policy: unit tests never touch the network; integration job is scheduled / dispatch-only and non-blocking.
- The masking-sensitive treatment of integration-test artefacts until the masking milestone lands.
