# AI Agent Workspace

This folder is the working memory surface for AI-assisted development on the FCS rebuild. It exists so GitHub Copilot, Claude, GPT-based models, and human developers share a consistent view of the project without turning generated documentation into rigid law.

## Source of Truth Order

Use this priority order when sources disagree:

1. Explicit human instruction in the current task or PR.
2. Original FCS design documents and sprint plan supplied by the product owner.
3. `.github/copilot-instructions.md` for repository-wide engineering, security, and compliance rules.
4. This `ai/` workspace, especially `PROJECT_CONTEXT.md`, `DECISION_LOG.md`, and `HANDOVER.md`.
5. Existing code and tests on the active branch.
6. Generated documents in `docs/`, unless their content has been promoted into this workspace.

Repository-wide security and compliance rules in `.github/copilot-instructions.md` always apply and cannot be overridden by notes in this workspace.

The `docs/` folder remains useful for CI, testing, settings, and onboarding references, but it should not freeze decisions that were only inferred by an AI model.

## Files

- `AGENT_TOOLING_GUIDE.md`: how Copilot instructions, prompt files, decision logs, handovers, and external model choices fit together.
- `PROJECT_CONTEXT.md`: compact product and architecture context for agents starting work.
- `DECISION_LOG.md`: living ADR-style log for decisions that should survive chat sessions.
- `DEVELOPMENT_LOG.md`: dated progress and investigation notes.
- `HANDOVER.md`: current state, next steps, known risks, and what to read first.
- `plans/`: implementation plans for non-trivial milestones, features, and migrations.
- `skills/`: notes for project-specific agent skills that may be installed in Copilot, Claude, or another agent runtime.

## Working Rules For Agents

- Read `PROJECT_CONTEXT.md`, `DECISION_LOG.md`, and `HANDOVER.md` before changing behaviour.
- Update `HANDOVER.md` whenever work stops with meaningful unfinished context.
- Add a decision log entry when a choice affects architecture, security, data formats, public interfaces, certification behaviour, or future migration cost.
- Keep routine implementation notes in `DEVELOPMENT_LOG.md`; keep durable decisions in `DECISION_LOG.md`.
- Prefer small, dated updates over large retrospective rewrites.
- If a design detail is still uncertain, record the uncertainty rather than inventing permanence.

## Folder Conventions

- Put feature or milestone implementation plans in `ai/plans/` using names like `m2-config-and-manifest-schema.md`.
- Put reusable skill definitions or skill design notes in `ai/skills/`. If a skill must live in a user-level runtime folder, keep the canonical description here and link to the installed location.
