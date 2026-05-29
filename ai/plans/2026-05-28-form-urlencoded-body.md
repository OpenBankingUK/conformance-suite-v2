# Plan: Form-urlencoded Request Body For Manifest v1

Date: 2026-05-28
Status: Proposed
Author: AI agent triage (Claude Opus 4.7 via GitHub Copilot)

## Goal

Add `application/x-www-form-urlencoded` request body support to the v1 manifest engine so that the M4 OAuth2 token-exchange step can be expressed declaratively without changing the executor again. Keep the engine domain-agnostic: this is an HTTP content-type capability, not an OAuth2 feature.

## Non-Goals

- mTLS client-cert wiring (separate slice).
- JWS request-object signing (separate slice).
- Callback / redirect handling (separate slice).
- `multipart/form-data`. Not needed by any Phase 1 ASPSP flow yet.
- Masking of secrets in step records (still deferred until the masking milestone, per DL-0013).
- Changing v0 in any way.
- Touching the report schema or REST API.

## Source Documents And Decisions Used

- `docs/FCS Rebuild - PRD v3 [DRAFT].md` §"Engine Architecture", §"Security Profile" (FAPI 1 Advanced hybrid flow needs token exchange).
- `ai/HANDOVER.md` → "Next Recommended Work" item 1.
- `ai/DECISION_LOG.md` DL-0012 (v1 schema + placeholder grammar), DL-0013 (non-GET + headers + JSON body; "Open consequence" line about form-urlencoded).
- `config/manifest-v1-token-exchange-example.json` (current example using JSON body; will be updated).

## Proposed Design

### Manifest shape

Introduce a discriminated body shape on a v1 step request. Backwards compatible: an unannotated `body` continues to mean JSON.

Preferred shape (tagged):

```json
"request": {
  "method": "POST",
  "url": "${steps.discovery.response.body.token_endpoint}",
  "headers": { "Accept": "application/json" },
  "body": {
    "encoding": "form",
    "fields": {
      "grant_type": "authorization_code",
      "code": "${steps.consent.response.body.code}",
      "client_id": "test-client"
    }
  }
}
```

Rules:

- `body.encoding` is an enum: `"json"` (default if omitted, equivalent to today's behaviour) or `"form"`.
- When `encoding == "form"`:
  - `fields` is a `dict[str, str]` of name→value.
  - Names must be non-empty strings; values must be strings (after placeholder resolution they remain strings).
  - Placeholder substitution is applied to every value leaf (same grammar as DL-0012).
  - Empty `fields` is rejected at parse time (no point sending an empty form body).
  - The executor sets `Content-Type: application/x-www-form-urlencoded` automatically **only** if the manifest did not already supply a `Content-Type` header (case-insensitive). This keeps the engine declarative and predictable.
  - Allowed on `POST`, `PUT`, `PATCH`, `DELETE`. Rejected on `GET` (same as JSON body today).
- When `encoding == "json"` (or `body` is a bare JSON value): no behaviour change from today.

Why discriminated rather than a magic header: declared intent in the manifest is easier to validate at parse time, makes substitution rules unambiguous (every value is a string), and avoids surprising behaviour where a header alone silently changes wire encoding.

### Parser

- `conformance/manifest.py`:
  - Add an internal helper `_parse_v1_body(raw, method)` that detects the tagged shape `{"encoding": "form", "fields": {...}}` and returns a typed body model.
  - Introduce a small typed body model (frozen dataclass or `Literal`-tagged union) that the executor can dispatch on. Suggested shape:
    ```python
    @dataclass(frozen=True, slots=True)
    class JsonBody:
        value: JsonValue
    @dataclass(frozen=True, slots=True)
    class FormBody:
        fields: Mapping[str, str]
    type ManifestBody = JsonBody | FormBody
    ```
    with attribute docstrings per DL-0009.
  - Update `ManifestRequest.body` to `ManifestBody | None`.
  - Reject `encoding == "form"` on `GET`.
  - Reject empty `fields`.
  - Validate placeholder syntax inside every `fields` value (reuse `_validate_placeholders_in_structure`).

### Context

- `conformance/context.py` already has `resolve_in_structure`. No new helper needed; the executor will call it on the `fields` dict before dispatch.

### HTTP layer

- `conformance/http.py`:
  - Extend `send_json` (or add a sibling `send_request`) so callers can choose between JSON and form encoding without leaking transport details to the executor.
  - Preferred: rename the dispatcher to a body-agnostic name (e.g. `send_request(method, url, *, headers, json=None, form=None)`) and keep a thin `get_json` / `send_json` for back-compat where it is still useful, **or** keep `send_json` and add `send_form`. Pick whichever produces the smaller diff; do not refactor every call site.
  - For form: pass `data=fields` to `httpx.Client.request`, which encodes correctly. Do not hand-roll `urllib.parse.urlencode`.
  - Headers: build with `httpx.Headers` (already done in current `send_json` — keep the case-insensitive merge).
  - Default `Content-Type` only when the caller has not supplied one.

### Executor

- `conformance/executor.py`:
  - `_execute_v1_step` dispatches on the parsed body type. JSON → existing path. Form → resolve placeholders in `fields`, call the form path of the HTTP helper.
  - Record method and resolved URL in the step record as today. **Do not** record form fields in the step record yet — masking is still deferred (DL-0013). Add an inline comment naming the masking deferral.

### Example

- Update `config/manifest-v1-token-exchange-example.json` to use the tagged form shape for the token exchange step. This is the canonical motivating example.

### Tests

All `@pytest.mark.unit` unless otherwise noted.

Parser (`tests/test_manifest.py`):

- Accepts a valid `{"encoding": "form", "fields": {...}}` body on POST.
- Accepts placeholders inside `fields` values.
- Rejects `encoding == "form"` on GET with a clear message.
- Rejects empty `fields`.
- Rejects non-string `fields` values.
- Rejects unknown `encoding` values.
- Rejects malformed placeholder syntax inside `fields`.
- Existing JSON-body tests continue to pass (regression).

HTTP (`tests/test_http.py`):

- Form dispatch sends `application/x-www-form-urlencoded` with a correctly encoded body (assert the wire body via `httpx.MockTransport`).
- Manifest-supplied `Content-Type` (any case) overrides the default form Content-Type.
- Special characters in form values are percent-encoded.
- Default `Accept` header is preserved if the manifest does not override it.

Executor (`tests/test_executor.py`):

- POST step with form body and placeholders is dispatched with resolved values.
- Step record contains the resolved URL and method (form fields are intentionally not recorded — assert absence to lock in DL-0013 deferral).

## Security And Certification Implications

- No new secret material handled. Form-urlencoded bodies for OAuth2 token exchange will eventually carry secrets (authorization codes, client secrets), but this slice intentionally does **not** add those to step records — masking is still owed. Add an inline comment naming the deferral so it is impossible to miss in review.
- httpx encodes form data using percent-encoding; do not hand-roll. Prevents accidental injection / control-character leakage.
- Defence in depth: keep parser rejection of empty fields, unknown encoding, GET-with-form, non-string values. These prevent malformed conformance evidence reaching the wire.

## Rollout / Migration

- Backwards compatible. Existing manifests with bare JSON `body` keep working; only the tagged shape unlocks form encoding.
- Update only the token-exchange example. Other examples stay on JSON.
- Bump nothing in versioning yet — this is additive within manifest schema v1.

## Open Questions

1. Should we keep the implicit "bare body == JSON" shape, or require an explicit `{"encoding": "json", "value": ...}` for new manifests going forward? Recommendation: keep implicit JSON for now; revisit when masking lands.
2. Should `headers` substitution and `fields` substitution share an error frame, or report independently? Recommendation: independent — easier debugging from manifest authors.
3. Once masking arrives, should the step record store the **raw** field values, the **resolved** values, or the **masked-after-resolve** values? Leave as a DL entry when masking is implemented.

## Decision-Log Impact

- Add `DL-0014: Tagged Body Encoding On Manifest v1 Requests` (Accepted) summarising the discriminated shape, the implicit-JSON back-compat, and the default-Content-Type override rule.
