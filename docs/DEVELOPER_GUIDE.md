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
| `CONFORMANCE_DEVELOPER_MODE` | No | Disables masking for local engine debugging only. |
| `CONFORMANCE_TOOL_VERSION` | No | Overrides generated report `tool.version`. |

`make docker` requires production-style configuration and fails fast when
misconfigured. Local source runs fall back to the project version in
`pyproject.toml`, then `0+unknown` if no version can be resolved.

## Catalogue architecture

The participant-facing source of truth is now a catalogue-backed plan spec, not
checked-in manifest examples or config-selected suites.

Core modules:

| Module | Role |
| --- | --- |
| `conformance.catalogue` | Domain model, plan-spec parser, compiler, applicability decisions, traceability model. |
| `conformance.catalogue_registry` | Registry of bundled catalogues available to CLI, API, and UI. |
| `conformance.catalogues.*` | Legacy FCS-derived catalogues for AIS, PIS, CBPII, VRP, and cVRP. |
| `conformance.executor.run_compiled_test_plan` | Executes compiled catalogue plans through the existing hardened HTTP/PSU/signing engine. |
| `conformance.results` | Serializes catalogue traceability and certification reasons in result JSON. |

The compiler selects test cases by catalogue key, security profile, and exact
implemented endpoint operations. It includes dependencies automatically, rejects
mandatory applicable deselection, snapshots runtime inputs without sensitive
values, and marks assertion overrides as non-certifying.

## Plan-spec contract

Plan specs use schema version `v1`:

```json
{
  "schemaVersion": "v1",
  "catalogue": {
    "standard": "open-banking",
    "version": "v4.0",
    "api": "ais"
  },
  "securityProfile": "fapi1-advanced",
  "implementedEndpoints": [
    {
      "method": "GET",
      "path": "/open-banking/v4.0/aisp/accounts",
      "resourceGroup": "Accounts",
      "operationId": "GetAccounts"
    }
  ],
  "runtimeInputs": {
    "resourceBaseUrl": "https://resource.example.com"
  },
  "assertionOverrides": []
}
```

Sensitive runtime values may be supplied to execution, but the compiler's
traceability snapshot records only that they were provided.

The CLI accepts `config.json --plan-spec plan-spec.json`. The REST API accepts
`{"config": {...}, "planSpec": {...}}`. The browser plan builder generates the
same shape from implemented endpoint selections and runtime prompts.

## Removed public surfaces

The following surfaces are intentionally not supported:

| Removed surface | Replacement |
| --- | --- |
| Checked-in participant config examples | Browser plan builder and user-supplied config files. |
| `config.testSuite` | `planSpec.catalogue` plus implemented endpoint selection. |
| CLI `--manifest` and `--deselect` for participant runs | CLI `--plan-spec`. |
| REST `manifest` and `deselectStepIds` | REST `planSpec`. |
| Legacy bundled suite JSON resources | Python catalogue modules under `conformance/catalogues/`. |

The internal manifest parser and executor remain because compiled plans are
lowered into an internal manifest facade while the hardened HTTP execution,
masking, signing, PSU authorisation, logging, and evidence paths are reused.
Do not re-expose those internals as participant configuration.

## Browser plan builder

The `/plan/` flow keeps the guided UX:

1. Select standard, version, API family, and security profile.
2. Select implemented endpoints grouped by resource group.
3. Provide endpoint-derived runtime inputs.
4. Preview generated test counts, certification status, and runtime prompts.
5. Expand audit details only when generated test IDs or applicability decisions
   are needed.
6. Launch through the shared run lifecycle.

Browser posts remain Django-form mediated and CSRF-protected. Run detail and log
downloads continue to use the same masking boundary as CLI/API execution.

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
