# Implementation Plan — HTML Report View

> **Status:** Draft
> **Branch:** `feature/html-report-view`
> **Target:** `develop`
> **Audience:** AI implementation agent
> **PRD references:** Participant Story #10, #11, #12, #14; OBL Engineering Story #6 (Phase-2 ready)

---

## 1. Why this is the next feature

The engine, REST API, structured result file, NDJSON execution log, masked
request/response evidence, and `TestPlan` with certification-eligibility
tracking are all landed and stable on `develop`. The most recent changelog
entry explicitly calls out the new top-level `plan` block as being _"consumed
by the upcoming HTMX plan-builder UI"_.

A full plan-builder is too large for one PR. The smallest meaningful slice
that unlocks it (and directly satisfies a PRD user story) is a **read-only
HTML report view** that renders the existing JSON result for a completed run.
It:

- Bootstraps the Django template layer (currently unused — Django is wired
  but no `templates/` directory exists) and the HTMX dependency, so the
  follow-up plan-builder PR is purely additive.
- Surfaces the just-stabilised `summary`, `plan`, `certificationEligibility`,
  and per-step masked evidence blocks in a human-readable form.
- Is a pure presentation layer over an already-stable artefact — zero engine,
  security-profile, or transport changes.
- Cleanly inherits the existing loopback security guard.
- Implements PRD Participant Story #10 (_"receive a structured report ... so
  that I can review all test outcomes"_) and renders the data needed for
  stories #11 (eligibility), #12 (request/response on FAIL), and #14
  (PASS/FAIL/WARN/SKIPPED outcome states).

**Out of scope for this PR** (deferred to follow-ups):

- Interactively building or deselecting from a `TestPlan` (plan-builder UI)
- Triggering a run from the UI (callers continue to use `POST /api/runs/`)
- Authentication, theming, CSS framework, JS bundling
- Live tailing of the NDJSON execution log
- Any change to the engine, masking, manifest schema, or result-file shape

---

## 2. Deliverables

### 2.1 Code

1. **New Django app: `conformance/web/`** with the same in-repo layout as
   `conformance/api/` (`__init__.py`, `urls.py`, `views.py`,
   `templates/web/...`). Adding it as a sibling of `conformance/api/`
   preserves the separation of concerns the PRD requires between the
   engine, the API, and the presentation layer.

2. **Shared loopback decorator.** Extract the existing private
   `_require_loopback` and `_is_loopback_address` from
   [conformance/api/views.py](conformance/api/views.py) into a new module
   `conformance/api/loopback.py` (or `conformance/web_security.py` if the
   reviewer prefers a non-`api` home — pick one and justify in the PR
   description). Re-export the decorator from `conformance.api.views` so
   the API call sites are unchanged. The new web views import the same
   decorator. Behaviour must be byte-identical to today; the existing API
   loopback tests in [tests/test_api.py](tests/test_api.py) must continue
   to pass without modification.

3. **Two routes, mounted at the project root** (alongside `health/`,
   `api/`, `admin/` in [config/urls.py](config/urls.py)):

   - `GET /runs/` — list of recent runs from `RunStore` (pending, running,
     completed, failed). Most recent first. Each row links to the detail
     view. Empty-state message when the store is empty (the in-memory
     store retains a bounded history of 10 terminal records).
   - `GET /runs/<run_id>/` — detail view for a single run. Renders:
     - Run id, environment, status, started/completed timestamps
     - The `certificationEligibility` block as a prominent verdict banner
       (eligible / not eligible + reason), with mandatory pass/fail/warn/
       skipped/deselected counts
     - The `plan` block (when present) — total, selected, deselected,
       mandatorySelected, mandatoryDeselected
     - The `summary` block (passed/failed/warn/skipped totals)
     - Per-step table grouped by status, with name, URL, HTTP status code,
       outcome badge, and an expandable details panel showing the masked
       request/response evidence already stored in `details.request` /
       `details.response` / `details.warning`
     - A "Download JSON report" link to the existing
       `GET /api/runs/<run_id>/result/` endpoint
     - A "Download execution log" link to the existing
       `GET /api/runs/<run_id>/log/` endpoint
     - For non-terminal runs (`pending`, `running`): a one-line "Run still
       in progress" notice with an HTMX `hx-get` polling stub that
       re-fetches the page every 2s. Polling stub only — no new API
       endpoint, no streaming. The page must still render correctly with
       JavaScript disabled.

4. **HTMX delivery.** Vendor a pinned, integrity-hashed copy of HTMX (e.g.
   `htmx.org@2.x`) into `conformance/web/static/web/htmx.min.js`. Do **not**
   load it from a CDN — Phase 1 ships a hardened Docker image with localhost
   binding and must work offline. Reference it from the base template with
   a `<script integrity="sha384-...">` tag.

5. **Templates** under `conformance/web/templates/web/`:
   - `base.html` — minimal layout: `<title>`, container `<div>`, slot for
     `{% block content %}`, single `<link>` to one small `web/styles.css`,
     single `<script>` to vendored HTMX. No external resources.
     `{% csrf_token %}`-ready (no POSTs in this PR, but the base must not
     preclude them).
   - `runs/list.html`
   - `runs/detail.html`
   - `runs/_step_row.html` partial (used by detail; lets a future
     plan-builder PR re-use the row shape).
   - `runs/not_found.html` — 404 body.
   - `runs/_eligibility_banner.html` partial.

