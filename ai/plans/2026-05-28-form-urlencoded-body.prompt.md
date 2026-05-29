# Task

Implement: Form-urlencoded request body support for manifest v1 steps.

# Goal

Add `application/x-www-form-urlencoded` body encoding to the v1 manifest engine so the M4 OAuth2 token-exchange step can be expressed declaratively without further executor changes. The engine must remain domain-agnostic — this is an HTTP content-type capability, not an OAuth2 feature. This is the explicit follow-up identified by DL-0013's "Open consequence" line and item 1 of `Next Recommended Work` in `ai/HANDOVER.md`.

# Input Context

This prompt was generated from a project review that determined:

> The repository just shipped manifest v1 with non-GET methods, header templating, and JSON body templating (DL-0013). The next smallest useful M4 slice — explicitly listed as "Next Recommended Work" item 1 and as the open consequence at the end of DL-0013 — is form-urlencoded body encoding. It unblocks the real OAuth2 token-exchange step without forcing decisions on mTLS, JWS, callbacks, or groups.

A full design plan exists at `ai/plans/2026-05-28-form-urlencoded-body.md`. Read it first — it is the authoritative design for this slice.

# Context To Read First

Read these before changing code:

- `.github/copilot-instructions.md`
- `ai/README.md`
- `ai/PROJECT_CONTEXT.md`
- `ai/DECISION_LOG.md` (especially DL-0012 and DL-0013)
- `ai/HANDOVER.md`
- `ai/plans/2026-05-28-form-urlencoded-body.md` ← authoritative design for this slice
- `conformance/manifest.py`
- `conformance/context.py`
- `conformance/http.py`
- `conformance/executor.py`
- `config/manifest-v1-token-exchange-example.json`
- `tests/test_manifest.py`, `tests/test_http.py`, `tests/test_executor.py`

# Scope

Do:

- Introduce a tagged body shape on v1 step requests: `{"encoding": "form", "fields": {string: string}}`. Keep bare JSON bodies working unchanged (implicit `encoding: "json"`).
- Add a typed body model (frozen dataclass union, e.g. `JsonBody` / `FormBody` → `ManifestBody`) in `conformance/manifest.py` with attribute docstrings per DL-0009 / Google-style docstrings per section 6 of the instructions.
- Update the parser to:
  - Accept the tagged shape on POST/PUT/PATCH/DELETE.
  - Reject form bodies on GET (mirror current JSON-on-GET rejection).
  - Reject empty `fields`, non-string values, unknown `encoding` strings, and malformed placeholders inside `fields` values (reuse `_validate_placeholders_in_structure`).
- Update `conformance/http.py` so the dispatcher can emit form-encoded bodies via httpx's native `data=` parameter (never hand-rolled `urllib.parse.urlencode`). Use `httpx.Headers` to preserve the existing case-insensitive merge. Default `Content-Type: application/x-www-form-urlencoded` only when the manifest has not supplied a `Content-Type` (case-insensitive).
- Update `conformance/executor.py` `_execute_v1_step` to dispatch on body type, resolve placeholders in `fields` via `resolve_in_structure` (or an equivalent string-by-string call), and continue recording only the method + resolved URL in the step record. Add an inline comment naming the masking deferral (DL-0013).
- Update `config/manifest-v1-token-exchange-example.json` to use the tagged form shape for the token exchange step (canonical motivating example).
- Add tests per the plan's Tests section (parser acceptance and every named rejection, HTTP wire encoding via `httpx.MockTransport`, executor dispatch with placeholder resolution, header-override behaviour, special-character percent-encoding).
- Add `DL-0014: Tagged Body Encoding On Manifest v1 Requests` (Accepted) to `ai/DECISION_LOG.md`.
- Add a `CHANGELOG.md` entry under `[Unreleased] → Added`.
- Update `ai/HANDOVER.md` "What Was Just Added" and "Next Recommended Work" sections, and append a dated entry to `ai/DEVELOPMENT_LOG.md`.

