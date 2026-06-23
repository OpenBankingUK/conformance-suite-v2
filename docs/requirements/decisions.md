# Decisions and open register

This file records product and architecture decisions that AI agents and humans must respect. Use `Status: Proposed` for working assumptions, `Accepted` for decisions, and `Open` for unresolved items.

## Accepted decisions

| ID | Decision | Rationale |
| --- | --- | --- |
| DEC-001 | `docs/requirements/` is the repo-native requirements home. | The SWE is managing AI agents, so requirements must live close to code and be directly inspectable by agents. |
| DEC-002 | The next certifiable target is Open Banking Read/Write v4.0.1 AIS. | The codebase already has v4.0.1 AIS rails and the planning direction needs one narrow certification target. |
| DEC-003 | Prior-FCS parity is the minimum baseline before new certification claims. | Participants and OBL need confidence that v2 covers at least what the previous FCS covered unless old behaviour is explicitly waived or superseded. |
| DEC-004 | All current bundled suites remain `certificationCoverage: partial`. | Standards sign-off, full parity/coverage mapping, and validator coverage are not complete yet. |
| DEC-005 | Machine-readable requirement and coverage artefacts are required. | Agent execution needs stable IDs, statuses, affected surfaces, acceptance criteria, and coverage ledgers. |
| DEC-006 | Custom test values default to exploratory and block certification eligibility when baseline deltas are present. | The shipped Rank 0 policy keeps certification conservative while enabling participant experimentation. |

## Proposed decisions

| ID | Proposal | Notes |
| --- | --- | --- |
| DEC-007 | Environment capability metadata should drive guided auth-method selection. | Prevents launching incompatible suite/auth combinations for custom environments. |
| DEC-008 | Mobile QR code auth should be its own feature track. | It has distinct external dependency, participant UX, and Phase 2 migration considerations. |

## Open decisions

| ID | Decision needed | Impact | Suggested owner |
| --- | --- | --- | --- |
| OPEN-001 | Exact Standards-owned mandatory matrix for v4.0.1 AIS. | Blocks promotion to `certificationCoverage: complete`. | Standards |
| OPEN-002 | How to represent Standards sign-off and waivers in repo artefacts. | Blocks reliable agent automation of coverage status. | Standards + Engineering |
| OPEN-003 | Whether custom-value overrides can ever remain certifiable. | Still open; Rank 0 ships conservatively without certifiable overrides. | Standards + Certification |
| OPEN-004 | Environment capability metadata shape for Model Bank and custom ASPSPs. | Affects guided config builder, auth selection, and validation. | Engineering + Standards |
| OPEN-005 | Headless PSU feasibility for Ozone/model-bank environments. | Affects CI/headless certification workflows and UX presets. | Engineering |
| OPEN-006 | Docker runtime hardening defaults for local Phase 1 distribution. | Affects participant run commands, Docker docs, and release posture. | Engineering + Security |
| OPEN-007 | Image signing process and approved-release policy ownership. | Affects release, certification validation, and participant trust. | Engineering + Certification |
| OPEN-008 | Accessibility target and tooling for browser UI. | Affects UI acceptance criteria and CI/tooling. | Product + Engineering |
| OPEN-009 | DCR timing relative to v4.0.1 AIS certification readiness. | Affects roadmap sequencing. | Product + Standards |
| OPEN-010 | Mobile QR auth Phase 1 support and Phase 2 migration approach. | Affects auth roadmap and external dependency handling. | Product + Engineering |
