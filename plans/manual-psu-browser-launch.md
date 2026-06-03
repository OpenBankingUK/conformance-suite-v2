# Plan: Browser Manual PSU Authorisation Launch

> Source PRD: [docs/FCS Rebuild - PRD v3 [DRAFT].md](../docs/FCS%20Rebuild%20-%20PRD%20v3%20%5BDRAFT%5D.md)
> Branch: `feature/manual-psu-browser-launch`
> Base branch: `develop`
> Draft PR title: `feat(ui): support browser manual PSU authorisation launch (#29)`
> Created: 2026-06-03

## Chosen Next Feature

Build browser launch support for v1 manifests that contain manual `psu-authorization` steps.

This is the optimal next feature because the current codebase has already delivered the surrounding vertical slice: participants can paste config plus a v1 manifest into `/plan/`, preview mandatory/optional selection, launch HTTP-only runs, and monitor status/results/logs at `/runs/<run_id>/`. The deliberate gap is that manual PSU manifests can be previewed but are blocked from browser launch because the UI does not yet have a raw authorisation URL handoff that avoids persisting sensitive query values.

This feature advances the PRD's core Phase 1 participant journey: guided manual PSU authorisation from the browser UI, followed by one-trigger execution, status monitoring, masked logs, and a structured report.

## Current Codebase Surface

Already present:

- v1 manifest parsing for HTTP and `psu-authorization` steps.
- Manual and headless PSU execution in the engine.
- Shared `AuthSessionStore` plus public `GET /callback/` capture flow.
- CLI raw URL handoff through `PsuAuthorizationUrlConsoleLogger`, while `BufferedExecutionLogger` persists only masked payloads.
- REST API start/status/result/log endpoints.
- Browser plan builder, launch form, run detail page, masked JSON/NDJSON downloads, and certification summary rendering.

Current gap:

- `PlanPreview.launch_supported` is false when a manifest includes a manual PSU step.
- The UI has no transient, browser-facing handoff for the raw authorisation URL emitted by the executor.
- The docs explicitly say browser launch for manual PSU is deferred.

## Durable Architectural Decisions

- **Deployment model**: Keep Phase 1 assumptions: single process, in-memory state, one active run at a time, no database migration.
- **Branch and PR**: All implementation chunks use `feature/manual-psu-browser-launch`, targeting `develop` through one draft PR.
- **Route shape**: Preserve the existing participant entry points: `/plan/`, `/plan/preview/`, `/plan/launch/`, and `/runs/<run_id>/`. Prefer adding a small run-detail partial or panel over creating a new top-level workflow.
- **No REST API contract change unless needed**: The REST API already supports manual PSU runs via logs/callback. This feature is for browser UX.
- **Transient raw URL storage**: The raw authorisation URL may exist only in process memory and in the participant-facing browser handoff response. It must not be written to `RunRecord.result`, persisted NDJSON, result JSON, docs examples, exception messages, or application logs.
- **Handoff capture pattern**: Reuse the existing logger-decorator pattern. A browser handoff decorator should observe the raw `psu-authorization-url` event before forwarding to `BufferedExecutionLogger`, which continues to mask persisted events.
- **Handoff data model**: Use a small run-scoped value such as `PsuAuthorizationHandoff` with `run_id`, `step_id`, `state`, `url`, `mode`, and timestamps/status. Keep it in a thread-safe in-memory store sibling to `RunStore` and `AuthSessionStore`.
- **Cleanup**: Drop handoffs when the matching callback is captured and when the parent run reaches a terminal state. Stale authorisation links must not remain visible after completion/failure.
- **Security posture**: Do not add CSRF exemptions to browser POSTs. Do not build redirects, shell commands, SQL, or file paths from request input. If any server-side external redirect is introduced, document and test why it is generated only from a parsed, HTTPS-validated manifest URL, not from request parameters. Prefer a direct participant-visible link if it satisfies the UX without a redirect endpoint.
- **Caching**: Any response that can include a raw authorisation URL should use `Cache-Control: no-store`.
- **Dependencies**: Do not add dependencies. Django, httpx, and the standard library are enough.
- **Documentation/release note**: Because the final branch implements participant-facing behavior, update `CHANGELOG.md` under `[Unreleased]` before the PR is marked ready.

## Agent Chunk Rules

Each chunk below is sized for a separate AI agent context window on the same branch.

Before starting any chunk:

```bash
git fetch origin
git switch feature/manual-psu-browser-launch
git pull --ff-only
```

After each chunk:

- Run the targeted tests named in the chunk.
- Keep edits focused on that chunk.
- Do not revert unrelated user or agent changes.
- Commit only when the user or coordinating agent asks for commits; otherwise leave the working tree ready for review.
- Include Google-style docstrings for every new Python module, class, function, method, private helper, and module-level type alias/constant attribute docstring where applicable.

## Phase 1: Transient Browser Handoff Primitive

