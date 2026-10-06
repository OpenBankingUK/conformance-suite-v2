# Developer Guide

## Prerequisites

- Python 3.14+ (managed via `.python-version`)
- [uv](https://docs.astral.sh/uv/) package manager
- Docker for container builds
- GNU Make

## Getting started

```bash
git clone <repo-url>
cd conformance-suite-v2
uv sync --frozen --no-install-project
git config core.hooksPath .githooks
```

## Running the application

| Command | Server | Auto-reload | Use case |
| --- | --- | --- | --- |
| `make dev` | Uvicorn (HTTPS) on `0.0.0.0:8443` | Yes | Day-to-day browser development. |
| `make dev-unmasked` | Uvicorn (HTTPS) on `0.0.0.0:8443` | Yes | Local engine debugging with unmasked logs. |
| `make serve` | Uvicorn on `0.0.0.0:8443` | No | Local production-behaviour check. |
| `make docker` | Uvicorn in Docker | Yes | Hardened, production-like container run with generated local TLS (see [`docs/DOCKER_GUIDE.md`](DOCKER_GUIDE.md) for the full participant-facing guide). |

All runtime entry points bind to port `8443` so callback registrations against
the legacy FCS callback URI continue to reach the local application.

`make dev-unmasked` can write credentials and tokens in clear text to
developer-visible logs. Use it only for local debugging.

## Local checks

Run `make check` before pushing. It runs secret scanning, ruff lint/format
checks, mypy strict, and the complete offline test suite with coverage.
The tracked-file secret scan runs first and can take around 15 seconds on a
developer machine; `make check` reports its progress before pytest starts. The
local scan skips versioned `*-openapi.json` reference snapshots because their
size makes entropy scanning expensive. The staged-file hook and CI continue to
scan those files. CI requests that full scan explicitly with
`make check SECRET_SCAN_EXCLUDE_PATHSPEC=`.

```bash
make secrets
make lint
make test
make check
```

The supported suite is offline-only and split into two categories. Every
collected test must carry exactly one of them; `tests/conftest.py` fails
collection otherwise. The same conftest installs a session-wide socket guard:
any attempt to connect to a non-loopback address fails the test immediately, so
"offline" is enforced rather than assumed.

Category is a directory: tests live under `tests/unit/<domain>/` or
`tests/component/<domain>/`, each module declares its category once with a
module-level `pytestmark`, and no test file sits directly in `tests/`. Shared
fakes, fixtures, and builders live in `tests/support/`. See
[Testing strategy](TESTING_STRATEGY.md) for the full layout.

| Category | Scope | Focused command |
| --- | --- | --- |
| `unit` | Deterministic in-process behaviour: no sockets, no Django database or request boundary, and no background run worker. HTTP is mocked at the client boundary. In-process thread-safety checks that start and join their own threads stay here. | `make unit` |
| `component` | Offline collaboration through a boundary: Django request/response handling, persistence, filesystem behaviour, complete executor flows, background run-worker behaviour, or a narrowly justified loopback transport/TLS/mTLS/certificate fixture. | `make component` |

`make unit` and `make component` skip coverage so focused iteration stays fast.
`make test` runs `-m "unit or component"` and enforces the aggregate coverage
minimum.

No environment variables are needed for local checks. `settings.py` supplies a
safe `django-insecure-` fallback when `DJANGO_SECRET_KEY` is absent so tooling
can boot Django without production configuration.

## Secret scanning

The repository uses `detect-secrets` and a staged-file pre-commit hook.

```bash
uv run detect-secrets scan --baseline .secrets.baseline --exclude-files '\.env$' --exclude-files 'uv\.lock$'
uv run detect-secrets audit .secrets.baseline
```

Only audit false positives. Move real secrets into environment variables,
untracked local files, or deployment secret stores.

## Environment variables

| Variable | Required | Purpose |
| --- | --- | --- |
| `DJANGO_SECRET_KEY` | No | Django signing key. `docker/entrypoint.py` generates and persists one automatically inside the container when unset; local source runs fall back to `settings.py`'s safe `django-insecure-` default. |
| `DJANGO_DEBUG` | No | Enables Django debug mode when `"true"`. |
| `DJANGO_ALLOWED_HOSTS` | No | Comma-separated allowed hosts. The container entrypoint defaults to `127.0.0.1,localhost` when unset, matching the documented localhost-only publish profile. |
| `DJANGO_SESSION_ENGINE` | No | Overrides the default server-side file session backend. |
| `DJANGO_SESSION_FILE_PATH` | No | Optional directory for file-backed browser session drafts. The container entrypoint defaults this to `<data-dir>/sessions` when a writable `/data` mount is present. |
| `CONFORMANCE_DATA_DIR` | No | Overrides the container's persistent data root (`/data` by default). Also redirects CLI `resultOutputPath`/`executionLogPath` defaults under `<data-dir>/results` and `<data-dir>/logs`. |
| `CONFORMANCE_DEVELOPER_MODE` | No | Disables masking for local engine debugging only. |
| `CONFORMANCE_TOOL_VERSION` | No | Overrides generated report `tool.version`. |

`make docker` runs the hardened profile (read-only root filesystem, all
capabilities dropped, `no-new-privileges`) and needs no manually supplied
Django secret or allowed-hosts configuration; see
[`docs/DOCKER_GUIDE.md`](DOCKER_GUIDE.md) for the full command and the
optional `/certs` mount contract. Local source runs fall back to the project
version in `pyproject.toml`, then `0+unknown` if no version can be resolved.

Browser wizard drafts use Django sessions. The default session backend is
server-side file storage, so `make dev` and `make dev-unmasked` do not require
running Django migrations before creating a builder draft. Do not switch to
signed-cookie sessions for the builder because imported plan documents may carry
participant-supplied secret values until launch or explicit export.

Credentials pasted or uploaded in the wizard are stored inline in the draft, so
private keys and software statement assertions live in that server-side session
store for the life of the draft. Each inline credential is capped at 64 KiB, is
never rendered back into the page once stored (the wizard shows only a
non-secret descriptor plus Keep/Replace/Clear controls), and is removed from the
draft by Clear. Inline material is redacted from logs, results, and safe plan
exports. Participants who prefer credentials never to reach the web tier should
continue to use absolute file paths with the read-only `/certs` mount.

## Catalogue architecture

The participant-facing source of truth is now a canonical JSON-first test plan,
not checked-in manifest examples or config-selected suites.

Core modules:

| Module | Role |
| --- | --- |
| `conformance.catalogue` | Domain model, canonical test-plan parser, compiler, applicability decisions, traceability model. |
| `conformance.catalogue_registry` | Registry of bundled catalogues available to CLI, API, and UI. |
| `conformance.catalogues.*` | Legacy FCS-derived catalogues for AIS, PIS, CBPII, VRP, and DCR 3.4. cVRP code is retained for future non-Open-Banking handling but is not bundled. |
| `conformance.dcr_execution` | Sequential DCR discovery, JOSE, token, management, state, cleanup, and masked evidence adapter. |
| `conformance.executor.run_compiled_test_plan` | Executes compiled catalogue plans through the existing hardened HTTP/PSU/signing engine. |
| `conformance.results` | Serializes catalogue traceability and certification reasons in result JSON. |

The compiler selects test cases by catalogue key, security profile, exact
implemented endpoint operations, and endpoint-scoped implementation
capabilities. Required capabilities are baseline endpoint coverage and are
selected automatically. Optional capabilities only generate implementation-
dependent cases when the participant declares them under the matching endpoint.
The compiler includes dependencies automatically, rejects mandatory applicable
deselection, snapshots runtime inputs without sensitive values, and marks
assertion overrides as non-certifying.

## Shared plan-document contract

The participant-facing contract is the canonical JSON-first test plan accepted
by the browser wizard, REST API, and CLI. Browser import/export uses schema
version `1.0`:

```json
{
  "schemaVersion": "1.0",
  "specification": {
    "family": "OBL_READ_WRITE",
    "version": "4.0.1",
    "openApiDocumentUpdate": "Update-1",
    "profile": "FAPI1_ADVANCED"
  },
  "securityEnvironment": {
    "discoveryUrl": "https://aspsp.example.com/.well-known/openid-configuration",
    "resourceBaseUrl": "https://resource.example.com"
  },
  "resourceGroups": [
    {
      "id": "AIS",
      "label": "Account and Transaction",
      "endpoints": [
        {
          "method": "GET",
          "path": "/open-banking/v4.0/aisp/accounts",
          "operationId": "GetAccounts",
          "capabilities": []
        }
      ]
    }
  ],
  "businessTestData": {
    "ais": {"accountIds": ["account-123"]},
    "inputs": {"accessToken": {"value": "token-reference-or-local-debug-value"}}
  },
  "metadata": {}
}
```

The canonical `specification.family` and `version` select one or more underlying
bundled catalogues. Read/Write `4.0.0` and `4.0.1` each map to their own copy of
the Open Banking v4.0 AIS, PIS, CBPII, and VRP catalogue areas (keyed by
`CatalogueKey.specification_version`; `CatalogueKey.version` is the shared `v4.0`
endpoint version), so one plan document can span multiple resource families and
the two versions' coverage can diverge independently. cVRP is intentionally not exposed
under this Open Banking UK boundary for now. Dynamic Client Registration 3.4 is
bound to the executable `open-banking/v3.4/dcr` catalogue and uses direct
endpoint scope with no synthetic resource group.

### OpenAPI document updates

Read/Write core specification pages only change with a version bump, but the
OpenAPI ("swagger") documents for a version are republished upstream in
`OpenBankingUK/read-write-api-specs` as updates. Each version in
`conformance.specification_registry` lists its `OpenApiDocumentUpdate`s in
publication order with the upstream tag and immutable commit. Plans select one
with `specification.openApiDocumentUpdate` (mandatory for Read/Write).

Catalogue assertions reference *logical* documents such as
`ob-read-write/account-info-openapi`. Compilation binds them to the selected
update's bundled snapshot, `ob-read-write/<catalogue-id>/<document>` (for example
`ob-read-write/v4.0.1-Update-1/account-info-openapi`), stored at
`conformance/standards/ob_read_write/openapi/<catalogue-id>/`. Naming follows
upstream history: `Baseline` for the original publication, then `Release-N` or
`Update-N` matching the tag terminology used at the time. Release candidates and
drafts are not bundled.

