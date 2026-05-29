# AI Agent Workspace

Working memory for AI-assisted development on the FCS rebuild. Loaded on-demand, not eagerly.

## Tiers

| Tier | Storage | Purpose | Loaded |
| --- | --- | --- | --- |
| 1. Hard rules | `.github/copilot-instructions.md` | Repo-wide engineering, security, compliance rules. | Always |
| 2. Durable shared | `ai/` (tracked) | Project context, decisions, active plans. | On-demand when task touches architecture / security / public interfaces / prior decisions |
| 3. Volatile shared | `ai/scratch/` (gitignored) | Handover notes, dated development log. | On-demand at session boundaries |
| 4. Agent-only | `/memories/repo/` (memory tool) | Single-machine, agent-discovered gotchas. | Auto-listed in context |

## Source of Truth Order

When sources disagree:

1. Explicit human instruction in the current task or PR.
2. Original FCS design documents and sprint plan supplied by the product owner.
3. `.github/copilot-instructions.md` for repo-wide engineering, security, compliance rules.
4. `ai/PROJECT_CONTEXT.md` and `ai/DECISION_LOG.md`.
5. Active plans in `ai/plans/`.
6. Existing code and tests on the active branch.
7. Generated documents in `docs/`, unless their content has been promoted into this workspace.

Repository-wide security and compliance rules in `.github/copilot-instructions.md` always apply.

## Tracked Files

- `PROJECT_CONTEXT.md` — compact product and architecture context.
- `DECISION_LOG.md` — ADR-style log; entries survive sessions.
- `plans/` — implementation plans for active, non-trivial milestones. Delete plans once shipped and decision-logged.

## Scratch (gitignored)

- `ai/scratch/HANDOVER.md` — current state, next steps, blockers. Local to your machine.
- `ai/scratch/DEVELOPMENT_LOG.md` — dated investigation notes. Local to your machine.

Promote durable findings from scratch into `DECISION_LOG.md` or `PROJECT_CONTEXT.md` before they age out.

## `/memories/repo/` Boundary

`/memories/repo/` holds agent-discovered, single-machine gotchas (build commands, lint behaviours, things you only learn by running them). Durable cross-machine decisions go in `ai/DECISION_LOG.md`; cross-machine context goes in `ai/PROJECT_CONTEXT.md`.

## Working Rules For Agents

- Consult tier-2 files when a task touches architecture, security, public interfaces, or a previously-recorded decision.
- Add a `DECISION_LOG.md` entry when a choice affects architecture, security, data formats, public interfaces, certification behaviour, or future migration cost.
- Update `ai/scratch/HANDOVER.md` if useful context remains for the next session.
- If a design detail is uncertain, record the uncertainty rather than inventing permanence.
