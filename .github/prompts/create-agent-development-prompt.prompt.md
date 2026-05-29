---
description: Turn a next-feature recommendation into a repeatable agent development prompt.
---

You are preparing a high-quality development prompt for an AI coding agent.

The user will paste a feature recommendation, roadmap note, triage summary, planning output, or "what should we build next?" response. Transform that input into a precise, implementation-ready prompt that another agent can execute in this repository.

Do not implement the feature. Produce the prompt only.

Before writing the development prompt, infer:

- The intended feature or fix.
- Why it matters now.
- The smallest useful vertical slice.
- The relevant project context files and likely code/test files to inspect.
- What should be explicitly out of scope.
- The acceptance criteria and verification commands.
- Whether the work may require updates to `CHANGELOG.md`, `ai/scratch/HANDOVER.md`, `ai/scratch/DEVELOPMENT_LOG.md`, or `ai/DECISION_LOG.md`.

If the pasted recommendation is too vague to produce an actionable prompt, ask up to three concise clarification questions. Otherwise, produce a complete prompt using this structure:

```markdown
# Task

Implement: <one sentence feature or fix name>

# Goal

<2-4 sentences explaining the desired outcome, why it matters now, and how it fits the current project direction.>

# Input Context

This prompt was generated from the following planning/recommendation input:

> <brief quoted or summarized source input>

# Context To Read First

Read these before changing code:

- `.github/copilot-instructions.md`
- `ai/README.md`
- `ai/PROJECT_CONTEXT.md`
- `ai/DECISION_LOG.md`
- `ai/scratch/HANDOVER.md` if it exists (local-only, gitignored)
- <specific files likely relevant to the task>

# Scope

Do:

- <specific change>
- <specific change>
- <specific tests>
- <specific docs/log updates, if needed>

Do not:

- <explicitly out-of-scope adjacent feature>
- <tempting but premature implementation detail>
- <large architectural expansion not needed for this slice>

# Design Constraints

- Follow the repository instructions and existing project style.
- Keep changes small, focused, and consistent with the `ai/` workspace direction.
- Prefer typed, structured data models over untyped dictionaries for domain data.
- Validate external input with clear errors.
- Do not commit secrets, credentials, tokens, certificates, or participant-sensitive data.
- Do not hardcode certification criteria or masking rules that should be configuration-driven.
- Add or update docstrings on any new or modified public module, class, function, or method.
- Add tests for meaningful business logic using the correct pytest markers.
- Update `ai/DECISION_LOG.md` if the work makes a durable architecture, schema, API, security, certification, or migration decision.
- Update `ai/scratch/HANDOVER.md` if useful context remains for the next session.

# Acceptance Criteria

The task is done when:

- <observable behaviour or artifact exists>
- <important invalid/error case is covered>
- <tests cover the new behaviour>
- <docs/logs are updated where appropriate>
- `make check` passes, or any inability to run it is clearly explained.

# Suggested Implementation Path

1. Read the context files and inspect the relevant code/tests.
2. Identify the smallest coherent vertical slice.
3. Add or update tests first where practical.
4. Implement the feature with minimal, focused changes.
5. Update documentation, changelog, handover, or decision log as needed.
6. Run the narrowest useful checks, then `make check` before finishing.
7. Summarise changed files, verification results, decisions made, and follow-up risks.
```

Quality bar:

- The generated development prompt must be specific enough that a fresh agent can start work without rereading the whole conversation.
- Keep the implementation slice small enough to be completed in one focused session.
- Prefer concrete file paths and behaviours over generic guidance.
- Preserve uncertainty by naming open questions rather than inventing requirements.
- Make the final prompt copy-paste ready.