Do not:

- Touch v0 manifests in any way.
- Add `multipart/form-data` support.
- Add masking to step records (still deferred per DL-0013).
- Wire mTLS, JWS, OAuth2 grant types, or callback handling.
- Introduce a new HTTP client or refactor every existing `send_json` / `get_json` call site beyond what is required to share the body-agnostic dispatch path.
- Change the report schema, REST API, or any UI surface.
- Add JWT/JWS dependencies, or any new runtime dependency without a justified `pyproject.toml` + `uv.lock` update.

# Design Constraints

- Follow the repository instructions and existing project style.
- Keep changes small, focused, and consistent with the `ai/` workspace direction.
- Prefer typed, structured data models over untyped dictionaries for domain data — use a `dataclass`-union body model, not a `dict[str, Any]`.
- Validate external input (manifest) with clear errors at parse time.
- Do not commit secrets, credentials, tokens, certificates, or participant-sensitive data. The token-exchange example must use placeholders, never literal secrets.
- Do not hardcode certification criteria or masking rules that should be configuration-driven.
- Add or update Google-style docstrings on every new or modified public **and private** module, class, function, or method (DL-0010, enforced by `interrogate` at 100%).
- Add tests for meaningful business logic using the correct pytest markers.
- Update `ai/DECISION_LOG.md` (`DL-0014`) — the tagged body shape is a durable schema decision.
- Update `ai/HANDOVER.md` and `ai/DEVELOPMENT_LOG.md` for the next session.

# Acceptance Criteria

The task is done when:

- A v1 manifest step with `body: {"encoding": "form", "fields": {...}}` on POST is parsed, placeholders inside `fields` are resolved at execution time, and the resulting HTTP request is sent with `Content-Type: application/x-www-form-urlencoded` and a correctly percent-encoded body (asserted via `httpx.MockTransport`).
- A manifest-supplied `Content-Type` header (any case) overrides the default form Content-Type — covered by a test.
- Every rejection case in the plan has a parser test with a clear error: form-on-GET, empty `fields`, non-string value, unknown `encoding`, malformed placeholder in a value.
- Existing JSON body, header templating, and v0 behaviour are unchanged (regression tests pass).
- The step record stores method + resolved URL only; form fields are not recorded (asserted by a test, with an inline executor comment naming the masking deferral).
- `config/manifest-v1-token-exchange-example.json` uses the tagged form shape.
- `CHANGELOG.md` has an `[Unreleased] → Added` entry.
- `ai/DECISION_LOG.md` has `DL-0014` recording the tagged body shape, implicit-JSON back-compat, and default-Content-Type override rule.
- `ai/HANDOVER.md` and `ai/DEVELOPMENT_LOG.md` are updated.
- `make check` passes (ruff, mypy strict, interrogate 100%, pytest, coverage ≥ 80%).

# Suggested Implementation Path

1. Read `.github/copilot-instructions.md`, the `ai/` files listed above, and `ai/plans/2026-05-28-form-urlencoded-body.md`.
2. Inspect the current parser, context, http, and executor modules to find the smallest seam for the tagged body model.
3. Write parser tests for the new shape and every rejection case first.
4. Add the typed body model and parser changes; get parser tests green.
5. Extend the HTTP helper to support form dispatch via httpx's `data=`; add `httpx.MockTransport` tests asserting the wire body and Content-Type behaviour.
6. Wire the executor to dispatch on body type, resolve placeholders in `fields`, and keep the step record minimal.
7. Update the token-exchange example manifest.
8. Update `CHANGELOG.md`, add `DL-0014`, update `ai/HANDOVER.md` and `ai/DEVELOPMENT_LOG.md`.
9. Run `make check`. Fix anything it surfaces.
10. Summarise changed files, verification results, decisions made, and any follow-up risks (especially the still-deferred masking work) in the final message.
