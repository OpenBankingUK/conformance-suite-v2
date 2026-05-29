---
description: Implement a feature or fix using the AI agent workspace as context.
---

You are working in the FCS rebuild repository. Before making changes, read:

1. `.github/copilot-instructions.md`
2. `ai/README.md` (workspace strategy)
3. `ai/PROJECT_CONTEXT.md`
4. `ai/DECISION_LOG.md`
5. `ai/scratch/HANDOVER.md` if it exists (gitignored, local-only)

Then inspect the relevant code and tests for the requested task.

Workflow:

1. Identify the smallest coherent change that satisfies the request.
2. Prefer existing repo patterns and the design direction recorded in `ai/`.
3. Add or update tests for meaningful business logic.
4. Add or update docstrings on any new or modified public module, class, function, or method.
5. Run the narrowest useful verification command, then broader checks when risk justifies it.
6. Update `ai/scratch/HANDOVER.md` if the work leaves important context for the next session.
7. Add an `ai/DECISION_LOG.md` entry only if the task creates or changes a durable architectural, security, public-interface, schema, or certification decision.

Do not treat generated docs in `docs/` as binding if they conflict with source design docs, user direction, or the `ai/` workspace.
