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

The accepted target boundaries and staged replacement sequence are recorded in
the [Test Plan Architecture Migration Guide](TEST_PLAN_ARCHITECTURE_MIGRATION_GUIDE.md).
The participant-facing source of truth is the schema-owned `participant-plan`
document. `conformance.configuration_contracts` validates it, resolves it
against the coordinator-owned suite release and manually reviewed executable
test catalogues, and generates a deterministic execution manifest. Browser, CLI,
REST, import, export, and review all use this boundary.

Files named `test-catalogue.v2.json` are manually authored and reviewed release
artefacts. Do not generate them from specification text, reconstruct a shadow
requirements/rules catalogue, or treat comparison scripts as production
authority. Specification material remains human design and review input only.

The following private runtime compatibility remains intentionally:

| Module | Current consumer and retention reason |
| --- | --- |
| `conformance.catalogue`, `conformance.catalogue_registry`, and `conformance.catalogues.*` | Deletion-only v1 catalogue implementation retained for its focused legacy tests. No participant compile, review, or launch path consumes it. |
| `conformance.test_plan_validation` | Deletion-only v1 plan validation retained for focused legacy tests. No participant launch path consumes it. |
| `conformance.manifest`, `conformance.test_plan`, and `conformance.execution_schedule` | Private HTTP/OAuth/PSU execution primitives. The executor lowers only immutable v2 manifest instructions into these runtime types. |
| `conformance.dcr_execution` | Executes DCR protocol exchanges declared by immutable v2 manifest steps. |
| `conformance.executor.run_compiled_test_plan` | Narrow deletion-only harness used by focused legacy runtime and golden tests. Public launch surfaces call `run_execution_manifest`. |
| `tests/fixtures/current_pipeline` and pinned parity ledgers | Comparison evidence proving that compatibility execution retains the characterised support matrix; they are not accepted participant documents. |

Production work selection, ordering, requests, assertions, outputs, and evidence
come exclusively from the released v2 catalogue and immutable execution
manifest; runtime lowering may not consult a compiled legacy catalogue graph.
There is no machine-readable normative catalogue or requirement-level
assessment contract.

Generated v2 manifest step IDs preserve the catalogue's dotted stable identity
and append `.instance.request`; runtime `${steps.<id>...}` resolution must treat
that complete value as the step ID. Every cross-step request reference must
target an earlier producer in the test's dependency closure. A consent request's
`psuAuthorization` declares the owner of nested authorization/token helpers,
while downstream requests name that helper through
`requiredPsuAuthorizationStepId`. Lowering emits those helpers only when such a
consumer is selected, so standalone positive and negative request-validation
tests must not carry unused PSU authorization metadata.

## Shared plan-document contract

The participant-facing contract is `participant-plan` schema version `2.0`:

```json
{
  "documentType": "participant-plan",
  "executionConfiguration": {
    "compatibilityRuntimeInputs": {},
    "dynamicClientRegistration": {},
    "metadata": {},
    "securityEnvironment": {
      "discoveryUrl": "https://aspsp.example.com/.well-known/openid-configuration",
      "resourceBaseUrl": "https://resource.example.com"
    }
  },
  "id": "participant.ais.example",
  "predefinedInputs": [],
  "schemaVersion": "2.0",
  "scheme": "open-banking-uk",
  "securityProfile": "fapi1-advanced",
  "selectedCapabilityIds": ["ais.v401.capability.accounts"],
  "specification": {
    "id": "read-write-api",
    "testScope": "ais",
    "version": "4.0.1"
  },
  "suiteReleaseId": "obl.open-banking-mvp.test-catalogue-release"
}
```

One plan selects one test scope. The manually authored v2 catalogue directly
owns scheme/specification identity, capabilities, endpoints and pinned
technical sources, predefined input metadata, test applicability and
dependencies, request construction, assertions, outputs, and request/response
evidence policy. Compilation infers capabilities, endpoints, tests, and
dependencies from that single executable authority. `executionConfiguration`
carries environment values needed to launch
without changing the deterministic resolved-plan identity.

