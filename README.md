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
5. For Read/Write, select compatible resource groups and implemented endpoints.
   For Dynamic Client Registration 3.4, select direct endpoints: POST is always
   selected and locked; GET, PUT, and DELETE are optional.
6. Confirm endpoint capabilities. DCR token traffic is generated and is never a
   participant-selected endpoint.
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

The CLI accepts a `participant-plan` 1.0 document containing participant scope,
predefined inputs, and local execution configuration:

```bash
uv run python main.py --test-plan path/to/test-plan.json
```

```json
{
  "documentType": "participant-plan",
  "executionConfiguration": {
    "compatibilityRuntimeInputs": {
      "pisCreditorAccountIdentification": "08080021325698",
      "pisCreditorAccountName": "Merchant",
      "pisCreditorAccountSchemeName": "UK.OBIE.SortCodeAccountNumber",
      "pisFirstPaymentDateTime": "2026-10-01T00:00:00Z",
      "pisInstructedAmountAmount": "10.00",
      "pisInstructedAmountCurrency": "GBP"
    },
    "dynamicClientRegistration": {},
    "metadata": {"aspspName": "Example Bank"},
    "securityEnvironment": {
      "clientId": "client-123",
      "discoveryUrl": "https://aspsp.example.com/.well-known/openid-configuration",
      "redirectUri": "https://client.example.com/callback",
      "resourceBaseUrl": "https://resource.example.com"
    }
  },
  "id": "participant.pis-v401.example",
  "predefinedInputs": [
    {
      "inputId": "pis.v401.input.standing-order-frequency",
      "value": {"frequencyType": "WEEK", "pointInTime": "03"}
    }
  ],
  "schemaVersion": "1.0",
  "scheme": "open-banking-uk",
  "securityProfile": "fapi1-advanced",
  "selectedCapabilityIds": [
    "pis.v401.capability.domestic-standing-order"
  ],
  "specification": {
    "id": "read-write-api",
    "requirementsScope": "pis",
    "version": "4.0.1"
  },
  "suiteReleaseId": "obl.open-banking-mvp.catalogue-release"
}
```

Each plan selects one requirements scope and its participant-facing
capabilities. Trusted release catalogues infer required capabilities, endpoints,
tests, and dependencies. `compatibilityRuntimeInputs` is restricted execution
configuration for values still consumed by the current hardened runtime; it
does not affect deterministic plan resolution.

DCR plans select specification `dynamic-client-registration`, requirements
scope `dcr`, and DCR capabilities. See
[`docs/DCR_3_4_PARITY_CONTRACT.md`](docs/DCR_3_4_PARITY_CONTRACT.md) for the
supported auth methods and operator workflow. Legacy canonical
`schemaVersion: "1.0"` plans are no longer accepted by browser import, CLI, or
REST.

## Browser and REST launch

The browser wizard imports, exports, reviews, and launches the same
`participant-plan` document accepted through CLI and REST. `POST /api/runs/`
accepts the document directly or under `testPlan`.

Browser exports are secret-safe by default: selected capabilities and
non-sensitive predefined inputs are retained, while sensitive predefined inputs
and compatibility runtime values are omitted or redacted. A separate
export-with-secrets action is available for local power-user workflows. DCR
accepts credential file references only, never inline SSA, PEM, assertion,
secret, or token material.

Run detail, result downloads, and NDJSON execution logs keep the existing
masking and evidence behaviour. Result JSON includes the safe test-plan snapshot,
the shared validation outcome, catalogue traceability for selected endpoints,
selected capabilities, generated test-case IDs, applicability decisions, runtime
input snapshots with sensitive values omitted, and non-certifying reasons. Manual
PSU authorisation handoff URLs remain transient browser state; persisted
artifacts mask credentials, tokens, request objects, client assertions, detached
JWS values, and sensitive headers.

DCR results additionally contain ordered scenario → case → step trace groups.
Unselected optional operations are explicit `skipped` cases/steps with
`endpoint-not-selected`; prerequisite failures skip dependent runtime steps and
fail the aggregate run. Automation must use
`python -m conformance.result_gate out/test-results.json`, which rejects any
failed structured case or step regardless of console transcript text.

## Bundled catalogues

The bundled catalogue registry currently covers the legacy FCS baseline for:

| Standard | Version | API family |
| --- | --- | --- |
| `open-banking` | `v3.1` | `ais` |
| `open-banking` | `v3.1` | `pis` |
| `open-banking` | `v3.1` | `cbpii` |
| `open-banking` | `v3.1` | `vrp` |
| `open-banking` | `v4.0` | `ais` |
| `open-banking` | `v4.0` | `pis` |
| `open-banking` | `v4.0` | `cbpii` |
| `open-banking` | `v4.0` | `vrp` |
| `open-banking` | `v3.4` | `dcr` |

The participant-facing Read/Write versions are `3.1.11`, `4.0`, `4.0.0`, and
`4.0.1`; the internal `v3.1` key is used only to bind exact `3.1.11` plans.
Each catalogue case carries traceability back to the relevant legacy FCS
coverage in its compliance scope. Each catalogue can also define endpoint-scoped
capabilities that explain baseline and optional implementation coverage without
turning generated tests into participant selections. The hand-maintained mapping
lives in `docs/FCS_LEGACY_BENCHMARK_MAPPING.md`.

Dynamic Client Registration 3.4 is first-class across browser, CLI, and local
REST execution. It has no resource-group page or resource-server/business-data
configuration.

## Outputs and exit codes

The runner writes a structured result JSON to `resultOutputPath`, defaulting to
`out/test-results.json`, and writes an NDJSON execution log to
`executionLogPath`, defaulting to `out/execution-log.ndjson`.

CLI exit codes are:

| Code | Meaning |
| --- | --- |
| `0` | All selected checks passed. |
| `1` | Execution completed with failed checks. |
| `2` | Config, participant plan, or catalogue compilation input was invalid. |
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

### Phase 1 assurance boundary

A passing validator result means that the submitted report is consistent with
the independently supplied mandatory-test criteria and approved-release policy.
It is certification-ready evidence for OBL review, not proof that a locally
produced report is authentic and not an automated certification decision.

Participants control the Phase 1 container and filesystem, so deliberate report
tampering cannot be excluded. This is an accepted Phase 1 risk. Tamper-resistant
provenance is a Phase 2 portal responsibility and applies to runs managed within
OBL-controlled infrastructure; uploading a local report does not by itself
establish authenticity.