6. **Small CSS file** at `conformance/web/static/web/styles.css`. Plain
   hand-written CSS. Outcome badges (`passed`, `failed`, `warn`, `skipped`)
   styled with accessible colour contrast (WCAG AA). No framework, no
   build step.

7. **`RunStore.list_runs()` method** added to
   [conformance/api/run_store.py](conformance/api/run_store.py). Returns a
   list of `RunRecord` snapshots ordered by `created_at` descending.
   Snapshot semantics must match `get_run` (deep-copied / immutable view
   under the existing lock). Add a corresponding unit test in
   [tests/test_api.py](tests/test_api.py).

### 2.2 Tests

Add view tests under `tests/test_web.py` using Django's test client with
`@pytest.mark.integration` (the marker already used for offline Django/DB
tests — see [pyproject.toml](pyproject.toml)). Cover:

- List view with: empty store, one pending run, one completed run, one
  failed run, the eviction boundary (10 retained terminal records)
- Detail view: 404 for unknown id, pending/running render, completed
  passed run, completed failed run with masked request/response evidence,
  completed run with non-eligible certification, completed run with
  deselected mandatory steps, completed v0 run (no `plan` block — must
  render without crashing)
- Loopback guard parity: a non-loopback `REMOTE_ADDR` on both routes
  returns 403; `CONFORMANCE_API_ALLOW_NON_LOCAL=true` opt-out works
- Outcome badge HTML is present for each of `passed`, `failed`, `warn`,
  `skipped`
- Masking: a deliberately injected `Authorization: Bearer secret123`
  rendered in the page body shows `***`, never the literal token (defence
  in depth — masking happens upstream in the engine; the view test
  confirms the page does not accidentally unmask)

No new engine tests. No changes to existing tests beyond test-id /
fixture additions.

### 2.3 Documentation

- **README.md** — add a short "Web UI" section under "Model-bank smoke
  check" describing the two routes, the loopback binding requirement, and
  the offline-by-design (vendored HTMX) note.
- **CHANGELOG.md** — entry under `[Unreleased]` → `Added`, citing PRD
  Participant Story #10 and noting the deferred plan-builder follow-up.
- **No new ADR / decision-log entry required** — this is a presentation
  layer over existing artefacts, not an architectural decision. If the
  reviewer disagrees, add a short note in the PR description.

### 2.4 Quality gates

- `make check` must pass clean (ruff, mypy strict, pytest unit +
  integration, interrogate at 100%, detect-secrets). See
  [/memories/repo/checks.md] — after adding tests, run `make check` (not
  just pytest) and `git add .secrets.baseline` first if line numbers in
  tracked files have shifted.
- New code must carry full Google-style docstrings on every module, class,
  function, and method (public and `_private`) — interrogate is at 100%
  and `ignore-private = false`.
- Test coverage must not drop below 80%.

---

## 3. Suggested commit sequence

Keep commits small and reviewable. One sensible ordering:

1. `refactor(api): extract loopback guard into reusable module`
2. `feat(api): add RunStore.list_runs() snapshot method`
3. `chore(web): scaffold Django web app, base template, vendored HTMX`
4. `feat(web): add GET /runs/ list view`
5. `feat(web): add GET /runs/<id>/ detail view with eligibility + evidence`
6. `test(web): cover list and detail views, loopback parity, masking`
7. `docs: README + CHANGELOG entry for HTML report view`

---

## 4. Non-goals / explicit guard rails

The agent **must not**:

- Modify the engine, executor, manifest schema, result-file shape, masking
  module, NDJSON log format, or any existing API endpoint
- Change the loopback guard's behaviour (only relocate it)
- Add interactive plan-building, run-triggering, or any form submission
- Pull a CSS framework, JS bundler, or any package from a CDN at runtime
- Add authentication or session management
- Expose the run id in URLs without validating it as a UUID4 hex string
  (defence in depth against path traversal / template injection)
- Render any unmasked field from `details` — rely on engine masking and
  Django auto-escaping; do not call `mark_safe` anywhere
- Add a new pytest marker — reuse `@pytest.mark.integration`
- Lower the interrogate or coverage thresholds
- Push to `main` or `develop` directly; merge only via PR review

---

## 5. Acceptance checklist

- [ ] `make check` passes locally
- [ ] New routes return 403 from a non-loopback caller by default
- [ ] List view renders empty, in-flight, and terminal runs
- [ ] Detail view renders v1 (with `plan` block) and v0 (without) runs
- [ ] Eligibility banner is correct for: eligible, ineligible-with-failures,
      ineligible-with-mandatory-deselected, ineligible-with-no-mandatory
- [ ] All four outcome badges (PASS/FAIL/WARN/SKIPPED) render
- [ ] Masked tokens in step evidence appear as `***`
- [ ] Page renders with JavaScript disabled (HTMX progressive enhancement)
- [ ] HTMX served from local static, not a CDN; `<script integrity=...>`
      present
- [ ] CHANGELOG `[Unreleased]` updated under `Added`
- [ ] README "Web UI" section added
- [ ] PR description references this plan and Participant Story #10