**User stories**: Participant #8, #10, #13, #25; Engineering #6, #7.

### Context Pack

Read first:

- `plans/manual-psu-browser-launch.md`
- `conformance/execution_log.py`
- `conformance/api/run_store.py`
- `conformance/api/auth_session_store.py`
- `conformance/api/run_lifecycle.py`
- `tests/test_execution_log.py`

### What To Build

Add a thread-safe, run-scoped browser handoff store and a logger decorator that captures raw `psu-authorization-url` payloads for browser display before forwarding the event to the existing buffered logger.

The decorator should be narrow: it should only react to `psu-authorization-url` events with a string `url`, a string `state`, and a run id available from the wrapped logger or constructor. It should forward every event exactly once to the wrapped logger. It should expose `run_id` like the CLI decorator so executor fallback behavior remains stable.

The store should support at least:

- Recording a handoff for a run and step.
- Reading current active handoffs for a run.
- Marking or discarding a handoff by `(run_id, state)`.
- Discarding all handoffs for a run.
- Resetting only for tests.

### Acceptance Criteria

- [ ] A `psu-authorization-url` event creates a browser handoff containing the raw URL and state.
- [ ] The wrapped `BufferedExecutionLogger` still stores a masked `psu-authorization-url` event.
- [ ] Missing or malformed URL/state payloads do not create a handoff and do not block logging.
- [ ] Handoffs can be discarded by run/state and by run.
- [ ] Unit tests cover the handoff store and decorator.

### Targeted Checks

```bash
uv run pytest tests/test_execution_log.py -q
uv run pytest tests/test_api.py::TestCreateRunEndpoint -q
```

### Review Focus

- No raw URL in NDJSON serialization.
- No global mutable state without locking.
- Full docstrings on new private helpers.
- No new dependency.

## Phase 2: Wire Handoff Into Run Lifecycle And Launch Eligibility

**User stories**: Participant #4, #5, #6, #8, #10.

### Context Pack

Read first:

- `conformance/api/plan_builder.py`
- `conformance/api/ui_views.py`
- `conformance/api/run_lifecycle.py`
- `tests/test_plan_builder.py`
- `tests/test_ui_views.py`

### What To Build

Wire the browser handoff decorator into browser/API run lifecycle where the run has a `BufferedExecutionLogger`. Remove the manual PSU browser launch blocker once the handoff primitive is available.

Keep existing behavior for:

- HTTP-only browser launches.
- Active-run conflict rendering.
- Invalid form handling.
- CSRF protection on browser POSTs.
- REST API loopback guard.

### Acceptance Criteria

- [ ] A manual PSU manifest preview still renders the PSU row.
- [ ] `PlanPreview.launch_supported` is true for browser-supported manual PSU manifests.
- [ ] `POST /plan/launch/` starts a run for a manual PSU manifest instead of returning the previous blocker.
- [ ] Active-run conflicts and invalid form responses are unchanged.
- [ ] Existing HTTP-only UI tests still pass.

### Targeted Checks

```bash
uv run pytest tests/test_plan_builder.py tests/test_ui_views.py::TestPlanBuilderUi -q
```

### Review Focus

- Do not weaken CSRF posture.
- Keep all user input behind `PlanBuilderForm`.
- Do not expose raw request payloads through error messages.

## Phase 3: Browser Run Detail Handoff UI

**User stories**: Participant #8, #10, #12, #13, #25.

### Context Pack

Read first:

- `conformance/api/ui_views.py`
- `conformance/api/templates/conformance/run_detail.html`
- `conformance/api/templates/conformance/partials/run_status.html`
- `conformance/api/templates/conformance/partials/run_log.html`
- `conformance/api/templates/conformance/partials/run_result.html`
- `tests/test_ui_views.py`

### What To Build

Render a compact PSU authorisation handoff panel on the run detail page whenever the active run has an unresolved browser handoff. The panel should let the participant open the authorisation URL and then return to the run page after the ASPSP callback.

Use the existing run detail refresh pattern. If the raw URL appears in an HTML response, mark the response `Cache-Control: no-store`. Avoid adding visible explanatory copy beyond what is needed for the immediate action and status.

### Acceptance Criteria

- [ ] A running manual PSU run shows an authorisation action on `/runs/<run_id>/` after the executor emits the handoff event.
- [ ] A run with no active handoff does not show the panel.
- [ ] The response that includes the raw URL is not cacheable.
- [ ] The raw URL is not present in masked log downloads or result downloads.
- [ ] The page remains responsive on mobile-width layouts without text overlap.

### Targeted Checks

```bash
uv run pytest tests/test_ui_views.py::TestRunDetailUi -q
```

### Review Focus

- Raw authorisation URL is deliberately participant-facing but not persisted server-side.
- No unsafe server-side redirect unless explicitly justified and tested.
- No nested cards or layout shifts beyond the existing style.

## Phase 4: End-To-End Browser Manual PSU Flow