To add a new update (for example `v4.0.1-Update-2`):

1. Download the four YAML-generated JSON documents (`account-info-openapi.json`,
   `payment-initiation-openapi.json`, `confirmation-funds-openapi.json`,
   `vrp-openapi.json`) from `dist/openapi/` at the tag's commit into
   `conformance/standards/ob_read_write/openapi/v4.0.1-Update-2/`.
2. Write `sources.json` beside them, copying an existing one: record the tag,
   commit, raw URLs, and each file's SHA-256 split into eight 8-character
   `sha256Chunks` (this keeps detect-secrets quiet).
3. Append an `OpenApiDocumentUpdate` to the version's
   `openapi_document_updates` tuple in `conformance/specification_registry.py`.
   The last entry is treated as the latest and preselected in the builder.
4. Run `make check`. `tests/unit/catalogue/test_openapi_document_updates.py`
   verifies hashes, registry/directory consistency, and that every catalogue
   `schemaRef` resolves in every update; fix any schema renames it reports.
5. Add a `CHANGELOG.md` entry under `[Unreleased]`.

Each exact specification version declares its valid security profiles in
`conformance.specification_registry`. Read/Write 4.0.x derives
`FAPI1_ADVANCED`; DCR 3.4 is profile-neutral and uses the internal `all` value.
The profile remains in the compiler model for catalogue applicability, but it is
not a participant choice when the selected version declares only one value.
Token endpoint client authentication (`private_key_jwt` or `tls_client_auth`)
is configured separately as part of the security environment.

