---
description: Refine the ai/ workspace strategy — decide what is tracked, what is private, and tighten token usage.
---

Help me decide and execute a clean strategy for the `ai/` agent workspace and related context-loading rules. Goals: keep what genuinely helps collaborators and agents, move ephemeral or single-machine notes out of git, and reduce per-turn context bloat.

## Context to read first

- `.gitignore` (current state of `ai/` rule)
- `.github/copilot-instructions.md` (the "AI Agent Operating Context" section — drives auto-loaded context)
- `ai/README.md`, `ai/AGENT_TOOLING_GUIDE.md`
- `ai/PROJECT_CONTEXT.md`, `ai/DECISION_LOG.md` (durable shared context)
- `ai/HANDOVER.md`, `ai/DEVELOPMENT_LOG.md` (volatile, candidate for private)
- `ai/plans/` and `ai/skills/` (durable; referenced from code/docs)
- `.github/prompts/*.prompt.md` (consumers of the above paths)
- `/memories/repo/` via the memory tool (agent-only, this-machine-only)

## Decisions to make (ask me one at a time)

1. **Tier 3 — volatile scratch.** Should `ai/HANDOVER.md` and `ai/DEVELOPMENT_LOG.md` stay tracked, move to an ignored `ai/scratch/` subfolder, or move to `/memories/repo/`? Trade-off: tracked = useful for cross-machine / contributor handover, churns PRs; ignored = quiet PRs, lost when reclone; `/memories/repo/` = silent and agent-only, no cross-machine sync.
2. **Token budget on `copilot-instructions.md`.** The "Before making non-trivial changes, read the AI agent workspace" line currently encourages eager loading. Should it become "Consult `ai/PROJECT_CONTEXT.md` and `ai/DECISION_LOG.md` **when** a task touches architecture or established decisions"? Confirm or counter-propose.
3. **`/memories/repo/` boundary.** What kinds of facts should live there vs. in `ai/`? Propose a 1–2 line rule (e.g. "agent-discovered repo gotchas, single-machine; durable decisions go to `ai/DECISION_LOG.md`").
4. **Prompt file references.** Several `.github/prompts/*.md` mention `ai/HANDOVER.md` etc. If Tier 3 moves, those need updating in lockstep.
5. **Audit of currently-tracked `ai/` content.** Skim `HANDOVER.md`, `DEVELOPMENT_LOG.md`, and the four dated plans for anything unsuitable for the public repo (snark, vendor criticism, half-formed personal notes). Flag, don't remove.

## Output

After interview:

- A short "AI workspace strategy" section to add to `ai/README.md` describing the four tiers (hard rules / durable shared / volatile shared / agent-only) and which files belong in each.
- A diff plan: files to move, `.gitignore` lines to add, `copilot-instructions.md` edits, prompt-file updates.
- A single commit (or two, if the audit surfaces content that needs redaction first) on a new `feature/ai-workspace-strategy` branch.

## Ground rules

- Do not push.
- Do not redact content from already-public history; only move/edit forward.
- Run `make check` before committing.
- Reference this prompt and any related GitHub issue/PR in the commit message.
