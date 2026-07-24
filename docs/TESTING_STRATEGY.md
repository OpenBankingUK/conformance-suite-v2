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
- `certificationEligibility` reasons.
- Masked request, response, token, signing, and PSU evidence.

## Catalogue regression coverage

The catalogue model is the participant-facing contract. Keep focused coverage
for:

- Plan-spec parsing and unknown-field rejection.
- Duplicate catalogue/test/request/assertion ID detection.
- Applicability filtering by catalogue key, profile, and implemented endpoint.
- Dependency inclusion and deterministic ordering.
- Runtime input requirement validation and sensitive-value snapshots.
- Assertion override non-certifying behaviour.
- Bundled catalogue registry coverage for AIS, PIS, CBPII, VRP, and cVRP.
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
- CLI `--plan-spec` validation and rejection of public `--manifest`.
- REST `planSpec` validation and rejection of public `manifest`/`deselectStepIds`.
- Browser implemented-endpoint selection, preview counts, launch, and hidden audit details.

Focused run:

```bash
DJANGO_DEBUG=true uv run pytest \
  tests/test_catalogue_integration.py \
  tests/test_executor.py \
  tests/test_results.py \
  tests/test_cli.py \
  tests/test_api.py \
  tests/test_plan_builder.py \
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

## Code quality and coverage targets

| Tool | Role |
| --- | --- |
| `ruff` | Linting, import sorting, and formatting checks. |
| `mypy` | Strict static type checking. |
| `pytest-cov` | Coverage reporting and 80% minimum enforcement. |
| `detect-secrets` | Secret scanning through the pre-commit hook and CI. |

Run selectively while iterating, then run `make check` before pushing.