**User stories**: Participant #6, #8, #10, #11, #13, #25.

### Context Pack

Read first:

- `tests/test_ui_views.py`
- `tests/test_api.py`
- `conformance/api/callback_views.py`
- `conformance/executor.py`
- `config/manifest-v1-psu-authorization-example.json`

### What To Build

Add integration coverage for launching a manual PSU manifest through the browser UI, waiting for the browser handoff, completing the flow through `/callback/`, and observing a passed result on the run detail/result surfaces.

Use deterministic manifest values and short PSU timeouts so tests do not hang. Avoid high-entropy test strings that can trip detect-secrets. If a false positive appears, update `.secrets.baseline` intentionally and include it in the branch.

### Acceptance Criteria

- [ ] Browser launch creates a run for a manual PSU manifest.
- [ ] The test can observe the handoff without scraping unmasked NDJSON.
- [ ] `GET /callback/?state=...&code=...` completes the awaiting session.
- [ ] The run completes with a passed PSU step.
- [ ] Masked log/result assertions prove credential-bearing URL fields and captured code are not persisted in clear text.
- [ ] Callback completion clears or resolves the visible handoff.

### Targeted Checks

```bash
uv run pytest tests/test_ui_views.py tests/test_api.py -q
```

### Review Focus

- Tests should exercise public behavior, not private implementation details, except where unit testing the new store/decorator.
- Keep sleeps/polls bounded and deterministic.
- Preserve callback non-enumerability: no response body should echo `state`, `code`, or ASPSP-supplied free text.

## Phase 5: Documentation, Changelog, And PR Review Pass

**User stories**: Participant #23, #24; Standards #8; Engineering #7.

### Context Pack

Read first:

- `README.md`
- `docs/DEVELOPER_GUIDE.md`
- `CHANGELOG.md`
- `.github/copilot-instructions.md`
- `pyproject.toml`

### What To Build

Update participant/developer documentation to describe the browser manual PSU launch path and remove statements saying it is deferred. Add a `[Unreleased]` changelog entry under `Added` or `Changed` for the completed behavior.

Then review the draft PR locally before asking for external review.

### Acceptance Criteria

- [ ] README and developer guide describe the browser manual PSU flow accurately.
- [ ] Changelog has an `[Unreleased]` entry for the behavior before the PR is marked ready.
- [ ] The PR body links to this plan and summarizes the security model.
- [ ] `make check` passes.
- [ ] No new Snyk-relevant dependencies were added.

### Required Checks

```bash
make check
git --no-pager diff --check
```

### Copilot Review Pre-Flight

Before requesting Copilot PR review or marking the PR ready, inspect the diff for these likely findings:

- [ ] Browser POST routes still require CSRF tokens.
- [ ] No `@csrf_exempt` was added.
- [ ] No raw SQL, shell command construction, or user-derived file paths were added.
- [ ] Any external authorisation URL handoff is generated from parsed manifest data, not request query parameters.
- [ ] Raw authorisation URLs and captured codes are absent from persisted NDJSON/result JSON when developer mode is off.
- [ ] Responses containing raw handoff URLs are marked `no-store`.
- [ ] Every new Python object has Google-style docstrings, including private helpers.
- [ ] New tests use declared pytest markers: `unit`, `integration`, `ozone`, or `e2e`.
- [ ] No `# noqa` or `# type: ignore` was added without an inline justification.
- [ ] `CHANGELOG.md` is updated under `[Unreleased]` for the final feature branch.
- [ ] `uv.lock` is unchanged unless dependencies were intentionally changed.

## Draft PR Body

Use this branch's draft PR as the coordination surface for the implementation agents.

Suggested body:

```markdown
## Summary

Implements browser launch support for v1 manifests containing manual `psu-authorization` steps.

This follows the implementation plan in `plans/manual-psu-browser-launch.md`. The intended design keeps raw authorisation URLs transient: visible only to the participant for the browser handoff, while NDJSON logs and result JSON continue to store masked values.

## Agent Work Chunks

- Phase 1: transient browser handoff primitive
- Phase 2: lifecycle wiring and launch eligibility
- Phase 3: run detail handoff UI
- Phase 4: end-to-end browser manual PSU flow
- Phase 5: docs, changelog, and PR review pass

## Validation

- [ ] Targeted tests for changed components
- [ ] `make check`
- [ ] Local PR review against `.github/copilot-instructions.md`

## Security Notes

- No raw authorisation URL should be persisted in result JSON or NDJSON logs.
- Browser POST routes remain CSRF-protected.
- The public `/callback/` security model remains state unguessability, one-shot capture, and run-scoped binding.
```

## Ready-To-Start Command Summary

```bash
git fetch origin
git switch feature/manual-psu-browser-launch
git pull --ff-only
uv run pytest tests/test_plan_builder.py tests/test_ui_views.py tests/test_execution_log.py -q
```