Canonical sections such as `securityEnvironment`, `businessTestData`, and
runtime `inputs` derive exact runtime inputs like `resourceBaseUrl`,
`consentedAccountId`, and debtor account fields so the browser does not duplicate
them as a separate runtime-input step. For Read/Write the browser collects
values in this order: specification, resource groups/endpoints/capabilities,
connection and security (discovery URL, OAuth/FAPI/security details), and
business test data. Scope comes before security because the selected tests
decide which security values are needed to run. Discovery metadata can prefill
empty OAuth fields, but only values saved on the security page become part of
the exported plan JSON. Runtime inputs remain supported by canonical
plans submitted through import, REST, and CLI execution; the compiler's
traceability snapshot records only that sensitive values were provided.

Keep capability IDs stable and domain-oriented, for example
`ais.transactions.date-range-filtering`, rather than generated test-case IDs.
The same endpoint-scoped `capabilities` contract is parsed by the UI, CLI, and
REST API. Required capabilities are catalogue-owned baseline coverage and are
selected automatically for implemented endpoints. Optional capabilities must be
listed under the matching endpoint context and must not be modelled as generated
test-case selections.

Browser safe exports preserve endpoint/capability scope and non-sensitive
runtime references, but write secret-bearing strings as `""`. Imported inline
secrets stay in the active Django-session draft for review/launch, are masked in
rendered summaries, and are included in downloaded JSON only through the
explicit export-with-secrets action.

