# AI Agent Tooling Guide

This guide explains how the AI-assistance pieces fit together for developers working on the FCS rebuild.

## Mental Model

Think of the tooling as four layers:

1. Instructions: stable rules the agent should always obey.
2. Context: compact project knowledge the agent should read before acting.
3. Prompts: repeatable workflows for common tasks.
4. Logs: durable memory for decisions, handovers, and progress.

The aim is not to make the agent blindly follow old notes. The aim is to make useful context cheap to recover while preserving the ability to adapt when new information arrives.

## Instructions

Repository instructions live in `.github/copilot-instructions.md`. They define security, testing, Django, Docker, typing, and review expectations for this regulated financial-services project.

Use instructions for rules that should rarely change, such as:

- Security and privacy requirements.
- Test and type-checking standards.
- Code documentation standards for human-readable modules and public APIs.
- Dependency and Docker expectations.
- The source-of-truth order for AI-assisted development.

Do not use instructions for evolving design choices that may need discussion. Put those in `DECISION_LOG.md` instead.

## Context Files

Agents should start with these files:

- `ai/PROJECT_CONTEXT.md` for product scope, architecture direction, and current PR progress.
- `ai/DECISION_LOG.md` for durable choices and open decision records.
- `ai/HANDOVER.md` for the latest working state.

Keep these files concise. They should orient a new session in minutes, not recreate the whole Confluence export.

## Prompt Files

Workspace prompt files live in `.github/prompts/`. They are reusable workflows for Copilot Chat and compatible tools.

Current prompts:

- `implement-from-agent-context.prompt.md`: implement a feature or fix using the AI workspace as context.
- `handover.prompt.md`: produce or refresh a handover after a working session.
- `log-decision.prompt.md`: turn a design choice into a durable decision-log entry.

Use prompt files when the task has a repeatable shape. For one-off discussions, normal chat is fine.

## Decision Logs

`ai/DECISION_LOG.md` is the lightweight ADR register. Add entries for choices that would be expensive or confusing to rediscover later.

Good decision-log topics include:

- Manifest and TestPlan schema shape.
- Assertion engine semantics.
- HTTP client, async model, and FAPI flow decisions.
- Security controls, logging, masking, and result integrity.
- Docker hardening and runtime assumptions.
- Public API, CLI, report schema, and certification eligibility behaviour.

Do not add entries for trivial local choices, formatting, or a helper function name unless they reflect a larger convention.

## Development Log

`ai/DEVELOPMENT_LOG.md` is a chronological working notebook. It captures what changed, what was learned, and what remains uncertain.

Use it for:

- PR summaries.
- Investigation outcomes.
- Test runs and environment findings.
- Links to chat-derived choices that should be reviewed later.

When a note becomes a durable decision, promote it to `DECISION_LOG.md`.

## Handover

`ai/HANDOVER.md` is the fast restart file. Keep it current at the end of meaningful sessions.

A good handover states:

- Current branch and recent PR progress.
- What was just done.
- What should happen next.
- Known blockers or open questions.
- Which files to read first.

## Model Selection Guidance

Use model choice intentionally:

- Use stronger long-context models for architecture, design review, PRD interpretation, and refactors.
- Use fast coding models for scaffolded implementation once requirements are clear.
- Use review-oriented models for security, test coverage, and regression risk checks.
- Keep regulated-domain decisions grounded in source documents and decision logs regardless of model.

The model is not the source of truth. The source of truth is the ordered context in `ai/README.md`.

## Updating This System

When adding a new agent workflow:

1. Add or update a prompt in `.github/prompts/` if the workflow is repeatable.
2. Update this guide if the workflow changes how developers should use the tooling.
3. Update `HANDOVER.md` if the change affects the next session.
4. Add a decision-log entry only if the workflow changes project governance or architecture.
