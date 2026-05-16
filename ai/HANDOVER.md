# Handover

Last updated: 2026-05-16

## Current State

The repository is on `develop` with PR #1 and PR #2 merged. The project has a working Python/Django scaffold, CI/E2E setup, Dockerfile, and an initial Ozone model-bank hello-world path in the `conformance` package.

The project is still before the main M2/M3 architecture work. The next substantial work should focus on config schema, manifest schema, manifest parsing, variable substitution, assertion evaluation, and how test execution state flows into results.

## What Was Just Added

- `ai/README.md`: source-of-truth order and working rules.
- `ai/AGENT_TOOLING_GUIDE.md`: developer guide for how AI instructions, prompts, logs, and handovers fit together.
- `ai/PROJECT_CONTEXT.md`: compact product context and current progress.
- `ai/DECISION_LOG.md`: initial durable decisions and open decisions.
- `ai/DEVELOPMENT_LOG.md`: chronological progress log.
- `.github/prompts/`: reusable Copilot prompt workflows for implementation, handover, and decisions.
- `.github/prompts/create-agent-development-prompt.prompt.md`: reusable prompt for converting next-feature recommendations into implementation-ready agent prompts.
- `.github/copilot-instructions.md`: updated to point agents at the `ai/` workspace and deprioritise generated docs as a source of decisions.

## Next Recommended Work

1. Review the initial decision log and confirm or adjust the proposed entries.
2. Add any useful development choices from previous chat files into `DEVELOPMENT_LOG.md` first, then promote durable choices into `DECISION_LOG.md`.
3. Start M2 with a focused plan for config schema, manifest schema, TestPlan schema, loader, and parser.
4. Keep generated `/docs` useful for onboarding and CI references, but avoid treating it as binding architecture.

## Files To Read First In A New Session

1. `.github/copilot-instructions.md`
2. `ai/README.md`
3. `ai/PROJECT_CONTEXT.md`
4. `ai/DECISION_LOG.md`
5. This file

## Open Questions

- Should the M2 schema work produce JSON schema files before engine code, or evolve schema and parser together?
- Which JWT/JWS library should be adopted for the FAPI flow?
- What should the report JSON schema look like at the top level?
- What exact secure Docker base image should be used for the final community image?
