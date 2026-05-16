# Agent Skills

Use this folder to describe project-specific skills that can be implemented in Copilot, Claude, or another agent runtime.

The canonical skill design should live here even if the executable skill is installed in a user-level folder such as `~/.copilot/skills` or `~/.claude/skills`.

## Candidate Skills

- `fcs-schema-plan`: turn a schema requirement into a plan covering JSON Schema, parser changes, validation tests, and migration impact.
- `fcs-conformance-test`: scaffold a conformance test from a manifest/test-plan requirement and identify required assertions, fixtures, and Ozone coverage.
- `fcs-security-review`: review a change against the project security checklist, including secrets, logging, masking, Docker, CSRF, path traversal, and dependency risk.
- `fcs-handover`: summarise a completed session into `ai/HANDOVER.md`, `ai/DEVELOPMENT_LOG.md`, and, where needed, `ai/DECISION_LOG.md`.

## Skill Design Rules

- Skills should read `../PROJECT_CONTEXT.md`, `../DECISION_LOG.md`, and `../HANDOVER.md` before acting.
- Skills should not rewrite source-of-truth files unless their task explicitly requires it.
- Skills should record uncertainty and open questions instead of inventing requirements.
- Skills should keep regulated-domain decisions grounded in source documents, tests, and decision logs.
