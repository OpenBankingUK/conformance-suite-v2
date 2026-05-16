# Plans

Use this folder for implementation plans that are too large for a single chat reply or PR description.

Plans should be created before substantial work such as:

- Manifest, TestPlan, or participant config schema design.
- Assertion engine design.
- FAPI client and OAuth/OIDC flow implementation.
- Report schema and CertificationValidator work.
- REST API, CLI, and UI milestones.
- Migration of old manifests, assertions, or discovery files.

## Suggested Format

Each plan should include:

- Goal and non-goals.
- Source documents and decision-log entries used.
- Proposed design.
- Security and certification implications.
- Test strategy.
- Rollout or migration notes.
- Open questions.

Use the plan as working context for agents. When the work is complete, summarise the outcome in `../DEVELOPMENT_LOG.md` and promote durable choices to `../DECISION_LOG.md`.
