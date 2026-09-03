# Testing Strategy

## Framework recommendation

Use `pytest` with `pytest-django`. It supports fast unit tests, Django view/form
tests, CLI result-file assertions, and selective live-network tiers.

## Test categories

| Category | Marker | Scope |
| --- | --- | --- |
| Unit | `unit` | Parser/compiler logic, result serialization, CLI/config validation, isolated helpers. |
| Integration | `integration` | Django client/API/UI flows and offline executor integration with mocked HTTP. |
| Ozone | `ozone` | Live-network Ozone checks gated by environment variables. |
| E2E | `e2e` | Container-level runs that assert on structured result files. |

`make test` excludes live Ozone and E2E by default. Live Ozone tests live under
`tests/integration/` and skip cleanly when their tier-specific environment
variables are absent.

## Result-file assertion pattern

E2E and CLI-facing tests must assert on structured result files, not incidental
side effects. Result JSON assertions should cover:

- `summary` totals and statuses.
- `catalogue` traceability for compiled plan runs.
- Selected endpoint capabilities and applicability decisions.
- `certificationEligibility` reasons.
- Masked request, response, token, signing, and PSU evidence.

## Catalogue regression coverage

The catalogue model is the participant-facing contract. Keep focused coverage
for:

- Shared plan-document parsing and unknown-field rejection for v1 compatibility
  specs and v2 browser/API/CLI documents.
- Duplicate catalogue/test/request/assertion ID detection.
- Applicability filtering by catalogue key or v2 boundary, profile,
  implemented endpoint, and selected endpoint capabilities.
- Required capability defaulting, optional capability inclusion/exclusion, and
  invalid capability rejection.
- Dependency inclusion and deterministic ordering.
- Runtime input requirement validation and sensitive-value snapshots.
- Assertion override non-certifying behaviour.
- Bundled catalogue registry coverage for AIS, PIS, CBPII, VRP, and DCR 3.4, with
  retained cVRP catalogue code covered outside the participant-facing registry.
- Aggregate v2 Read/Write compilation across AIS, PIS, CBPII, and VRP catalogue
  areas, with cVRP rejected from the Open Banking UK boundary.
- Legacy FCS provenance in compliance-scope traceability.

Primary tests:

```bash
DJANGO_DEBUG=true uv run pytest \
  tests/test_catalogue.py \
  tests/test_catalogue_ais.py \
  tests/test_catalogue_pis.py \
  tests/test_catalogue_cbpii.py \
  tests/test_catalogue_vrp.py \
  tests/test_catalogue_registry.py \
  -m unit -v
```

## Execution/API/CLI/UI compiled-plan coverage

Compiled catalogue plans execute through the existing hardened executor path.
Regression coverage should prove that replacing public manifests did not weaken:

- HTTP execution, status/header/body assertions, and response-schema assertions.
- PSU authorisation handoff and headless test helpers.
- FAPI signing, token endpoint auth policy, detached JWS signing, and mTLS checks.
- Masking in result JSON, NDJSON logs, browser downloads, and API log snapshots.
- CLI `--test-plan` validation and rejection of public `--manifest` and
  `--plan-spec`.
- REST canonical test-plan validation and rejection of public `manifest`,
  `planSpec`, and `deselectStepIds`.
- Browser main menu, session-backed draft creation, scheme/specification/version
  selection, security-environment capture before resource groups,
  endpoint/feature drill-down for selected groups, server-rendered dynamic
  feature filtering, locked required capabilities, unchecked optional
  capabilities, grouped business data, generated runtime artifact prompts,
  import/review, safe export, explicit export-with-secrets, launch, and
  collapsed read-only generated-test rows.
- REST and CLI parity for the same capability-selected schemaVersion `1.0`
  contract.
- Run-detail rendering of catalogue traceability evidence from completed result
  JSON.
- DCR CLI, local REST, browser import/review/launch, persisted run lifecycle,
  scenario/case/step statuses, optional-operation skips, safe evidence, and
  certification eligibility.

Focused run:

```bash
DJANGO_DEBUG=true uv run pytest \
  tests/test_catalogue_integration.py \
  tests/test_executor.py \
  tests/test_results.py \
  tests/test_cli.py \
  tests/test_api.py \
  tests/test_builder_wizard.py \
  tests/test_ui_views.py \
  -m "unit or integration" -v
```

## Certification validator coverage

The OBL-side validator is an internal-tool surface. It intentionally still
accepts the manifest representation used for the original run and an independent
approved-release policy.

Focused run:

```bash
DJANGO_DEBUG=true uv run pytest \
  tests/test_results.py \
  tests/test_version.py \
  tests/test_approved_releases.py \
  tests/test_model_bank_config.py \
  tests/test_certification_validator.py \
  tests/test_certification_cli.py \
  -v
```

Coverage must include approved versions, unapproved versions, absent policies,
mandatory passed/warn acceptance, mandatory failed/skipped/missing rejection,
malformed report rejection, and Confluence summary rendering.

## DCR live verification gate

The deterministic mTLS service covers DCR protocol and result parsing offline.
Run:

```bash
uv run pytest tests/test_dcr_product_integration.py tests/test_dcr_execution.py -v
uv run python -m conformance.result_gate out/test-results.json
```

The result gate reads JSON only and requires a passing aggregate, zero reported
failed steps, and no failed scenario/case/step in `catalogue.traceGroups`.
Expected endpoint-not-selected skips are permitted. The Ozone workflow exposes
an opt-in `run_dcr_34` dispatch input and applies this same gate to the generated
DCR result; it requires repository-configured mTLS/signing/SSA secrets and must
not be described as run unless that job actually executed.

## Code quality and coverage targets

| Tool | Role |
| --- | --- |
| `ruff` | Linting, import sorting, and formatting checks. |
| `mypy` | Strict static type checking. |
| `pytest-cov` | Coverage reporting and 80% minimum enforcement. |
| `detect-secrets` | Secret scanning through the pre-commit hook and CI. |

Run selectively while iterating, then run `make check` before pushing.
