# Requirements source of truth

This directory is the repo-native planning home for the refreshed FCS product requirements, roadmap, and AI-agent-compatible delivery model.

The older PRD remains in `docs/FCS Rebuild - PRD v3 [DRAFT].md` for history. New planning should use this directory first.

## Current baseline

The codebase already has a working Phase 1 local platform: Python/Django, CLI/API/browser launch surfaces, config-selected bundled suites, v1 manifests, first-class `TestPlan`, group-aware execution, PSU authorisation plumbing, FAPI signing, structured result JSON, masked NDJSON execution logs, certification eligibility metadata, and an OBL-side certification validator.

All bundled suites remain `certificationCoverage: partial`. The first planned certifiable target is **Open Banking Read/Write v4.0.1 AIS**.

## Artefacts

| File | Purpose |
| --- | --- |
| `PRD-v4-draft.md` | Refreshed product requirements draft aligned to the current codebase and development model. |
| `roadmap.md` | High-level delivery roadmap/backlog for Phase 1 certification readiness and Phase 2 constraints. |
| `next-priorities.md` | Ordered next implementation priorities for agent-managed delivery. |
| `requirements.yml` | Machine-readable requirement index for agents, humans, and future automation. |
| `suite-coverage/v4.0.1-ais.json` | Seed coverage ledger for the first certifiable target. |
| `suite-coverage/v4-ais-prior-fcs-inventory.json` | Generated 95-row previous-FCS AIS inventory used as the parity baseline seed. |
| `coverage-matrix.md` | How requirements, parity rows, manifests, tests, validator behaviour, and sign-off combine into certification coverage. |
| `v4.0.1-ais-certification-scope.md` | Target scope and promotion checklist for the first certifiable suite. |
| `agent-slice-template.md` | Standard shape for turning a requirement into an AI-agent-ready implementation task. |
| `decisions.md` | Decision log and open decision register. |
| `prior-fcs-parity.md` | Method for turning previous FCS scripts into a v2 parity ledger. |
| `visual-plan-builder.md` | Product/design notes for tree-style test selection and guided config UX. |
| `guided-config-builder.md` | Product/design notes for reducing raw JSON through presets, capability metadata, and structured auth choices. |
| `custom-test-values.md` | Product/design notes for certification vs exploratory test values. |
| `multi-auth-model.md` | Product/design notes for multiple consent/token/auth bundles and auth method selection. |
| `release-readiness.md` | Phase 1 release-readiness checklist and open hardening decisions. |

## Working rules

- Treat prior-FCS parity as the minimum baseline unless Standards explicitly marks old behaviour as a legacy issue, waived, or superseded.
- Do not promote a suite to `certificationCoverage: complete` until the coverage ledger, manifest, validator, docs, and Standards sign-off all agree.
- Keep Open Banking-specific semantics in manifests and coverage artefacts where possible; add Python primitives only when they are generic and reusable.
- Every agent-owned slice must include observable acceptance criteria, affected files, validation commands, security/masking notes, and documentation impact.
- Custom test values are exploratory by default until a policy explicitly says which override classes can remain certifiable.
