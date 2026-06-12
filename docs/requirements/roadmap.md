# Roadmap and backlog

This roadmap is intentionally high-level. Individual implementation work should be split into agent-ready slices using `agent-slice-template.md` and tracked against stable IDs from `requirements.yml`.

## Horizon 0: Planning foundation

Outcome: make the product direction and delivery model explicit enough for humans and AI agents.

- Establish `docs/requirements/` as the source of truth.
- Maintain `requirements.yml` with stable IDs and statuses.
- Seed the v4.0.1 AIS coverage ledger.
- Record open decisions and owners/decision paths.
- Use the agent slice template for future implementation issues.

## Horizon 1: v4.0.1 AIS certification baseline

Outcome: know exactly what blocks the first certifiable suite.

- Build a complete prior-FCS parity inventory for AIS.
- Bridge current v4 AIS legacy benchmark evidence into the v4.0.1 target.
- Confirm the Standards-owned v4.0.1 AIS mandatory matrix.
- Map each mandatory requirement to v2 manifest steps, assertions, auth bundle requirements, tests, and validator expectations.
- Classify missing primitives and decide whether to implement, waive, or supersede old behaviour.
- Keep the suite `partial` until the promotion gate is satisfied.

## Horizon 2: Auth and data-shape completeness

Outcome: support the auth and content variants required by real certification.

- Model multi-auth bundles explicitly across plan preview, manifest metadata, execution, results, and coverage ledgers.
- Preserve and extend basic/detail permission-boundary flows beyond the current AIS transaction examples.
- Investigate and implement Model Bank-compatible headless PSU where feasible.
- Validate `private_key_jwt`, `tls_client_auth`/mTLS, and environment capability combinations.
- Design mobile QR code auth as a separate feature track.

## Horizon 3: Participant UX revamp

Outcome: participants can configure and select tests visually without editing raw JSON for common paths.

- Replace flat plan selection with a tree view of standard/version/API/resource/endpoint/content/auth/step.
- Add structured suite, API family, auth-method, and model-bank/environment presets.
- Reduce free-text fields while keeping custom environments possible.
- Add compatibility validation for environment and auth choices.
- Surface mandatory, optional, conditional, and certification-impacting decisions clearly.

## Horizon 4: Custom test values

Outcome: exploratory custom testing is useful without weakening certification integrity.

- Define certification and exploratory profiles.
- Add a test-data/default-profile contract.
- Record custom-value deviations in preview, result JSON, execution logs, and downloads.
- Mask sensitive custom values consistently.
- Ensure unapproved custom values block certification eligibility or follow the final approved override policy.

## Horizon 5: Release readiness

Outcome: Phase 1 can be released with a defensible operational and security posture.

- Close Docker runtime hardening decisions: read-only filesystem, dropped capabilities, no-new-privileges, tmpfs scratch, and localhost binding guidance/defaults.
- Define image signing and approved-release policy operations.
- Align CI/CD status checks, Snyk expectations, E2E gates, and release branch rules.
- Complete participant setup/config docs, suite reference, OBL operator/certification guide, and troubleshooting docs.
- Confirm accessibility target and tooling for browser UI.

## Horizon 6: Wider standards coverage

Outcome: repeat the v4.0.1 AIS method across other APIs.

- Extend parity-ledger approach to PIS, CBPII, VRP, cVRP, and DCR.
- Add generic engine primitives only when needed by multiple standards or when clearly reusable.
- Keep starter suites partial until each family has coverage, validator, docs, and Standards sign-off.

