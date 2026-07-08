# Functional Conformance Suite V2 (WIP)

This forked repository will be used to manage the development of the new v2 Functional Confirmance suite before it is ready to be merged back into the main conformance suite repository.

This repository is purely for development and should not be used as the official conformance suite.

Please see the official repo for the Open Banking Conformance Suite [here](https://github.com/OpenBankingUK/conformance-suite).

## Requirements and roadmap

The active product requirements and AI-agent-compatible delivery artefacts live under [`docs/requirements/`](docs/requirements/). Start there for the refreshed PRD v4 draft, roadmap, machine-readable requirement index, v4.0.1 AIS coverage ledger seed, agent slice template, and decision register. The older PRD in `docs/FCS Rebuild - PRD v3 [DRAFT].md` is retained for history.

## Model-bank smoke check

The first Ozone model-bank interaction is available as a small manual runner. It reads a JSON config and fetches the OpenID discovery document. The runner can also fetch the discovered JWKS endpoint when `followUp.mode` is set to `jwks` and the required certificate trust chain is available locally.

```bash
uv run python main.py config/model-bank-example.json
```

The runner writes a structured result JSON to the configured `resultOutputPath`, which defaults to `out/test-results.json`, and exits with `0` when all smoke-check steps pass, `1` when the model-bank check fails, `2` when the config is invalid, or `3` when the result file or the structured execution log cannot be written. Relative `resultOutputPath` and `executionLogPath` values are resolved from the current working directory, while certificate paths are resolved from the config file location.

Alongside the result file the runner writes a structured **execution log** (NDJSON, one event per line) to `executionLogPath` (default `out/execution-log.ndjson`). The log records `run-started`, `step-started`, `request-sent`, `response-received`, `assertion-evaluated`, `step-completed`, `run-completed` and the error events, with credentials and sensitive headers masked exactly as in the result file. The same log is exposed by the REST API as `GET /api/runs/<id>/log/` (`application/x-ndjson`), which returns the current full NDJSON snapshot in a single response — CI scripts can poll the endpoint to observe an in-flight run, but it is not a streaming/tail endpoint. Set `CONFORMANCE_DEVELOPER_MODE=true` to disable masking for local engineering debugging only — a `WARN` line is logged at startup whenever this is set, and it must never be enabled in release builds.

The config is JSON-only for now. TLS certificate paths, when supplied, are resolved under `tls.certificatePathRoot`; FAPI signing certificate and private-key paths are resolved under `fapiSigning.certificatePathRoot`. Do not commit real certificates, private keys, or inline secret material.

## Target-based conformance runs

Participant config selects what to test using a `testTarget` object that identifies the Open Banking standard, specification, security profile, version, and resource groups. The tool resolves the appropriate bundled test coverage automatically.

### Read/Write API runs

Add a `testTarget` section selecting the Read/Write specification and one resource group:

```jsonc
{
	"environment": "ozone-model-bank",
	"discoveryUrl": "https://auth1.obie.uk.ozoneapi.io/.well-known/openid-configuration",
	"timeoutSeconds": 10,
	"testTarget": {
		"standard": "obl",
		"specification": "read-write",
		"specificationVersion": "v4.0.1",
		"resourceGroups": ["ais"]
	},
	"oauth": {
		"clientId": "your-client-id-here",
		"redirectUri": "https://conformance.example.com/callback",
		"resourceBaseUrl": "https://resource.example.com"
	},
	"fapiSigning": {
		"certificatePathRoot": "./certs",
		"signingCertificatePath": "signing.crt",
		"signingPrivateKeyPath": "signing.key", // pragma: allowlist secret
		"kid": "your-signing-kid-here",
		"clientAssertionIssuer": "your-client-id-here",
		"clientAssertionSubject": "your-client-id-here",
		"tokenEndpointAuthMethod": "private_key_jwt"
	},
	"tls": {
		"certificatePathRoot": "./certs"
	},
	"resultOutputPath": "./out/rw-ais-results.json",
	"executionLogPath": "./out/rw-ais-log.ndjson"
}
```

Supported `testTarget` combinations for Read/Write:

| `specificationVersion` | `resourceGroups` | Coverage | Config required |
|---|---|---|---|
| `v4.0.1` | `["ais"]` | AIS certification baseline — full mandatory AIS resource coverage | `oauth.*`, `fapiSigning.*` |
| `v4.0.1` | `["pis"]` | PIS domestic-payment starter | `oauth.*`, `fapiSigning.*` |
| `v4.0.1` | `["cbpii"]` | CBPII PSU auth starter | `oauth.*` |
| `v4.0.1` | `["vrp"]` | VRP PSU auth starter | `oauth.*` |
| `v4.0` | `["ais"]` | AIS certification baseline (v4.0 paths) | `oauth.*`, `fapiSigning.*` |
| `v4.0` | `["pis"]` | PIS domestic-payment starter (v4.0) | `oauth.*`, `fapiSigning.*` |
| `v3.1.11` | `["ais"]` | AIS PSU auth starter | `oauth.*` |

Each run covers one resource group. For multi-resource-group testing, submit separate runs per resource group.

**Every bundled coverage set remains explicitly `partial` — none can satisfy certification eligibility yet.** `certificationCoverage: partial` is set in the underlying manifests and blocks `certificationEligibility.eligible` in result JSON.

The `oauth.resourceBaseUrl` must be the HTTPS base URL for the protected resource server without the `/open-banking/...` path prefix; the bundled manifests append that prefix themselves. The `fapiSigning` block is required for AIS baseline and PIS runs; `private_key_jwt` adds a `client_assertion` to the token exchange, while `tls_client_auth` reuses `tls.clientCertificatePath` / `tls.clientPrivateKeyPath` for outbound mTLS. Only `${config.oauth.*}` fields (non-secret values) are addressable from manifests — `fapiSigning`, TLS paths, certificates, and private keys are never placeholder-addressable.

PIS write requests require an OB-compliant detached JWS in `x-jws-signature`. Supply `fapiSigning.signatureIssuer` (`<orgId>/<softwareId>`) and `fapiSigning.signatureTrustAnchor` (`openbanking.org.uk`) together to enable the OB-specific JOSE protected header claims.

### Dynamic Client Registration (DCR) runs

Add a `testTarget` section selecting the DCR specification plus a `dcr` section with file-backed credential material:

```jsonc
{
	"environment": "ozone-model-bank",
	"discoveryUrl": "https://auth1.obie.uk.ozoneapi.io/.well-known/openid-configuration",
	"timeoutSeconds": 30,
	"testTarget": {
		"standard": "obl",
		"specification": "dynamic-client-registration",
		"specificationVersion": "3.3"
	},
	"dcr": {
		"credentialPathRoot": "./certs",
		"ssaPath": "ssa.jwt",
		"signingPrivateKeyPath": "signing.key", // pragma: allowlist secret
		"signingCertificatePath": "signing.crt",
		"transportCertificatePath": "transport.crt",
		"transportPrivateKeyPath": "transport.key", // pragma: allowlist secret
		"tokenEndpointAuthMethod": "tls_client_auth"
	},
	"tls": {
		"certificatePathRoot": "./certs",
		"clientCertificatePath": "transport.crt",
		"clientPrivateKeyPath": "transport.key" // pragma: allowlist secret
	},
	"resultOutputPath": "./out/dcr-results.json",
	"executionLogPath": "./out/dcr-log.ndjson"
}
```

Supported DCR versions: `3.2`, `3.3`, `3.4`. The `dcr` section requires:

| Field | Description |
|---|---|
| `ssaPath` | Software Statement Assertion JWT from the OB Directory |
| `signingPrivateKeyPath` | PEM RSA private key for registration JWT signing |
| `signingCertificatePath` | X.509 certificate paired with the signing key |
| `transportCertificatePath` | mTLS client certificate for all DCR connections |
| `transportPrivateKeyPath` | Private key paired with the transport certificate |
| `caBundlePath` | Optional PEM CA bundle for ASPSP server verification |
| `tokenEndpointAuthMethod` | `tls_client_auth` (default) or `private_key_jwt` |
| `disableKeepAlives` | Optional boolean; disables HTTP keep-alives when `true` |
| `tlsSkipVerify` | Optional boolean; **unsafe** — disables TLS server verification. Never use against real ASPSP infrastructure |

All credential paths are resolved under `credentialPathRoot` (defaults to the config file directory). DCR runs are **non-certifying** until a formal certification policy exists.

### CLI runs

```bash
# Read/Write AIS run (requires oauth + fapiSigning in config)
uv run python main.py config/model-bank-rw-ais-example.json

# DCR run (requires dcr section in config)
uv run python main.py config/model-bank-dcr-example.json

# Headless run from a saved RunPlan v2 JSON file
uv run python main.py config/model-bank-rw-ais-example.json --run-plan my-run-plan.json
```

`--manifest` remains an explicit override for authoring and certification-validation workflows. `--run-plan` accepts a RunPlan v2 JSON file (the export format from the plan-builder UI) for headless target/coverage-based runs. `--deselect` works with an explicit `--manifest`; it is not valid for target-based or smoke-check runs.

The REST API follows the same model: an inline `manifest` in `POST /api/runs/` wins; a `runPlan` key (RunPlan v2 JSON object) or `config.testTarget` routes through the plugin planning path; otherwise the smoke check runs. `deselectStepIds` is accepted with inline manifests only. In the browser plan builder at `/plan/`, the guided Standard → Specification → Security Profile → Version → Resource Group(s) → Endpoint Coverage → Field Values journey builds a RunPlan v2 for the configured ASPSP without requiring manual JSON entry. The guided flow includes model-bank examples for environment and discovery values, with the generated fields remaining editable for custom ASPSP/model-bank endpoints. CLI, REST API, and browser-launched runs all carry the validated `fapiSigning` block into runtime execution without widening the manifest placeholder boundary.

The PIS starter's manifest and config example show the new user-level schema contracts in practice: `testValueProfiles` declare named defaults and generated non-secret keys, `${testValues.<key>}` placeholders are only allowed for manifest-declared keys, `selectionMetadata` marks conditional rows with a machine-readable condition id/label, and `testValues.profile` / `testValues.overrides` select the effective profile at run time. The plan summary records whether a row was selected by default values, by override values, or remained conditional and deselected.

For a safe runnable PIS FCS starter flow:

1. Copy `config/model-bank-pis-fcs-legacy-benchmark-example.json` to a private local path, then replace placeholder OAuth/signing/Open Banking fields with your local participant values.
2. Keep certificates and private keys outside committed config (`local-config/` is local-only and must not be committed).
3. Run `uv run python main.py <your-local-config>.json` (or use the committed example path directly for offline preview wiring only).
4. Optionally use the browser plan builder (`/plan/`) to preview conditional rows before launch.
5. Inspect both `resultOutputPath` JSON and `executionLogPath` NDJSON outputs after each run.

Browser-launched runs can also drive manual PSU authorisation manifests. While a manual PSU step is waiting for the ASPSP callback, the run detail and status views show an `Open authorisation` action for the current step. The raw authorisation URL is held only in active in-memory run state for that browser prompt; result JSON, NDJSON execution logs, API log snapshots, downloadable artifacts, and the existing CLI/API masked-log behaviour remain unchanged.

All bundled suites set `certificationCoverage: partial` in their manifests. This blocks `certificationEligibility.eligible` in the result JSON and OBL-side `validate_report`, even when all mandatory steps pass and the tool version is approved. The result JSON includes a `certificationCoverage` block under `certificationEligibility` so the blocker is visible for audit. Manifest `authMetadata` remains the non-secret bundle inventory contract, and result JSON can also carry matching `authMetadata` and `environmentCapabilities` evidence blocks for validation. This still applies to both v4 AIS entries: the baseline suite is the certifiable-track working set, while the older slice remains a deliberately partial proof flow.

The bundled `ais-certification-baseline` manifest builds on the preserved slice path with a broader v4.0 AIS working set. Its mandatory default flow covers:

- generated PS256 request-object signing for the PSU authorisation redirect (`requestObject: {"source": "fapi-signing"}`)
- form-urlencoded token exchange using `${steps.psu-authorization.response.body.code}`
- token-endpoint client authentication from `fapiSigning.tokenEndpointAuthMethod`
- `POST /open-banking/v4.0/aisp/account-access-consents`
- `GET /open-banking/v4.0/aisp/accounts`
- `GET /open-banking/v4.0/aisp/accounts/${steps.accounts-list.response.body.Data.Account.0.AccountId}`
- `GET /open-banking/v4.0/aisp/accounts/${steps.accounts-list.response.body.Data.Account.0.AccountId}/balances`
- `GET /open-banking/v4.0/aisp/accounts/${steps.accounts-list.response.body.Data.Account.0.AccountId}/transactions`
- `GET /open-banking/v4.0/aisp/transactions`

The baseline manifest also signs the exact JSON consent payload as a detached PS256 JWS sent in `x-jws-signature`. The detached-JWS rows are available for local execution and authoring review, but they are not selected by default. The older `ais-certification-slice` manifest is kept unchanged as the narrower proof flow that stops after consent creation plus accounts, balances, and account-scoped transactions.

The resource-server calls assert more than status codes. The consent response must return `Data.ConsentId`. The accounts responses must include a top-level `Data` object plus an account array with `AccountId` and `Status` fields on each item. The balances responses must include a top-level `Data` object plus a balance array with `Type`, `Amount`, and `CreditDebitIndicator` fields on each item. The account-scoped and top-level transactions responses must include a top-level `Data` object plus a `Data.Transaction` array. In the `ais-certification-baseline` manifest, transaction items must include `BookingDateTime` and `Amount`; the preserved `ais-certification-slice` manifest continues to require `TransactionId` and `Amount`.

Manifest placeholders can also traverse JSON arrays using non-negative numeric path segments. Both bundled v4 AIS manifests use `${steps.accounts-list.response.body.Data.Account.0.AccountId}` to follow the first returned account into account-scoped resource endpoints. Array indices must be in bounds, and response placeholders must still resolve to primitive values rather than whole objects or arrays.

Masking now also covers OAuth authorisation codes, access tokens, ID tokens, client assertions, request objects, detached `x-jws-signature` values, and `Authorization` header values in result JSON, NDJSON execution logs, API log snapshots, and browser downloads. Signing certificate PEM, private-key PEM, and raw client-auth/signing config secrets are loaded only at execution time and are not serialized into logs, results, or error messages. The CLI still prints the one-time manual browser handoff URL needed for PSU consent, but persisted artifacts retain masked values.

The bundled `psu-auth-starter` manifests also act as the first authoring proof for the expanded generic response-assertion vocabulary. They stay deliberately partial, but now demonstrate response-header checks plus richer JSON rules on the discovery and JWKS responses: `header` assertions (`present`, `absent`, `equals`, `contains`, `matches_request_header`) and `json_field` rules including `required`, `absent`, `string`, `number`, `boolean`, `object`, `https_url`, `array`, `non_empty_array`, `min_items`, `equals`, `one_of`, and `all_items_have_field`. `matches_request_header` verifies that a response header echoes the request header value, accepts an optional `requestHeader` field that defaults to the assertion `name`, compares header names case-insensitively, and compares values case-sensitively. The v4 AIS baseline and legacy benchmark slices now also use a schema-backed `response_schema` assertion for allowlisted bundled standards documents.

For ad hoc manifest authoring, the updated `config/manifest-v1-openid-jwks-example.json` shows the same generic style against discovery/JWKS endpoints. Representative assertion fragments look like this:

```json
{
	"type": "header",
	"name": "content-type",
	"rule": "contains",
	"value": "application/json"
}
```

```json
{
	"type": "header",
	"name": "x-fapi-interaction-id",
	"rule": "matches_request_header",
	"requestHeader": "x-fapi-interaction-id"
}
```

```json
{
	"type": "json_field",
	"path": "keys",
	"rule": "all_items_have_field",
	"field": "kty"
}
```

```json
{
	"type": "json_field",
	"path": "token_endpoint_auth_method",
	"rule": "one_of",
	"values": ["private_key_jwt", "tls_client_auth"]
}
```

```json
{
	"type": "response_schema",
	"source": "bundled_openapi",
	"document": "ob-read-write-v4.0-account-info-openapi",
	"schemaRef": "#/components/schemas/OBReadAccount6"
}
```

For `response_schema`, `source` is currently restricted to `bundled_openapi`, and `document` is currently restricted to the bundled Account Info OpenAPI snapshots: `ob-read-write-v4.0-account-info-openapi` and `ob-read-write-v4.0.1-account-info-openapi`. Assertions must provide exactly one of `schemaRef` or inline `schema`; optional `bodyPath` can scope validation to a nested response node. Placeholders are not allowed in `source`, `document`, `schemaRef`, or `bodyPath`, and participant manifests cannot trigger arbitrary filesystem or network schema loading.

This is still an enabling layer for suite authors. It does not publish full Read/Write certification coverage, and no bundled suite should be treated as certifying until Standards confirm the complete mandatory manifest coverage.

## Certification report validation

OBL reviewers can validate a submitted result JSON against the manifest used for the run and an approved-release policy:

```bash
uv run python -m conformance.certification_cli out/test-results.json \
	--manifest config/manifest-v1-openid-jwks-example.json \
	--approved-releases config/approved-fcs-releases-example.json
```

The command prints a Confluence-ready summary to stdout and exits with `0` when the report is valid, `1` when validation finds blocking certification issues, `2` when an input file is malformed or missing required fields, and `3` when `--summary-output` cannot be written.

For complete-suite reports the validator also checks submitted `authMetadata` and `environmentCapabilities` evidence against the manifest and catalog metadata; partial bundled suites still fail on coverage first.

The approved-release policy is supplied as JSON so release governance is kept outside Python code:

```json
{
	"schemaVersion": "v1",
	"approvedToolVersions": ["OBL-APPROVED-RELEASE-VERSION"]
}
```

The bundled `config/approved-fcs-releases-example.json` is non-authoritative and contains a placeholder version. Replace it with the exact OBL-approved release versions before using the validator for a real review.

Participant runs can also use the same policy shape for report self-assessment by adding `approvedReleasePolicyPath` to model-bank config JSON:

```json
{
	"environment": "ozone-model-bank",
	"discoveryUrl": "https://auth1.obie.uk.ozoneapi.io/.well-known/openid-configuration",
	"approvedReleasePolicyPath": "approved-fcs-releases-example.json"
}
```

The path is resolved inside the config directory for CLI-loaded config files, or the process working directory for API/UI-submitted config JSON. Malformed policy files fail config validation before a run starts. Generated reports always include `certificationEligibility.approvedRelease`; if the policy is absent or does not list the current `tool.version`, the participant-side self-assessment is non-eligible. OBL-side validation remains authoritative and recomputes approved-release status from independently supplied inputs.

Generated report JSON includes stable metadata consumed by the validator:

```json
{
	"metadata": {"reportVersion": "1.0"},
	"tool": {"version": "0.1.0"}
}
```

Release builds can stamp the Docker image with `--build-arg CONFORMANCE_TOOL_VERSION=<version>`. Local/source runs fall back to `[project].version` from `pyproject.toml`, then `0+unknown` if no version can be resolved.
