# Development Log

This log captures dated progress and investigation notes that help the next developer or agent resume work. Promote durable choices to `DECISION_LOG.md`.

## 2026-05-16: AI Agent Workspace Created

- Read the FCS sprint plan and relevant Confluence Q&A export sections.
- Confirmed current repo head on `develop` includes PR #1 and PR #2.
- PR #1 added project setup: Django scaffold, CI, E2E workflow, Dockerfile, uv configuration, tests, Makefile, security baseline files, and generated docs.
- PR #2 added the Ozone model-bank hello-world flow: `conformance` package, CLI, model-bank config parser, Ozone client, result model, runner, example config, and tests.
- Created the `ai/` workspace to make agent handovers, decision logs, and development guidance the adaptive source of truth.
- Added workspace prompt files under `.github/prompts/` for implementation, handover, and decision logging workflows.
- Added `.github/prompts/create-agent-development-prompt.prompt.md` to convert feature recommendations or planning notes into copy-paste-ready agent development prompts.

## Useful Source Notes Captured

- Phase 1 code complete target: December 2026.
- First ASPSP beta target: January 2027.
- Review gates: July, October, December.
- M2 and M3 are the next major architecture-heavy work: schemas, manifest parser, variable substitution, assertion engine, and context carry-forward.
- Ozone testing is available now and should be used early.
- Generated docs should be deprioritised when they encode choices not explicit in design docs or user direction.

## Known Tooling Notes

- `rg` was not available in the active shell during initial document extraction. Use `grep`, `find`, or installed project tooling when needed.
- The Confluence exports are MHTML and contain large amounts of UI markup. Prefer cross-platform targeted `grep` or Python standard-library parsing for extraction; on macOS, `textutil -convert txt -stdout <file> | sed -n '<range>p'` is also a useful local shortcut.

## 2026-05-26: M3 — Manifest v1 Sequential Steps + Context Carry-Forward

Implemented the v1 manifest schema with sequential steps and execution context:

- **Phase 1 (parser):** Extended `conformance/manifest.py` to accept `schemaVersion: "v1"` with a `steps` array. Added `ManifestStep` dataclass, placeholder syntax validation via regex, duplicate-id rejection, and forward-reference detection. Static HTTPS validation is deferred for placeholder-containing URLs.
- **Phase 2 (context):** Created `conformance/context.py` with `ExecutionContext` (frozen, accumulates `StepRecord`s), `record_step` (returns new context), and `resolve_placeholders` (dot-path resolution with clear error messages). Supported grammar: `request.method`, `request.url`; `response.status_code`, `response.body.<path>`. Header templating is not implemented in this milestone.
- **Phase 3 (executor):** Refactored `conformance/executor.py`. New `_run_manifest_v1` iterates steps, resolves placeholders, validates URLs, fetches endpoints, evaluates assertions, and records context. v0 now desugars `followUp` to v1 steps inside `_run_manifest_v0` while preserving the skip-on-fail gate.
- **Phase 4 (docs):** Added `config/manifest-v1-openid-jwks-example.json`, CHANGELOG entries, DL-0012, and updated handover/dev-log.

Key design choices (captured in DL-0012):
- Placeholder grammar is regex-based (`${steps.<id>.(request|response).<field>.<path>}`). Intentionally not jinja/jsonpath.
- Substitution applies only to `url` field in this milestone.
- Steps that fail assertions still record their response into context (carry-forward is unconditional).
- v0 is retained as supported sugar over v1 for at least one more milestone.

## 2026-05-27: M4 Prerequisite — Non-GET Methods + Header/Body Templating

Implemented manifest v1 support for POST/PUT/PATCH/DELETE with header and body templating:

- **Parser (`conformance/manifest.py`):** Widened `RequestMethod` to `GET|POST|PUT|PATCH|DELETE`. Added optional `headers` (dict[str, str], RFC 7230 token-validated names, non-empty string values) and `body` (JsonValue) fields to `ManifestRequest`. Body rejected on GET. Placeholder validation recurses into header values and body string leaves via `_validate_placeholders_in_structure`.
- **Context (`conformance/context.py`):** Added `resolve_in_structure` — walks dicts and lists, resolves placeholders in string leaves, preserves non-string scalars.
- **HTTP (`conformance/http.py`):** Added `send_json` — dispatches arbitrary method with optional headers and JSON body via `httpx.Client.request()`. `get_json` now delegates to `send_json` preserving its public signature.
- **Executor (`conformance/executor.py`):** `_execute_v1_step` now resolves placeholders in URL, headers, and body sequentially. Dispatches all methods via `send_json`. Defence-in-depth guard rejects unsupported methods.
- **Example:** `config/manifest-v1-token-exchange-example.json` — two-step flow (GET discovery, POST token exchange with placeholders in headers and body).
- **Tests:** 20+ new test cases covering parser acceptance/rejection, `resolve_in_structure`, and executor POST dispatch with placeholder resolution.
- **Docs:** CHANGELOG entry, DL-0013, updated HANDOVER and this log.

Key design choices (captured in DL-0013):
- Body templating substitutes string leaves only. Non-string scalars pass through unchanged.
- `application/json` is the only natively-formatted body type; form-urlencoded deferred to next slice.
- Masking is not yet applied to substituted secrets in step records.
- The engine remains domain-agnostic: no OAuth2/FAPI terminology in engine modules.

Follow-ups identified:
- Form-urlencoded body encoding for real OAuth2 token exchange.
- mTLS transport wiring.
- JWS request-object signing.
- Callback/redirect handling.
