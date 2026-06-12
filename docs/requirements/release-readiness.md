# Phase 1 release-readiness review

This review captures release requirements that are separate from conformance-suite coverage. It should be kept aligned with `docs/CICD_STRATEGY.md`, `docs/DEVELOPER_GUIDE.md`, `README.md`, the Dockerfile, and release workflows.

## Current baseline

- Dockerfile uses pinned `python:3.14.5-alpine3.22` in builder and runtime stages.
- Runtime uses a non-root `appuser`.
- Docker build copies dependency files before source and includes a healthcheck.
- `CONFORMANCE_TOOL_VERSION` can stamp release builds.
- `make check` runs secret scanning, lint/type/docstring checks, and offline tests.
- CI workflows exist for core CI, E2E, and Ozone integration.
- Certification validator can check submitted reports against approved-release policy.

## Release blockers and decisions

| Area | Current status | Needed |
| --- | --- | --- |
| Docker runtime hardening | Partial | Decide and document read-only filesystem, tmpfs scratch, dropped capabilities, no-new-privileges, and localhost-only publish defaults/guidance. |
| Image signing | Open | Define signing tool/process, release ownership, participant verification instructions, and CI/release integration. |
| Approved-release policy | Partial | Define who owns authoritative approved version lists and how validator operators obtain them. |
| Snyk/security gates | Partial | Confirm actual required check names and severity policy match branch protection docs. |
| Participant docs | Partial | Produce setup/config guide, guided UI guide, auth-method guide, and troubleshooting guide. |
| OBL operator docs | Partial | Produce certification validator runbook, approved-release handling, waiver/sign-off handling, and Confluence/Salesforce workflow notes. |
| Accessibility | Open | Confirm WCAG target and whether tooling belongs in CI. |
| E2E release evidence | Partial | Confirm required model-bank/Ozone evidence for merges to release/main. |

## Docker hardening target

The PRD target includes:

- non-root runtime user
- read-only root filesystem where feasible
- writable tmpfs/scratch area only where needed
- dropped Linux capabilities
- no privilege escalation
- participant config/certs mounted read-only
- API bound/published locally by default
- no secrets baked into image layers or environment defaults

Some of these are runtime flags rather than Dockerfile-only settings, so docs and release scripts may be required in addition to Dockerfile changes.

## Release evidence checklist

Before a Phase 1 release candidate:

1. `make check` passes.
2. Docker build and healthcheck pass.
3. E2E/model-bank evidence is attached or linked.
4. Snyk dependency/code/container checks are clear for high/critical findings or have approved exceptions.
5. Image version is stamped.
6. Image signing artefact is produced once process is defined.
7. Approved-release policy is updated by the owner.
8. Participant docs and OBL operator docs match the released config/report/schema behaviour.
9. Changelog has a release entry.
10. Certification validator accepts only complete, approved, mandatory-passing reports.

