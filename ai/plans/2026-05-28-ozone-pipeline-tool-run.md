# Plan: Real Conformance Tool Run Against Ozone In The Pipeline (Tier 1 E2E)

Date: 2026-05-28
Status: Proposed
Author: AI agent triage (Claude Opus 4.7 via GitHub Copilot)

## Goal

In CI, run **the actual conformance tool** — the published Docker image, invoked exactly as a participant would invoke it — against the live Ozone model-bank discovery URL, and assert on the structured `result.json` it produces. This closes the gap between the existing tier 1 *integration* tests (which import engine functions in-process) and a true end-to-end pipeline verification.

## Non-Goals

- Replacing or rewriting the existing in-process tier 1 integration tests in `tests/integration/test_ozone_tier1_discovery.py` — they stay; they're faster and catch engine-layer regressions earlier.
- Tier 2 / tier 3 work (mTLS, token exchange, FAPI flow) — those remain on `ai/plans/2026-05-28-ozone-integration-tiers.md`.
- Generalising the E2E workflow to multiple model banks — Ozone-only for now.
- Capturing or asserting on response bodies until the result-masking milestone lands.

## Source Documents And Decisions Used

- `ai/plans/2026-05-28-ozone-integration-tiers.md` — defines the four-tier ramp. This plan implements the missing "real tool, real pipeline" verification slice that tier 1 integration tests do not cover.
- `docs/TESTING_STRATEGY.md` — "E2E tests must assert on the structured result file produced by the tool, not on side effects." This plan honours that contract.
- `.github/copilot-instructions.md` §"What to Always Block" — "Merges to `main` without a passing E2E test run."
- `.github/workflows/e2e.yml` — existing E2E workflow pattern (Docker build, run image, parse `--json-report` results, upload artefact). Reuse this shape.
- `.github/workflows/ozone-integration.yml` — existing Ozone integration workflow. The new E2E job should live alongside it (same workflow file) so the Ozone-against-live story is in one place.
- `conformance/cli.py` — the tool's entry point: `conformance <config.json> [--manifest manifest.json]`. Exit codes: 0 pass, 1 conformance fail, 2 invalid input, 3 result-write error. Writes structured JSON to `config.result_output_path`.
- `conformance/model_bank_config.py` — JSON config shape (already in repo via `config/model-bank-example.json`).
- `config/manifest-v1-openid-jwks-example.json` — template for an Ozone tier 1 discovery-only manifest.
- DL-0011 — status-agnostic HTTP fetches. The pipeline test must accept that "tool ran successfully" and "all conformance checks passed" are two different outcomes.

## Proposed Design

### New committed artefacts

1. **`config/ozone-tier1-manifest.json`** — a v1 manifest file with a single discovery step and the three assertions already used by the in-process test (`http_status=200`, `issuer` is HTTPS URL, `jwks_uri` is HTTPS URL). The discovery URL is templated as `${OZONE_DISCOVERY_URL}` at the manifest level **only if** the manifest parser supports env interpolation; otherwise the workflow renders the manifest from a template via `envsubst` at job time. (Audit during implementation — most likely option B.)
2. **`config/ozone-tier1-config.json`** — minimal model-bank config matching `ModelBankConfig` shape, pointing `result_output_path` at `/work/result.json` inside the container. No TLS material (tier 1 is discovery-only over public HTTPS).

### Workflow changes

Add a **second job** to `.github/workflows/ozone-integration.yml`:

- Job name: `ozone-tier1-e2e` (alongside the existing `ozone-integration` job).
- Runs on the same triggers (PR into protected branches, nightly schedule, `workflow_dispatch`).
- Gated on `vars.OZONE_DISCOVERY_URL` being non-empty (skip with a notice when absent, mirroring the existing job's policy — fork PRs without variable access skip rather than fail).
- Steps:
  1. `actions/checkout` (pinned SHA — match the existing pins).
  2. `docker/setup-buildx-action` + `docker/build-push-action` to build the image with GHA cache, exactly as `e2e.yml` does (load locally, do not push).
  3. Render `config/ozone-tier1-manifest.json` from a template into a workspace temp dir, substituting `${OZONE_DISCOVERY_URL}` from the resolved URL (workflow_dispatch override → repo var → skip).
  4. `docker run --rm -v "$PWD/work:/work" ob-conformance-tool:tier1-${{ github.sha }} /work/config.json --manifest /work/manifest.json` — note the volume mount so the tool can write `result.json` back to the runner workspace.
  5. **Assert on `result.json` from the host**: parse the JSON, fail the job iff `status != "passed"` *or* the file is missing. Capture the file as an artefact unconditionally (so a failed run is debuggable).
  6. Post a per-run summary to `$GITHUB_STEP_SUMMARY` with: tool exit code, `result.status`, step count, list of failed steps (id + url + status_code, **no response bodies** until masking lands).
  7. `actions/upload-artifact` for `result.json` with a 30-day retention, matching `e2e.yml`.
- Non-blocking on PRs by design (continues the existing tier 1 posture). Re-evaluate gating after a few days of stable nightly runs.

### Why this shape (rather than `tests/e2e/test_ozone_tier1.py`)

- `e2e.yml` is built around a generic `tests/e2e/` pytest harness that does not yet exist (the workflow has an explicit `Check for E2E tests` step that skips when the directory is missing). Inventing that harness *just* for this slice would pull in scope.
- The conformance tool's own contract is "give me a config + manifest, get a `result.json`". Asserting directly on `result.json` exercises that contract more faithfully than wrapping the tool in pytest. A pytest harness can come later if/when multiple E2E manifests need orchestrating.
- Keeping the new job in `ozone-integration.yml` keeps all Ozone-against-live behaviour discoverable in one workflow file.

### Result-file contract being verified

The job verifies, end to end:

- The published Docker image starts cleanly with a non-root user (Dockerfile contract).
- The CLI accepts `config + --manifest` and exits 0 when checks pass.
- The result JSON is well-formed, written to the path declared in config, and has `status == "passed"` with the expected number of steps.
- The manifest-on-disk → parser → executor → HTTP → assertion-evaluator → result-writer pipeline produces a green result against a real network endpoint.

None of those are covered by the in-process tier 1 integration test.

## Security And Certification Implications

- Tier 1 uses only a public discovery URL — no secrets cross into the container. The discovery URL is a publishable value held as a **repo variable**, not a secret.
- No response bodies are recorded in the workflow log or summary until the result-masking milestone (PRD §"Result Outcomes") lands. `result.json` may include URLs and status codes; flag this in the upload step's retention so old artefacts get pruned.
- The Docker image continues to run as a non-root user (Dockerfile contract). The bind-mounted `/work` directory must be writable by the container user — verify in implementation; if not, use `chmod` on the host workspace pre-run rather than weakening the container user.
- Tier 1 deliberately does **not** include a JWKS follow-up (Ozone advertises JWKS on `keystore.openbankingtest.org.uk` whose chain requires the OB CA bundle). Adding JWKS is part of tier 2 alongside mTLS.

## Test Strategy

- The change *is* a test, so unit-test coverage for the new files is limited to: a quick JSON-schema lint of the committed config + manifest (could reuse existing `parse_manifest` in a unit test that loads the file from disk and asserts no `ManifestError`).
- The existing in-process tier 1 integration tests stay and continue to run via `make integration`. They provide a fast local feedback loop; the new pipeline job provides production-shape verification.
- Local re-run: a `make ozone-tier1-e2e` target that builds the image and runs the same `docker run` invocation, so developers can reproduce the CI job locally given an `OZONE_DISCOVERY_URL`.

## Rollout / Sequencing

1. **PR A (this plan):** commit the manifest+config templates, add the `ozone-tier1-e2e` job to `ozone-integration.yml`, add the Make target, add a unit test that parses the committed manifest, document in `docs/TESTING_STRATEGY.md`.
2. **Observe** for ~1 week of nightly runs to confirm Ozone discovery is stable and the job is reliable.
3. **PR B (follow-up):** flip the job from notice-skip to required check on PRs into `main`/`develop` if reliability supports it. Update `.github/copilot-instructions.md` §"What to Always Block" to name the new check.
4. **Tier 2 E2E** comes later, as part of the tier 2 integration work — same shape, plus mTLS material via GitHub Environment secrets.

## Open Questions

1. Does `parse_manifest` support env-variable interpolation inside manifest JSON, or must the workflow render the manifest from a template via `envsubst`? **Action:** audit in the first implementation step. Recommendation if no: template at workflow level — keeps the engine free of magic.
2. Does the container's non-root user have write access to a bind-mounted `/work`? **Action:** verify during implementation; if not, decide between `chown` on the host or a tmpfs strategy.
3. Should the job run in parallel with the existing `ozone-integration` job (faster feedback, two artefacts) or sequentially (cheaper minutes, single artefact)? **Recommendation:** parallel — they exercise different layers and a failure in one shouldn't mask the other.
4. Do we want `result.json` artefacts retained for 30 days (matches `e2e.yml`) or shorter to limit storage of URL-bearing data? **Recommendation:** 30 days for parity until masking ships, then revisit.

## Decision-Log Impact

If accepted, add `DL-XXXX: Pipeline Runs The Real Conformance Tool Against Live Ozone (Tier 1 E2E)` capturing:

- The separation of concerns: in-process tier 1 *integration* tests cover the engine; the new pipeline job covers the CLI + Docker + result-file contract.
- The decision to colocate Ozone-against-live behaviour in `ozone-integration.yml` rather than `e2e.yml`.
- The "skip-on-PR-when-vars-absent, fail-on-pipeline-error-when-vars-present" gating policy.
- The deferral of response-body capture until the result-masking milestone.