The CLI accepts canonical test plans through `--test-plan path/to/test-plan.json`.
The REST API accepts the same document as the request body or under `testPlan`.
The browser builder generates the same canonical document from selected
specification, security environment, scope, and business data.

## Removed public surfaces

The following surfaces are intentionally not supported:

| Removed surface | Replacement |
| --- | --- |
| Checked-in participant config examples | Browser plan builder and user-supplied config files. |
| `config.testSuite` | Canonical JSON-first `resourceGroups` plus endpoint capability selection. |
| CLI `--manifest`, `--deselect`, and `--plan-spec` for participant runs | CLI `--test-plan`. |
| REST `manifest`, `planSpec`, and `deselectStepIds` | REST canonical body or `testPlan`. |
| Browser `/plan/` single-page builder | Session-backed `/builder/...` wizard and `/builder/import/`. |
| Legacy bundled suite JSON resources | Python catalogue modules under `conformance/catalogues/`. |

The internal manifest parser and executor remain because compiled plans are
lowered into an internal manifest facade while the hardened HTTP execution,
masking, signing, PSU authorisation, logging, and evidence paths are reused.
Do not re-expose those internals as participant configuration.

## Browser plan builder

The browser root menu at `/` exposes the multi-step builder and canonical JSON
import flow. The legacy single-page `/plan/` builder is no longer mounted.

The Read/Write wizard follows this order:

1. POST `/builder/new/` to create a session-backed draft.
2. Select scheme, specification, and version at `/builder/<draft>/catalogue/`.
   The registry derives the matching security profile. Registered future
   boundaries without an executable catalogue render a generic blocked state.
