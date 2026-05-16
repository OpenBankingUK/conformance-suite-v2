# Development Log

This log captures dated progress and investigation notes that help the next developer or agent resume work. Promote durable choices to `DECISION_LOG.md`.

## 2026-05-16: AI Agent Workspace Created

- Read the FCS sprint plan and relevant Confluence Q&A export sections.
- Confirmed current repo head on `develop` includes PR #1 and PR #2.
- PR #1 added project setup: Django scaffold, CI, E2E workflow, Dockerfile, uv configuration, tests, Makefile, security baseline files, and generated docs.
- PR #2 added the Ozone model-bank hello-world flow: `conformance` package, CLI, model-bank config parser, Ozone client, result model, runner, example config, and tests.
- Created the `ai/` workspace to make agent handovers, decision logs, and development guidance the adaptive source of truth.
- Added workspace prompt files under `.github/prompts/` for implementation, handover, and decision logging workflows.

## Useful Source Notes Captured

- Phase 1 code complete target: December 2026.
- First ASPSP beta target: January 2027.
- Review gates: July, October, December.
- M2 and M3 are the next major architecture-heavy work: schemas, manifest parser, variable substitution, assertion engine, and context carry-forward.
- Ozone testing is available now and should be used early.
- Generated docs should be deprioritised when they encode choices not explicit in design docs or user direction.

## Known Tooling Notes

- `rg` was not available in the active shell during initial document extraction. Use `grep`, `find`, or installed project tooling when needed.
- The Confluence exports are MHTML and contain large amounts of UI markup. Useful content can be extracted with `textutil -convert txt -stdout <file> | sed -n '<range>p'` or targeted `grep`.
