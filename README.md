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

## Config-selected suite runs

Participant config can also select a bundled conformance-suite manifest instead of requiring callers to pass or paste manifest JSON manually. Add a `testSuite` object to the model-bank config:

```jsonc
{
	"environment": "ozone-model-bank",
	"discoveryUrl": "https://auth1.obie.uk.ozoneapi.io/.well-known/openid-configuration",
	"timeoutSeconds": 10,
	"testSuite": {
		"standard": "ob-read-write",
		"specVersion": "v4.0",
		"profile": "fapi1-advanced",
		"suite": "discovery-jwks"
	},
	"tls": {
		"certificatePathRoot": "./certs"
	},
	"resultOutputPath": "./out/test-results.json",
	"executionLogPath": "./out/execution-log.ndjson"
}
```

Supported bundled suites use the `ob-read-write` standard and `fapi1-advanced` profile, with the explicit version/suite combinations shown below. **Every bundled suite remains explicitly `partial` coverage — none can satisfy certification eligibility yet.** The v4.0 AIS catalog now has two bundled tracks: `ais-certification-baseline` is the certifiable-track baseline suite, and `ais-certification-slice` is the older preserved proof slice. The new PIS `pis-domestic-payment-starter` is a sandbox/model-bank-only domestic-payment consent/submission/read-back starter: it uses Ozone-demo defaults with a synthetic fallback, exercises detached-JWS PIS write calls and a narrow pair of signing-negative checks, and must not be used to claim live-payment or full certification coverage. The `pis-fcs-legacy-benchmark` suite now preserves all 29 legacy PIS rows as default-selected coverage when prerequisite test values are available and keeps capability/test-value gating explicit; it remains `certificationCoverage: partial`.

| Spec version | Suite name | Steps | Participant config required |
|---|---|---|---|
| `v3.1.11`, `v4.0` | `discovery-jwks` | OpenID discovery + JWKS fetch | No |
| `v3.1.11`, `v4.0` | `psu-auth-starter` | OpenID discovery + JWKS fetch + manual PSU authorisation | `oauth.clientId`, `oauth.redirectUri`, `oauth.openBankingIntentId` |
| `v4.0` | `ais-certification-baseline` | Discovery + JWKS + manual PSU authorisation + token exchange + consent creation + mandatory core AIS resource coverage, with optional/conditional AIS rows available as opt-in plan entries | `oauth.clientId`, `oauth.redirectUri`, `oauth.resourceBaseUrl`, `fapiSigning.*` |
| `v4.0` | `ais-certification-slice` | Discovery + JWKS + manual PSU authorisation + token exchange + account-access consent + accounts, balances, and transactions resource validation | `oauth.clientId`, `oauth.redirectUri`, `oauth.resourceBaseUrl` |
| `v4.0` | `pis-domestic-payment-starter` | Discovery + JWKS + client-credentials token + domestic payment consent creation + manual PSU authorisation + consent read-back + funds confirmation + domestic payment submission + domestic payment read-back | `oauth.clientId`, `oauth.redirectUri`, `oauth.resourceBaseUrl`, `fapiSigning.*` |
| `v4.0` | `pis-fcs-legacy-benchmark` | Discovery + JWKS + client-credentials token + domestic payment consent (×2 variants) + manual PSU authorisation + token exchange + consent read-back + funds confirmation + domestic payment submission + domestic payment read-back; plus scheduled/standing/international legacy rows auto-selected when required test values are present | `oauth.clientId`, `oauth.redirectUri`, `oauth.resourceBaseUrl`, `fapiSigning.*` |

The `psu-auth-starter`, `ais-certification-baseline`, and `ais-certification-slice` suites require an `oauth` section in the participant config. `clientId` must be registered at the ASPSP, `redirectUri` must be an HTTPS redirect URI registered with the ASPSP, and `resourceBaseUrl` must be the HTTPS base URL for the protected AIS resource server. Do not include the Open Banking API path prefix in `resourceBaseUrl`; the bundled v4 AIS manifests append `/open-banking/v4.0/aisp/...` themselves. The starter suite uses a pre-existing `oauth.openBankingIntentId` and does not create account-access consent; run `ais-certification-baseline` first if you want the tool to create consent and return a `Data.ConsentId`. Both AIS suites also use `resourceBaseUrl` for consent creation and protected resource calls.