3. Select scope at `/builder/<draft>/scope/`. Read/Write uses resource groups,
   endpoints, and optional capabilities. DCR shows direct POST/GET/PUT/DELETE
   operations with POST locked and management methods optional. The
   server-rendered fragment at
   `/builder/<draft>/scope/options/` shows endpoints from the selected AIS, PIS,
   CBPII, or VRP groups and reveals capabilities only for selected endpoints.
   `conformance/endpoint_requirements.py` codifies the
   [reviewed specification matrix](READ_WRITE_ENDPOINT_REQUIREMENTS.md).
   Mandatory covered endpoints are locked within selected groups, and a selected
   resource POST locks its mandatory dependent endpoints. The server restores
   these selections even if checkbox values are omitted. Conditional/Optional
   endpoints remain selectable; unknown catalogue-only paths are not inferred
   to be Optional. This is separate from required capabilities for an implemented
   endpoint and does not change the raw JSON compiler's declared-scope contract.
   Ticking a group (or **Select all endpoints and features**) sends
   `expand_resource_group`, and ticking an endpoint sends `expand_endpoint`, on
   the fragment refresh; `scope_selection_defaults()` then adds every endpoint
   and optional feature for those items only. An empty scope, or a group with
   no endpoint (`resource_groups_without_endpoints()`), is saved and reported
   as a scope issue at review; `/config/` then shows no fields and links to
   scope.
4. Enter connection and security settings at `/builder/<draft>/config/security/`:
   the `.well-known/openid-configuration` URL, OAuth/FAPI signing, mTLS, and
   the resource-server base URL. For Read/Write, `/config/discovery/` redirects
   here. Saving fetches discovery metadata again only when the URL changed or
   the previous fetch failed, stores non-secret helper metadata on the draft,
   and fills empty OAuth endpoint, response type and algorithm fields from it.
   The inline **Fetch and fill** button posts to
   `/builder/<draft>/config/discovery/preview/`, which validates and fetches
   without saving and fills empty OAuth inputs with HTMX out-of-band swaps.
   It summarises what was filled and kept, tags each field holding the
   discovery value **From discovery** (the tag clears when the field is
   edited), and lists typed values that differ from discovery. Typed values
   are only replaced when the participant chooses **Replace with discovery
   values**, which reposts with `overwrite=true`.
   Field badges come from `security_field_requirements()` in
   `builder_wizard.py`, which uses `compiled_plan_run_config_requirements()`
   (`conformance/run_config_requirements.py`) on the draft's compiled scope:
   **Required to run** (with the reason), **Optional**, or **Depends on
   scope** when no endpoints are selected. The mTLS certificate and key are
   required to run only when the token endpoint auth method is
   `tls_client_auth`. DCR keeps its separate discovery page before security, whose
   **Preview discovery** button shows the metadata without filling anything.
5. Enter business/request defaults at `/builder/<draft>/config/`. DCR skips this
   page. AIS, PIS,
   CBPII, and VRP fields render only when selected endpoints need that domain.
   Known account, amount, date, and frequency shapes use friendly fields with
   advanced JSON fallbacks.
6. Review the generated plan at `/builder/<draft>/review/`, including summary
   counts, import warnings, launch blockers, the unmasked, editable plan JSON
   (the single view of the plan: launch, export, and the step-bar buttons all
   submit it and apply it to the draft first, with no separate save), and
   collapsed generated-test rows.
7. Download safe JSON from `/builder/<draft>/export.json`, explicitly request
   local secret-bearing JSON with a POST `include_secrets=1`, or launch through
   `/builder/<draft>/launch/`.

Every step page and review render a step bar from
`conformance/api/builder_steps.py`, the single definition of both flow orders
(Read/Write: specification, scope, connection & security, business data, review;
direct endpoint/DCR: specification, scope, discovery, security, review). A
supported specification is the only navigation gate: until one is chosen every
later step (and typed URL) redirects to `/catalogue/`; afterwards every step is
open in any order. Step-bar buttons and **Back** submit the page form with
`next=<step id>` (or `next=back`), redirected via `resolve_next`, which accepts
only fixed step ids; anything else falls back to the following step.
Every builder page uses the same `main` width (`min(1180px, calc(100% - 32px))`)
and header, and the step pills have one fixed height in every state, so the
bar never moves between steps; a divider sets **Review** apart from the editing
steps. When scope has issues, review reports them instead of the raw plan
validation error they cause.