`compatibilityRuntimeInputs` is limited to environment, credential references,
captured protocol-session state, and runtime-generated values needed by the
temporary executor bridge. It is not a business-data extension point. PIS
creditor accounts, amounts, currencies, dates, and frequencies are
catalogue-owned predefined inputs, and public surfaces reject their legacy
runtime aliases. The bridge may lower resolved logical values to legacy names
in memory, but those aliases never become participant-plan authority.

Non-sensitive predefined values participate in content-addressed resolved-plan
and execution-manifest identity. Sensitive values are represented only by their
stable input ID, source, applicability, and redaction state; no unkeyed value
hash is published. Sensitive request-evidence paths are derived from trusted
test-definition bindings and remain masked even when developer-mode evidence
would otherwise be unmasked.

Browser safe exports omit sensitive predefined values and compatibility runtime
values. The CLI accepts the document through `--test-plan`; REST accepts it
directly or under `testPlan`.

## Removed public surfaces

The following surfaces are intentionally not supported:

| Removed surface | Replacement |
| --- | --- |
| Checked-in participant config examples | Browser plan builder and user-supplied config files. |
| `config.testSuite` | `participant-plan.selectedCapabilityIds`. |
| CLI `--manifest`, `--deselect`, and `--plan-spec` for participant runs | CLI `--test-plan`. |
| REST `manifest`, `planSpec`, and `deselectStepIds` | REST participant-plan body or `testPlan`. |
| Browser `/plan/` single-page builder | Session-backed `/builder/...` wizard and `/builder/import/`. |
| Legacy bundled suite JSON resources | Python catalogue modules under `conformance/catalogues/`. |
| Legacy canonical plans with `resourceGroups`, `endpoints`, and `businessTestData` | `participant-plan` test scope, capabilities, and predefined inputs. |

The internal legacy parser is used only for generated compatibility documents,
and the manifest parser/executor remains an internal runtime implementation.
Do not re-expose either as participant configuration.

## Browser plan builder

The browser root menu at `/` exposes the multi-step builder and canonical JSON
import flow. The legacy single-page `/plan/` builder is no longer mounted.

The wizard follows the PRD order:

1. POST `/builder/new/` to create a session-backed draft.
2. Select scheme, specification, and version at `/builder/<draft>/catalogue/`.
   The registry derives the matching security profile. Registered future
   boundaries without an executable catalogue render a generic blocked state.
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
   advanced JSON fallbacks. PIS values collected here are serialized as
   versioned catalogue predefined inputs, never compatibility runtime inputs.
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

Imported schemaVersion `2.0` plans enter through `/builder/import/` and go
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
submitted result report against an independently supplied, approved
schema-version 2.0 suite release and the generated artefacts used for the run.

```bash
uv run python -m conformance.certification_cli out/test-results.json \
  --suite-release conformance/configuration_contracts/bundles/open-banking-mvp/suite-release.json \
  --resolved-plan path/to/resolved-plan.json \
  --manifest path/to/execution-manifest.json \
  --trusted-root .
```

Participant config may include `approvedReleasePolicyPath` for advisory
self-assessment in generated reports, but that legacy claim is not validator
authority. The validator resolves and digest-checks the release-bound executable
catalogue, v2 schemas, technical sources, and suite policy. It reconstructs the
participant's declared scope from the masked result snapshot, independently
re-resolves applicable tests, regenerates the expected manifest, verifies every
identity and provenance link, and compares manifest assertions with result
observations.

The output reports separate individual approved-test outcomes, overall
automated assessment, and certification eligibility. It does not load a
requirements catalogue, calculate requirement-level coverage, or handle
documented-only/manual-evidence obligations. A passing validation does not
cryptographically authenticate a report produced in a participant-controlled
container.

Tamper-resistant evidence is a Phase 2 portal concern. Portal-managed runs must
bind a server-validated plan to its results and retain trusted provenance and an
immutable audit history. Uploaded local reports retain the Phase 1 assurance
level unless an independently trustworthy attestation can be verified.

## CI pipeline

GitHub Actions run two independent jobs in parallel. `Check` invokes the
canonical `make check` gate with the local OpenAPI exclusion cleared, so it
runs ruff, mypy, the complete offline `unit`/`component` suite with coverage,
and a full tracked-file secret scan. `Docker Build` builds the image, starts a
container, and probes `/health/`.

There is no live-network or end-to-end workflow. Container startup and health
checking validate packaging only; they are not an Ozone or conformance-system
test.
