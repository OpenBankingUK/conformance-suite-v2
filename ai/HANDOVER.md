# Handover

Last updated: 2026-05-16

## Current State

The repository is on `develop` with PR #1 and PR #2 merged. The project has a working Python/Django scaffold, CI/E2E setup, Dockerfile, and an initial Ozone model-bank hello-world path in the `conformance` package.

M2 has now started with a small manifest v0 parser slice. The project still has not replaced the model-bank runner and does not yet have assertion execution, variable substitution, orchestration, report generation, REST API, or UI work.

## What Was Just Added

- `ai/README.md`: source-of-truth order and working rules.
- `ai/AGENT_TOOLING_GUIDE.md`: developer guide for how AI instructions, prompts, logs, and handovers fit together.
- `ai/PROJECT_CONTEXT.md`: compact product context and current progress.
- `ai/DECISION_LOG.md`: initial durable decisions and open decisions.
- `ai/DEVELOPMENT_LOG.md`: chronological progress log.
- `.github/prompts/`: reusable Copilot prompt workflows for implementation, handover, and decisions.
- `.github/prompts/create-agent-development-prompt.prompt.md`: reusable prompt for converting next-feature recommendations into implementation-ready agent prompts.
- `.github/copilot-instructions.md`: updated to point agents at the `ai/` workspace and deprioritise generated docs as a source of decisions.
- `conformance/manifest.py`: typed, parser-only manifest v0 dataclasses and loader.
- `config/manifest-v0-openid-jwks-example.json`: example manifest for OpenID discovery with optional JWKS follow-up.
- `tests/test_manifest.py`: unit coverage for valid manifests, unsupported schema versions, missing required fields, unknown fields, non-HTTPS request URLs, unsupported assertion types, and unsupported follow-up shapes.
- `ai/DECISION_LOG.md`: DL-0007 records the manifest v0 parser-only contract boundary.

## Next Recommended Work

1. Review the initial decision log and confirm or adjust the proposed entries.
2. Add any useful development choices from previous chat files into `DEVELOPMENT_LOG.md` first, then promote durable choices into `DECISION_LOG.md`.
3. Design how manifest v0 assertions map to an execution context and structured result records without hardcoding certification criteria.
4. Decide whether to add JSON Schema files alongside the Python parser for participant-facing validation.
5. Keep generated `/docs` useful for onboarding and CI references, but avoid treating it as binding architecture.

## Files To Read First In A New Session

1. `.github/copilot-instructions.md`
2. `ai/README.md`
3. `ai/PROJECT_CONTEXT.md`
4. `ai/DECISION_LOG.md`
5. This file

## Open Questions

- Should manifest v0 gain JSON Schema files before engine code, or should schema and parser continue to evolve together?
- Should JWKS follow-up assertions remain generic manifest assertions, or become a specialised reusable step when orchestration starts?
- Which JWT/JWS library should be adopted for the FAPI flow?
- What should the report JSON schema look like at the top level?
- What exact secure Docker base image should be used for the final community image?