The `pis-domestic-payment-starter` suite also requires `oauth.clientId`, `oauth.redirectUri`, `oauth.resourceBaseUrl`, and `fapiSigning.*`. For Ozone model-bank runs, set `resourceBaseUrl` to the HTTPS resource host root (for example `https://rs1.obie.uk.ozoneapi.io`) and do not include `/open-banking/...` in that value; the suite appends `/open-banking/v4.0/pisp/...` itself. It uses the manifest's default `testValueProfiles` (`ozone-demo`) unless participants choose `testValues.profile`, and allows safe key-level overrides through `testValues.overrides`. Manifest steps can declare `selectionMetadata` for conditional rows; those rows are auto-selected only when the declared `requiredTestValueKeys` resolve from the selected profile or overrides. Result JSON, execution logs, and plan preview carry masked evidence for the selected profile/source, override keys, and conditional-row status without exposing secret material. The suite's request-object signing-negative PSU check runs in headless mode with an explicit expected authorisation-endpoint rejection (`HTTP 400`). Some ASPSPs (including Ozone deployments) may surface that rejection via a non-callback redirect rather than a direct `400`; this is accepted only for that explicitly-declared negative signing check.

The `pis-fcs-legacy-benchmark` suite follows the same signing and config requirements as `pis-domestic-payment-starter`. It adds a second domestic-payment consent creation variant (`OB-400-DOP-100300`) that includes debtor account fields. The 21 conditional rows (domestic scheduled, domestic standing order, international, and international scheduled payments) are marked conditional and are default-selected when their required `testValues` keys are resolved, while still remaining capability/config gated when prerequisites are missing. The committed `config/model-bank-pis-fcs-legacy-benchmark-example.json` uses placeholder-only participant values and non-secret `testValues.overrides` so all legacy conditional categories can be previewed/run locally without committing private participant data. This suite is `certificationCoverage: partial` — it preserves legacy script IDs and benchmarks prior FCS PIS coverage; it must not be used to claim full PIS certification coverage.

