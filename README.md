# Functional Conformance Suite V2 (WIP)

This repository is used to develop the next Open Banking UK Functional
Conformance Suite before it is ready to merge back into the official
conformance-suite repository. It is not the official released suite.

## Participant workflow

Participants no longer select checked-in suites, manifests, or config examples.
The supported workflow is:

1. Open the browser plan builder at `/plan/`.
2. Select the standard, version, API family, and security profile.
3. Tick the implemented endpoints grouped by resource area.
4. Provide the runtime values required by those endpoints.
5. Preview the generated catalogue plan, then launch the run.

The UI shows generated test counts and certification status by default. The
generated tests and applicability decisions are available in the audit details,
but are hidden by default so the participant journey stays endpoint-led.

## CLI plan-spec execution

The CLI accepts participant config plus a plan-spec JSON file:

```bash
uv run python main.py path/to/config.json --plan-spec path/to/plan-spec.json
```

The config carries environment, TLS, OAuth, signing, output, and optional
approved-release-policy settings. The plan spec carries the catalogue key,
security profile, implemented endpoints, runtime input values or references,
and any assertion overrides.

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
      "resourceGroup": "Accounts"
    }
  ],
  "runtimeInputs": {
    "resourceBaseUrl": "https://resource.example.com"
  }
}
```

`config.testSuite`, public `--manifest`, public `--deselect`, REST `manifest`,
and REST `deselectStepIds` are intentionally rejected. Mandatory applicable
catalogue tests cannot be arbitrarily deselected. Assertion overrides are
recorded and make the run non-certifying.

## Browser and REST launch

The browser plan builder posts a `planSpec` and config through the same
catalogue compiler used by the CLI. The REST run creation endpoint accepts the
same model in `POST /api/runs/`:

```json
{
  "config": {
    "environment": "ozone-model-bank",
    "discoveryUrl": "https://aspsp.example.com/.well-known/openid-configuration"
  },
  "planSpec": {
    "schemaVersion": "v1",
    "catalogue": {"standard": "open-banking", "version": "v4.0", "api": "ais"},
    "securityProfile": "fapi1-advanced",
    "implementedEndpoints": [],
    "runtimeInputs": {}
  }
}
```

Run detail, result downloads, and NDJSON execution logs keep the existing
masking and evidence behaviour. Manual PSU authorisation handoff URLs remain
transient browser state; persisted artifacts mask credentials, tokens, request
objects, client assertions, detached JWS values, and sensitive headers.

## Bundled catalogues

The bundled catalogue registry currently covers the legacy FCS baseline for:

| Standard | Version | API family |
| --- | --- | --- |
| `open-banking` | `v4.0` | `ais` |
| `open-banking` | `v4.0` | `pis` |
| `open-banking` | `v4.0` | `cbpii` |
| `open-banking` | `v4.0` | `vrp` |
| `open-banking` | `v4.0` | `cvrp` |

Each catalogue case carries traceability back to the relevant legacy FCS
coverage in its compliance scope. The hand-maintained mapping lives in
`docs/FCS_LEGACY_BENCHMARK_MAPPING.md`.

## Outputs and exit codes

The runner writes a structured result JSON to `resultOutputPath`, defaulting to
`out/test-results.json`, and writes an NDJSON execution log to
`executionLogPath`, defaulting to `out/execution-log.ndjson`.

CLI exit codes are:

| Code | Meaning |
| --- | --- |
| `0` | All selected checks passed. |
| `1` | Execution completed with failed checks. |
| `2` | Config, plan-spec, or catalogue compilation input was invalid. |
| `3` | Result or execution-log output could not be written. |

Set `CONFORMANCE_DEVELOPER_MODE=true` only for local debugging. It disables
masking in developer-visible logs and must never be enabled in release builds.

## Certification report validation

The OBL-side certification validator remains an internal reviewer tool. It
validates a submitted result report against the manifest representation used for
the original run and an independently supplied approved-release policy:

```bash
uv run python -m conformance.certification_cli out/test-results.json \
  --manifest path/to/internal-manifest.json \
  --approved-releases path/to/approved-releases.json
```

Approved-release policy files use this shape:

```json
{
  "schemaVersion": "v1",
  "approvedToolVersions": ["OBL-APPROVED-RELEASE-VERSION"]
}
```

Generated reports include catalogue traceability, runtime input snapshots with
sensitive values omitted, certification/non-certification reasons, and stable
`metadata.reportVersion` plus `tool.version` fields consumed by the validator.