Leaving a step always saves it. Step views bind their form leniently
(`_lenient_bind` in `ui_views.py`, `lenient=True` on the config forms): required
and whole-group checks are skipped, a non-secret field that fails format
validation is reverted to its saved value and its raw text is kept in the
draft's `invalid_field_values[step]`, then shown back with its error on the
next visit. Pasted or uploaded credential material that fails validation is
dropped, never stored or echoed, and reported with a Django `messages`
warning. `conformance/api/builder_step_status.py` replays the saved values
(plus retained invalid values) through the strict forms to produce per-step
issues, and derives the step bar's `complete`/`attention`/`not_started`
progress from them. For Read/Write, the connection and security step also
reports each value the selected scope needs to run but that is missing
(`draft_run_config_requirements` and `_run_config_issues`); the same check runs
in shared plan validation (`_run_config_issues` in
`conformance/test_plan_validation.py`), so imported, REST and CLI plans are held
to it too. Review lists those issues per step with a **Fix** button
and treats them as launch blockers, so lenient saving never relaxes launch
validation. The review JSON jump still renders a discard `<dialog>` when the
submitted plan JSON is not a JSON object.

Changing the specification on `/catalogue/` when it would switch flow or prune
saved scope re-renders with the affected items and a confirmation token
(`confirm_specification_change`); the change is saved only when the token
matches the newly chosen boundary.

Imported plans enter through `/builder/import/` (pasted JSON or an uploaded
`.json` file, up to 1 MB) and go straight to the same review page, or to the specification step (with the
import warnings and **Start a new plan instead**) when no supported
specification was loaded. Import is
lenient (`conformance/api/plan_import_recovery.py`): only empty input, invalid
JSON, or a non-object root is rejected. Every other document is recovered field
by field against the schemaVersion `1.0` shape. Values the builder can represent
load into the draft; missing, invalid, unrecognised, or unresolvable values
(including individual scope items) are recorded as import warnings and kept
verbatim in the draft's unrepresented-field overlay rather than being replaced
with defaults. The plan JSON composed from the builder and that overlay is the
single source of truth for review, export, and launch: while the overlay is
non-empty, review runs normal load validation on the composed JSON and launch
uses `prepare_test_plan_for_run` on it, so lenient import never relaxes launch
validation. Saving a builder step takes ownership of the overlay fields that
step produces and clears their warnings. The review JSON editor reloads the
draft with the same recovery rules. Because it shows secrets unmasked, the
review page is served with `Cache-Control: no-store`.

Generated tests are always read-only. Scope changes happen by editing resource
groups, endpoints, and capabilities; the review page must not expose generated
test-case checkboxes or deselection controls.

Browser posts remain Django-form mediated and CSRF-protected. Run detail and log
downloads continue to use the same masking boundary as CLI/API execution. Run
detail surfaces the top-level catalogue evidence summary from the completed
result: selected endpoints, selected capabilities, generated test-case counts,
and non-certifying reasons.

### Dynamic pages (HTMX)

