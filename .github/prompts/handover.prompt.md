---
description: Refresh the AI handover after a development session.
---

Refresh `ai/scratch/HANDOVER.md` for the current repository state. (The `ai/scratch/` directory is gitignored; this is a local-only handover surface.)

Include:

- Current branch and relevant recent commits or PRs.
- What changed in this session.
- Verification that was run and any failures.
- Next recommended work.
- Open questions or blockers.
- Files the next agent or developer should read first.

If the session produced investigation notes, add dated bullets to `ai/scratch/DEVELOPMENT_LOG.md` (also gitignored). If the session made a durable architectural, security, schema, API, certification, or migration decision, add or update `ai/DECISION_LOG.md` (tracked).

Keep the handover concise enough to be read at the start of a new chat session.
