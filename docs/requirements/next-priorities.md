# Next development priorities

This sequence turns the planning artefacts into agent-manageable implementation work. It assumes v4.0.1 AIS remains the first certifiable target.

## Priority 1: Standards review of v4.0.1 AIS coverage

Why first: implementation cannot safely promote coverage without Standards decisions.

Inputs:

- `suite-coverage/v4-ais-prior-fcs-inventory.json`
- `suite-coverage/v4.0.1-ais.json`
- `v4.0.1-ais-certification-scope.md`
- `coverage-matrix.md`

Outputs:

- v4.0.1 applicability decisions for the 95 generated prior-FCS rows
- mandatory/optional/conditional decisions
- waiver, legacy issue, and superseded decisions
- updated blockers for missing primitives

## Priority 2: Mandatory parity blockers

Why second: mandatory prior-FCS rows currently include unresolved statuses.

Initial blocker groups:

- assertion groups / `asserts_one_of`
- schema-check completion or waivers
- permission-negative flows
- no-token and client-credentials protected-resource variants
- FAPI interaction ID playback
- reusable legacy assertion mapping

Each blocker should become one or more agent slices only after the affected rows and acceptance criteria are explicit.

## Priority 3: Auth bundle and environment capability metadata

Why third: negative flows, basic/detail variants, headless PSU, mTLS, and guided UI all depend on explicit auth modelling.

Status: implemented. Manifest `authMetadata`, environment capability presets, and selected-step-to-auth-bundle mappings now flow through plan preview, result evidence, and validator inputs.

Outputs:

- authMetadata manifest contract
- environmentCapabilities evidence and capability presets
- selected-step-to-auth-bundle mapping in UI/report surfaces

## Priority 4: Visual plan builder and guided config revamp

Why fourth: the UI should be driven by stable coverage/auth metadata rather than ad hoc grouping.

Outputs:

- tree metadata in plan preview
- grouped visual selection
- branch selection/deselection
- auth method presets
- capability-based launch blockers
- advanced JSON escape hatch retained

## Priority 5: Custom test values

Why fifth: custom values affect certification eligibility and result schema, so they should be added after coverage and auth metadata are stable.

Status: complete. Custom test values now ship with suite baselines, participant `testData.values`, compiled `RunConfiguration` deltas, custom-value UI, exploratory-run gating, import/export drift checks, and validator enforcement.

Outputs:

- baseline vs participant-data contract [done]
- baseline-delta audit shape [done]
- UI diffing and reset-to-default [done]
- validator rejection of unapproved custom values [done]

## Priority 6: Complete-suite promotion and release readiness

Why last: promotion requires all coverage, validator, docs, and release controls to align.

Outputs:

- v4.0.1 AIS manifest promoted only when ready
- validator coverage for complete suite
- participant and OBL operator docs
- Docker hardening decisions implemented or documented
- image signing and approved-release operations defined

## Work not first

- PIS/CBPII/VRP/cVRP/DCR certifiable coverage should wait until the AIS method is proven, except for reusable primitives that also unblock AIS.
- Phase 2 portal implementation should remain architecture constraints unless business priority changes.
- Mobile QR code auth should remain a separate feature track until manual/headless/mTLS paths are clearer.

Rank 0 custom test values are done; Rank 1 should now focus on multi-auth bundle UI and plan-builder polish.