The browser UI is server-rendered Django templates enhanced with
[HTMX](https://htmx.org/); there is no single-page app or JavaScript build step.
Every page except the OAuth callback includes
`conformance/partials/htmx_script.html`, which loads the vendored bundle,
sends Django's CSRF token on HTMX requests, disables HTMX history snapshots
(builder pages can render credential references), and swaps `4xx`/`5xx`
response bodies so `400` validation re-renders appear in place.

- **Run detail** panels poll their partial routes every 2 seconds while the run
  is `pending` or `running`. Terminal states render no `hx-trigger`, so polling
  stops. The terminal status poll sends `HX-Trigger: run-finished` and the other
  panels refresh once more on that event. A `<noscript>` meta refresh keeps the
  page live without JavaScript.
- **Builder steps** set `hx-boost="true"` on `<main>`, so wizard links and form
  posts swap the page body instead of reloading it. The `head-support`
  extension merges each page's `<head>` styles. Export downloads, export with
  secrets, launch and links to run pages opt out with `hx-boost="false"`.
  Inline page scripts must stay idempotent IIFEs because boosted swaps re-run them.
- **Builder dropdowns** are native `<select class="builder-select">` elements
  styled by `conformance/partials/builder_select_styles.html`, included in the
  page `<head>`. Every browser gets the restyled closed control. Browsers that
  support `appearance: base-select` also get a styled option list; others keep
  the native list. There is no dropdown JavaScript, so scripts that hide or
  disable options keep working.
- **Scope** refreshes `#scope-options` through `hx-post` to
  `/builder/<draft>/scope/options/`; the bulk select buttons fire a
  `scope-refresh` event.

Static assets are served by [WhiteNoise](https://whitenoise.readthedocs.io/)
because uvicorn has no static file handler. The Docker build runs
`collectstatic`; local runs and tests fall back to the static finders
(`WHITENOISE_USE_FINDERS`). Vendored files live in
`conformance/api/static/conformance/vendor/` with their versions in the
filenames. The README there records source, licence and SHA-256 and explains
how to upgrade.

## Certification validation

`conformance.certification_cli` is an internal reviewer tool. It validates a
submitted result report against the manifest representation used for the
original run and an independently supplied approved-release policy.

```bash
uv run python -m conformance.certification_cli out/test-results.json \
  --manifest path/to/internal-manifest.json \
  --approved-releases path/to/approved-releases.json
```

The approved-release policy shape is:

```json
{
  "schemaVersion": "v1",
  "approvedToolVersions": ["OBL-APPROVED-RELEASE-VERSION"]
}
```

Participant config may include `approvedReleasePolicyPath` for advisory
self-assessment in generated reports. OBL-side validation remains authoritative
and recomputes approved-release status from independently supplied inputs.

For Phase 1, validator authority is limited to consistency and
certification-readiness. It must derive expected mandatory coverage and approved
release status from OBL-controlled inputs and must not trust an eligibility
assessment embedded in the submitted report. A passing validation does not
cryptographically authenticate a report produced in a participant-controlled
container.

Tamper-resistant evidence is a Phase 2 portal concern. Portal-managed runs must
bind a server-validated plan to its results and retain trusted provenance and an
immutable audit history. Uploaded local reports retain the Phase 1 assurance
level unless an independently trustworthy attestation can be verified.

## CI pipeline

GitHub Actions run independent jobs in parallel. `Check` invokes the
canonical `make check` gate with the local OpenAPI exclusion cleared, so it
runs ruff, mypy, the complete offline `unit`/`component` suite with coverage,
and a full tracked-file secret scan. `Docker Build` builds the image, starts a
container, and probes `/health/`. `Image (linux/amd64|arm64)` builds each
platform image and scans it; the aggregate `Vulnerability Scan` check fails on
any fixable vulnerability and on unfixed critical/high findings. Open the job
summary for the full report.

To resolve a failing `Vulnerability Scan`, upgrade the affected package
(`uv lock --upgrade-package <name>`) or base image. If no fix is possible and
the risk is accepted, add a reviewed entry to
`security/vulnerability-exceptions.toml` (maximum 90 days; see
[CI/CD Strategy §3.2](CICD_STRATEGY.md#32-vulnerability-scanning-and-the-pr-gate)).
You can run the policy locally against scanner output with
`uv run python -m scripts.vulnerability_gate --help`.

Participant-facing preview, beta, RC, and GA images add a stricter release
gate. The workflow builds an immutable candidate image, runs the full checks
against that exact digest, scans the final runtime image with Snyk, generates
SBOM/provenance, records a candidate manifest, and promotes the already-checked
digest only after the appropriate GitHub Environment approval. The version/tag
to publish is set in `[project].version` in `pyproject.toml` and reviewed in
the PR; it is not typed into the workflow approval screen.

There is no live-network or end-to-end workflow. Container startup and health
checking validate packaging only; they are not an Ozone or conformance-system
test.
