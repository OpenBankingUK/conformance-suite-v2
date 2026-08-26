# Functional Conformance Suite V2 (WIP)

This repository is used to develop the next Open Banking UK Functional
Conformance Suite before it is ready to merge back into the official
conformance-suite repository. It is not the official released suite.

## Participant workflow

Participants no longer select checked-in suites, manifests, or config examples.
The supported workflow is:

1. Open the browser main menu at `/`.
2. Choose **Create a new test plan with builder** or **Import test plan**.
3. For a new plan, select the scheme, specification, and version.
4. Enter the single security environment for the plan, starting with the OpenID
   discovery URL and then confirming OAuth/FAPI, mTLS, and resource-server
   values.
5. Select one or more compatible resource groups, such as Read/Write Account and
   Transaction or Payment Initiation.
6. Select implemented endpoints inside the chosen resource groups when the
   selected specification is catalogue-backed.
7. Review the endpoint capabilities shown inline on each selected endpoint card.
   Required capabilities are checked and locked; optional capabilities are
   unchecked until the participant declares that behaviour as implemented.
8. Provide resource-group-specific business data and generated runtime artifacts.
   Domain-specific fields appear only for the selected endpoint scope.
9. Review the generated schemaVersion `1.0` test plan, export reusable JSON, or launch the
   run.

The UI shows generated tests, counts, source traceability, runtime/auth
requirements, launch blockers, and certification status after preview. Generated
tests are read-only: participants cannot select exact generated tests. Lower-level
request and assertion details stay collapsed under audit details.

## CLI plan execution

The preferred CLI path accepts a canonical JSON-first test plan that contains
the specification, security environment, resource groups, business test data,
and reporting metadata in one portable document:

```bash
uv run python main.py --test-plan path/to/test-plan.json
```

Browser discovery can prefill OAuth/FAPI values from OpenID metadata, but
exported JSON includes only the final accepted values without recording whether
they came from discovery or manual entry. A single Read/Write plan can span AIS,
PIS, CBPII, and VRP catalogue areas when those groups use one security
environment and OpenID discovery URL. cVRP is not exposed under the Open Banking
UK Read/Write boundary for now.

```json
{
  "schemaVersion": "1.0",
  "specification": {
    "family": "OBL_READ_WRITE",
    "version": "4.0.1",
    "profile": "FAPI1_ADVANCED"
  },
  "executionMode": "certification",
  "securityEnvironment": {
    "name": "Primary Authorization Server",
    "discoveryUrl": "https://aspsp.example.com/.well-known/openid-configuration",
    "clientAuthMethod": "private_key_jwt",
    "signingAlgorithm": "PS256",
    "resourceBaseUrl": "https://resource.example.com",
    "mtls": {
      "enabled": true,
      "certificatePath": "/absolute/path/to/transport-cert.pem"
    }
  },
  "resourceGroups": ["AIS"],
  "businessTestData": {
    "ais": {"accountIds": ["account-123"]},
    "inputs": {"accessToken": {"value": "token-reference-or-local-debug-value"}}
  },
  "metadata": {
    "aspspName": "Example Bank",
    "brandName": "Example Retail",
    "environmentName": "Sandbox"
  }
}
```

`resourceGroups` accepts either shorthand group names such as `"AIS"` or detailed
objects with explicit endpoint/capability selections for builder exports. Required
endpoint capabilities may be omitted because the compiler selects them
automatically for implemented endpoints. Optional capabilities must be listed
under their endpoint to generate implementation-dependent tests. Public browser,
CLI, and REST execution paths accept canonical schemaVersion `1.0` plans only.
`config.testSuite`, public `--manifest`, public `--deselect`, public
`--plan-spec`, REST `manifest`, REST `planSpec`, and REST `deselectStepIds` are
intentionally rejected. Mandatory applicable catalogue tests cannot be
arbitrarily deselected.

## Browser and REST launch

The browser wizard imports, exports, reviews, and launches the same canonical
test plan that the catalogue compiler accepts through CLI and REST. The REST run
creation endpoint accepts the canonical document directly, or under `testPlan`,
in `POST /api/runs/`:

```json
{
  "schemaVersion": "1.0",
  "specification": {"family": "OBL_READ_WRITE", "version": "4.0.1"},
  "securityEnvironment": {
    "discoveryUrl": "https://aspsp.example.com/.well-known/openid-configuration",
    "resourceBaseUrl": "https://resource.example.com"
  },
  "resourceGroups": ["AIS"],
  "businessTestData": {},
  "metadata": {}
}
```

Browser exports are secret-safe by default: the generated schemaVersion `1.0`
test plan preserves resource-group, endpoint, capability, business-data, and
non-sensitive runtime references, but writes secret-bearing strings as empty strings. A separate
export-with-secrets action is available for local power-user workflows. Launch
still uses the sensitive values retained in the same browser session or supplied
inline by direct CLI/API submission.

Run detail, result downloads, and NDJSON execution logs keep the existing
masking and evidence behaviour. Result JSON includes the safe test-plan snapshot,
the shared validation outcome, catalogue traceability for selected endpoints,
selected capabilities, generated test-case IDs, applicability decisions, runtime
input snapshots with sensitive values omitted, and non-certifying reasons. Manual
PSU authorisation handoff URLs remain transient browser state; persisted
artifacts mask credentials, tokens, request objects, client assertions, detached
JWS values, and sensitive headers.

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
| `2` | Config, canonical test plan, or catalogue compilation input was invalid. |
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
