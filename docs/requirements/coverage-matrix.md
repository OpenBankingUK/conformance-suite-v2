# Coverage matrix model

The coverage matrix connects requirements, prior-FCS parity, suite manifests, tests, validator behaviour, and Standards sign-off. It is the control that prevents partial suites from being treated as certifiable by accident.

## Artefact roles

| Artefact | Role |
| --- | --- |
| `requirements.yml` | Stable product and delivery requirement IDs. |
| `suite-coverage/v4-ais-prior-fcs-inventory.json` | Generated 95-row previous-FCS AIS inventory. |
| `suite-coverage/v4.0.1-ais.json` | Target-specific v4.0.1 AIS coverage and promotion ledger. |
| `conformance/suites/*.json` | Executable v2 manifests. |
| `conformance/standards/**` | Bundled standards snapshots used by schema-backed assertions and parity extraction. |
| `tests/**` | Behavioural proof for parser, executor, API/UI, result, masking, and validator semantics. |
| `conformance/certification_validator.py` | OBL-side enforcement that must reject partial or incomplete submissions. |

## Promotion gate

A suite may move from `certificationCoverage: partial` to `complete` only when all of the following are true:

1. The target coverage ledger names the suite and target version/API/profile.
2. Every prior-FCS mandatory row relevant to the target is implemented, waived, marked as a legacy issue, or superseded with Standards sign-off.
3. Every Standards-owned mandatory requirement is mapped to one or more v2 manifest steps.
4. Every mapped step has assertion coverage sufficient for the requirement.
5. Required auth/content variants are represented as distinct plan/auth-bundle paths.
6. Optional and conditional coverage is available for participant selection where applicable.
7. Result JSON surfaces plan, suite, coverage, approved-release, mandatory, and custom-value eligibility evidence.
8. OBL-side certification validation recomputes mandatory coverage and rejects partial/incomplete reports.
9. Participant and OBL operator docs match the released behaviour.
10. Standards sign-off is recorded in the repo-visible ledger.

## Status interpretation

| Status | Meaning | Blocks `complete`? |
| --- | --- | --- |
| `implemented` | Covered by v2 manifest/assertions/tests for the target. | No, if Standards accepts it. |
| `partially_implemented` | Some behaviour exists but coverage is incomplete. | Yes for mandatory rows. |
| `requires_new_primitive` | Needs a generic engine/assertion/auth primitive or a waiver. | Yes for mandatory rows. |
| `blocked` | External or unresolved dependency blocks implementation. | Yes for mandatory rows. |
| `waived` | Standards explicitly says this row need not be implemented. | No, if sign-off is recorded. |
| `legacy_issue` | Prior-FCS behaviour is wrong or unsuitable to preserve. | No, if sign-off is recorded. |
| `superseded` | A new v2 requirement intentionally replaces old behaviour. | No, if mapped and signed off. |
| `not_started` | No v2 implementation or decision exists. | Yes for mandatory rows. |
| `requires_v401_confirmation` | Seeded from v4 evidence but not confirmed for v4.0.1. | Yes for mandatory rows. |

## Current generated inventory summary

From `suite-coverage/v4-ais-prior-fcs-inventory.json`:

| Metric | Count |
| --- | ---: |
| Total prior-FCS AIS scripts | 95 |
| Mandatory | 24 |
| Optional | 34 |
| Conditional | 37 |
| Implemented in current v4 benchmark | 19 |
| Not implemented in current v4 benchmark | 76 |
| Mandatory rows requiring v4.0.1 confirmation | 7 |
| Mandatory rows requiring new primitives | 8 |
| Mandatory rows not started | 9 |

These counts are planning inputs, not certification evidence. Every row still needs v4.0.1 applicability review.

## Machine-readable row lifecycle

1. Generated from bundled prior-FCS source.
2. Reviewed for v4.0.1 applicability.
3. Mapped to v2 requirement ID and manifest step IDs.
4. Classified as implemented, waived, legacy issue, superseded, blocked, or requiring a primitive.
5. Linked to tests and validator expectations.
6. Signed off by Standards for certification coverage.
7. Used to justify a manifest `certificationCoverage: complete` change.

## Agent slice extraction

A row or cluster of rows becomes agent-ready when:

- it has a stable requirement or coverage ID
- the missing behaviour is specific and generic enough to implement safely
- affected manifests/modules/tests are known
- masking and certification impact are explicit
- acceptance criteria can be validated without live-network access unless the slice is explicitly an Ozone/live integration task

