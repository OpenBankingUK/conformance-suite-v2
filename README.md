# Functional Conformance Suite V2 (WIP)

This repository is used to develop the next Open Banking UK Functional
Conformance Suite before it is ready to merge back into the official
conformance-suite repository. It is not the official released suite.

## Participant workflow

Participants no longer select checked-in suites, manifests, or config examples.
The supported workflow is:

1. Open the browser main menu at `/`.
2. Choose **Create a new test plan with builder** or **Import test plan**.
3. For a new plan, select the scheme, specification, and version. The page then
   shows resource groups only when the selected specification defines them, such
   as Read/Write Account and Transaction or Payment Initiation.
4. Select implemented endpoints inside the chosen resource groups when the
   selected specification is catalogue-backed.
5. Review the endpoint capabilities shown inline on each selected endpoint card.
   Required capabilities are checked and locked; optional capabilities are
   unchecked until the participant declares that behaviour as implemented.
6. Provide config through staged pages: business/request defaults first,
   discovery URL next, OAuth/FAPI/security settings after discovery metadata is
   available, and generated runtime artifacts last. Domain-specific fields
   appear only for the selected endpoint scope.
7. Review the generated v2 plan document, export reusable JSON, or launch the
   run.

The UI shows generated tests, counts, source traceability, runtime/auth
requirements, launch blockers, and certification status after preview. Generated
tests are read-only: participants cannot select exact generated tests. Lower-level
request and assertion details stay collapsed under audit details.

## CLI plan-spec execution

The CLI accepts participant config plus a shared plan document JSON file:

```bash
uv run python main.py path/to/config.json --plan-spec path/to/plan-spec.json
```

The config carries discovery, TLS, OAuth, signing, resource-server header,
scope-relevant AIS/PIS/CBPII business defaults, conditional-property, output,
and optional approved-release-policy settings. Browser discovery can prefill
OAuth/FAPI values from OpenID metadata, but exported JSON includes the final
accepted config values without recording
whether they came from discovery or manual entry. The
preferred v2 plan document carries the scheme/specification/version boundary,
security profile, nested resource groups, implemented endpoints, selected
endpoint capabilities, grouped config defaults, and remaining runtime inputs or
references. A single Read/Write v2 plan can span AIS, PIS, CBPII, and VRP
catalogue areas. cVRP is not exposed under the Open Banking UK Read/Write
boundary for now.

```json
{
  "schemaVersion": "v2",
  "scheme": "open-banking-uk",
  "specification": "read-write",
  "version": "4.0.1",
  "securityProfile": "fapi1-advanced",
  "scope": {
    "resourceGroups": [
      {
        "id": "account-and-transaction",
        "label": "Account and Transaction",
        "endpoints": [
          {
            "method": "GET",
            "path": "/open-banking/v4.0/aisp/accounts"
          }
        ]
      }
    ]
  },
  "config": {
    "resourceServer": {"baseUrl": "https://resource.example.com"},
    "ais": {"resourceIds": {"accountIds": [{"accountId": "account-123"}]}}
  }
}
```

Required endpoint capabilities may be omitted from exported documents because
the compiler selects them automatically for implemented endpoints. Optional
capabilities must be listed under their endpoint to generate implementation-
dependent tests. The lower-level v1 per-catalogue plan spec remains accepted by
CLI/API for compatibility, but the browser import/export flow uses v2 shared
plan documents. `config.testSuite`, public `--manifest`, public `--deselect`,
REST `manifest`, and REST `deselectStepIds` are intentionally rejected.
Mandatory applicable catalogue tests cannot be arbitrarily deselected. v1
assertion overrides are import-only, recorded, and make the run non-certifying.

## Browser and REST launch

The browser wizard imports, exports, reviews, and launches the same v2 plan
document that the catalogue compiler accepts through CLI and REST. The REST run
creation endpoint accepts the document under `planSpec` in `POST /api/runs/`:

```json
{
  "config": {
    "discoveryUrl": "https://aspsp.example.com/.well-known/openid-configuration"
  },
  "planSpec": {
    "schemaVersion": "v2",
    "scheme": "open-banking-uk",
    "specification": "read-write",
    "version": "4.0.1",
    "securityProfile": "fapi1-advanced",
    "scope": {"resourceGroups": []},
    "config": {}
  }
}
```

Browser exports are secret-safe by default: the generated v2 plan document
preserves endpoint and capability scope plus non-sensitive runtime references,
but writes secret-bearing strings as empty strings. A separate
export-with-secrets action is available for local power-user workflows. Launch
still uses the sensitive values retained in the same browser session or supplied
inline by direct CLI/API submission.

Run detail, result downloads, and NDJSON execution logs keep the existing
masking and evidence behaviour. Result JSON includes catalogue traceability for
selected endpoints, selected capabilities, generated test-case IDs, applicability
decisions, runtime input snapshots with sensitive values omitted, and
non-certifying reasons. Manual PSU authorisation handoff URLs remain transient
browser state; persisted artifacts mask credentials, tokens, request objects,
client assertions, detached JWS values, and sensitive headers.

## Bundled catalogues

The bundled catalogue registry currently covers the legacy FCS baseline for:

| Standard | Version | API family |
| --- | --- | --- |
| `open-banking` | `v4.0` | `ais` |
| `open-banking` | `v4.0` | `pis` |
| `open-banking` | `v4.0` | `cbpii` |
| `open-banking` | `v4.0` | `vrp` |

Each catalogue case carries traceability back to the relevant legacy FCS
coverage in its compliance scope. Each catalogue can also define endpoint-scoped
capabilities that explain baseline and optional implementation coverage without
turning generated tests into participant selections. The hand-maintained mapping
lives in `docs/FCS_LEGACY_BENCHMARK_MAPPING.md`.

The browser catalogue selector also lists Dynamic Client Registration v3.4 as a
selector-only example. It deliberately hides Read/Write resource groups and
blocks continuation until DCR catalogue coverage is implemented.

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