PIS write requests (consent creation and payment submission) require an Open Banking-compliant detached JWS in `x-jws-signature`. To generate compliant headers, supply two optional non-secret fields in `fapiSigning`: `signatureIssuer` (the OB `iss` claim — typically `<orgId>/<softwareStatementId>`) and `signatureTrustAnchor` (the `tan` claim — for OB UK this is `openbanking.org.uk`). These must be supplied together or not at all. When supplied, the protected JOSE header includes `http://openbanking.org.uk/iat`, `http://openbanking.org.uk/iss`, and `http://openbanking.org.uk/tan` in addition to the base `alg`, `kid`, `b64`, and `crit` claims. The `x-fapi-financial-id` header (the ASPSP's unique identifier) is injected automatically on PIS write requests when `openBanking.financialId` is configured; this value is masked in all persisted logs and results.

The `ais-certification-baseline` suite also accepts a dedicated `fapiSigning` block for generated JAR request objects, token-endpoint client authentication, and detached JWS signing of the account-access-consent request body. The current config fields are `certificatePathRoot`, `signingCertificatePath`, `signingPrivateKeyPath`, `kid`, `clientAssertionIssuer`, `clientAssertionSubject`, `tokenEndpointAuthMethod` (`private_key_jwt` or `tls_client_auth`), and optionally `signatureIssuer` and `signatureTrustAnchor` (required together for OB-compliant detached JWS — see the PIS section above). The preserved `ais-certification-slice` remains the older proof flow and does not opt into these manifest directives.

`private_key_jwt` uses the signing key to add `client_assertion_type` and `client_assertion` form fields at token exchange time. `tls_client_auth` reuses the existing `tls.clientCertificatePath` and `tls.clientPrivateKeyPath` settings for the outbound mTLS client and still keeps all signing and client-auth inputs outside the manifest placeholder allow-list.

```jsonc
{
	"environment": "ozone-model-bank",
	"discoveryUrl": "https://auth1.obie.uk.ozoneapi.io/.well-known/openid-configuration",
	"timeoutSeconds": 10,
	"testSuite": {
		"standard": "ob-read-write",
		"specVersion": "v4.0",
		"profile": "fapi1-advanced",
		"suite": "ais-certification-baseline"
	},
	"oauth": {
		"clientId": "your-client-id-here",
		"redirectUri": "https://conformance.example.com/callback",
		"resourceBaseUrl": "https://resource.example.com"
	},
	"fapiSigning": {
		"certificatePathRoot": "./certs",
		"signingCertificatePath": "dummy-signing.crt",
		"signingPrivateKeyPath": "dummy-signing.key", // pragma: allowlist secret
		"kid": "your-signing-kid-here",
		"clientAssertionIssuer": "your-client-id-here",
		"clientAssertionSubject": "your-client-id-here",
		"tokenEndpointAuthMethod": "private_key_jwt"
	},
	"tls": {
		"certificatePathRoot": "./certs"
	},
	"resultOutputPath": "./out/test-results.json",
	"executionLogPath": "./out/execution-log.ndjson"
}
```

Only `${config.discoveryUrl}`, `${config.environment}`, `${config.oauth.clientId}`, `${config.oauth.redirectUri}`, `${config.oauth.openBankingIntentId}`, and `${config.oauth.resourceBaseUrl}` are exposed to manifests. `fapiSigning` values, TLS paths, certificates, private keys, client secrets, request objects, client assertions, and arbitrary config traversal are not placeholder-addressable.

Run a config-selected suite from the CLI by omitting `--manifest`:

```bash
# Smoke suite (no OAuth config required)
uv run python main.py config/model-bank-suite-example.json

# PSU auth starter suite (requires oauth section in config)
uv run python main.py config/model-bank-psu-auth-starter-example.json

# PIS domestic-payment starter (requires oauth, fapiSigning, and optional testValues)
uv run python main.py config/model-bank-pis-domestic-payment-starter-example.json

# PIS FCS legacy benchmark (requires oauth, fapiSigning, and optional testValues)
uv run python main.py config/model-bank-pis-fcs-legacy-benchmark-example.json

# AIS certification baseline (recommended v4 AIS catalog entry; requires
# oauth.clientId, oauth.redirectUri, oauth.resourceBaseUrl, and fapiSigning)
uv run python main.py config/model-bank-ais-certification-baseline-example.json

# AIS certification proof slice (older partial reference flow; requires the
# same oauth settings but does not opt into generated signing directives)
uv run python main.py config/model-bank-ais-certification-slice-example.json
```

`--manifest` remains an explicit override for authoring and certification-validation workflows. `--deselect` works with either an explicit manifest or a config-selected `testSuite`, and remains invalid for plain model-bank smoke checks that have neither.

The REST API follows the same precedence: an inline `manifest` in `POST /api/runs/` wins; otherwise `config.testSuite` resolves a bundled suite; otherwise the legacy smoke check runs. `deselectStepIds` is accepted with inline or config-resolved manifests only. In the browser plan builder at `/plan/`, leave the manifest textarea blank to preview and launch the suite selected by config, or paste a manifest to override the catalog for authoring/testing. The guided flow includes model-bank examples for environment and discovery values, with the generated fields remaining editable for custom ASPSP/model-bank endpoints. The preview also surfaces auth-bundle inventory and capability warnings before launch. CLI, REST API, and browser-launched config-resolved runs all carry the validated `fapiSigning` block into runtime execution without widening the manifest placeholder boundary.

The PIS starter's manifest and config example show the new user-level schema contracts in practice: `testValueProfiles` declare named defaults and generated non-secret keys, `${testValues.<key>}` placeholders are only allowed for manifest-declared keys, `selectionMetadata` marks conditional rows with a machine-readable condition id/label, and `testValues.profile` / `testValues.overrides` select the effective profile at run time. The plan summary records whether a row was selected by default values, by override values, or remained conditional and deselected.

For a safe runnable PIS FCS starter flow:

1. Copy `config/model-bank-pis-fcs-legacy-benchmark-example.json` to a private local path, then replace placeholder OAuth/signing/Open Banking fields with your local participant values.
2. Keep certificates and private keys outside committed config (`local-config/` is local-only and must not be committed).
3. Run `uv run python main.py <your-local-config>.json` (or use the committed example path directly for offline preview wiring only).
4. Optionally use the browser plan builder (`/plan/`) with blank manifest JSON to preview config-selected conditional rows before launch.
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
