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
| `make dev` | Django `runserver` on `0.0.0.0:8443` | Yes | Day-to-day browser development. |
| `make dev-unmasked` | Django `runserver` on `0.0.0.0:8443` | Yes | Local engine debugging with unmasked logs. |
| `make serve` | Uvicorn on `0.0.0.0:8443` | No | Local production-behaviour check. |
| `make docker` | Uvicorn in Docker | No | Production-like container run. |

All runtime entry points bind to port `8443` so callback registrations against
the legacy FCS callback URI continue to reach the local application.

`make dev-unmasked` can write credentials and tokens in clear text to
developer-visible logs. Use it only for local debugging.

## Local checks

Run `make check` before pushing. It runs secret scanning, ruff lint/format
checks, mypy strict, and pytest.

```bash
make secrets
make lint
make test
make check
```

No environment variables are needed for local checks. `settings.py` supplies a
safe `django-insecure-` fallback when `DJANGO_SECRET_KEY` is absent so tooling
can boot Django without production configuration.

## Secret scanning

The repository uses `detect-secrets` and a staged-file pre-commit hook.

```bash
uv run detect-secrets scan --exclude-files '\.env$' --exclude-files 'uv\.lock$' > .secrets.baseline
uv run detect-secrets audit .secrets.baseline
```

Only audit false positives. Move real secrets into environment variables,
untracked local files, or deployment secret stores.

## Environment variables

| Variable | Required | Purpose |
| --- | --- | --- |
| `DJANGO_SECRET_KEY` | Production only | Django signing key. |
| `DJANGO_DEBUG` | No | Enables Django debug mode when `"true"`. |
| `DJANGO_ALLOWED_HOSTS` | Production only | Comma-separated allowed hosts. |
| `DJANGO_SESSION_ENGINE` | No | Overrides the default server-side file session backend. |
| `DJANGO_SESSION_FILE_PATH` | No | Optional directory for file-backed browser session drafts. |
| `CONFORMANCE_DEVELOPER_MODE` | No | Disables masking for local engine debugging only. |
| `CONFORMANCE_TOOL_VERSION` | No | Overrides generated report `tool.version`. |

`make docker` requires production-style configuration and fails fast when
misconfigured. Local source runs fall back to the project version in
`pyproject.toml`, then `0+unknown` if no version can be resolved.

Browser wizard drafts use Django sessions. The default session backend is
server-side file storage, so `make dev` and `make dev-unmasked` do not require
running Django migrations before creating a builder draft. Do not switch to
signed-cookie sessions for the builder because imported plan documents may carry
participant-supplied secret values until launch or explicit export.

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
bundled catalogues. The current Open Banking UK Read/Write boundary maps to the
bundled Open Banking v4.0 AIS, PIS, CBPII, and VRP catalogue areas so one plan
document can span multiple resource families. cVRP is intentionally not exposed
under this Open Banking UK boundary for now. Dynamic Client Registration 3.4 is
bound to the executable `open-banking/v3.4/dcr` catalogue and uses direct
endpoint scope with no synthetic resource group.

Canonical sections such as `securityEnvironment`, `businessTestData`, and
runtime `inputs` derive exact runtime inputs like `resourceBaseUrl`,
`consentedAccountId`, and debtor account fields so the browser does not duplicate
them as endpoint runtime prompts. The browser collects values in PRD order:
specification/profile, discovery URL, OAuth/FAPI/security details, resource
groups, endpoints/capabilities, business test data, and generated runtime
artifacts. Discovery metadata can prefill security fields, but only values
accepted on the security page become part of the exported plan JSON. Sensitive
runtime values may still be supplied to browser launch, REST, or CLI execution,
but the compiler's traceability snapshot records only that they were provided.

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
specification, security environment, scope, business data, and runtime prompts.

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

The wizard follows the PRD order:

1. POST `/builder/new/` to create a session-backed draft.
2. Select scheme, specification, version, and profile at
   `/builder/<draft>/catalogue/`. Registered future boundaries without an
   executable catalogue render a generic blocked state.
3. Enter the `.well-known/openid-configuration` URL at
   `/builder/<draft>/config/discovery/`. The server attempts discovery metadata
   lookup, records non-secret helper metadata in the draft, and allows manual
   continuation when the lookup fails.
4. Enter OAuth/FAPI/security, mTLS, and resource-server settings at
   `/builder/<draft>/config/security/`. Discovery-derived values are editable
   prefilled fields; the token-endpoint-auth-method selector remains the tool's
   supported list while discovery-supported methods are shown as metadata.
5. Select scope at `/builder/<draft>/scope/`. Read/Write uses resource groups,
   endpoints, and optional capabilities. DCR shows direct POST/GET/PUT/DELETE
   operations with POST locked and management methods optional. The
   server-rendered fragment at
   `/builder/<draft>/scope/options/` shows endpoints from the selected AIS, PIS,
   CBPII, or VRP groups and reveals capabilities only for selected endpoints.
6. Enter business/request defaults at `/builder/<draft>/config/`. DCR skips this
   page. AIS, PIS,
   CBPII, and VRP fields render only when selected endpoints need that domain.
   Known account, amount, date, and frequency shapes use friendly fields with
   advanced JSON fallbacks.
7. Enter generated runtime artifacts such as tokens, token file references,
   consent ids, payment ids, and idempotency keys at
   `/builder/<draft>/config/runtime/`. DCR skips this page because token and
   client state are generated during each scenario.
8. Review the generated plan at `/builder/<draft>/review/`, including summary
   counts, masked config, launch blockers, safe export preview, and collapsed
   generated-test rows.
9. Download safe JSON from `/builder/<draft>/export.json`, explicitly request
   local secret-bearing JSON with a POST `include_secrets=1`, or launch through
   `/builder/<draft>/launch/`.

Imported schemaVersion `1.0` plans enter through `/builder/import/` and go
straight to the same review page. Missing secret-capable runtime inputs do not
block import; the review page shows launch blockers and edit links back to the
appropriate wizard steps.

Generated tests are always read-only. Scope changes happen by editing resource
groups, endpoints, and capabilities; the review page must not expose generated
test-case checkboxes or deselection controls.

Browser posts remain Django-form mediated and CSRF-protected. Run detail and log
downloads continue to use the same masking boundary as CLI/API execution. Run
detail surfaces the top-level catalogue evidence summary from the completed
result: selected endpoints, selected capabilities, generated test-case counts,
and non-certifying reasons.

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

## CI pipeline

GitHub Actions run the same checks as `make check`: ruff, mypy, pytest with
coverage, secret scanning, Docker build, and health checks. The E2E workflow
uses `tests/fixtures/e2e-default.yaml` as a placeholder path and can be pointed
at environment-specific config via `workflow_dispatch`.
