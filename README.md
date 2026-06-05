# Functional Conformance Suite V2 (WIP)

This forked repository will be used to manage the development of the new v2 Functional Confirmance suite before it is ready to be merged back into the main conformance suite repository.

This repository is purely for development and should not be used as the official conformance suite.

Please see the official repo for the Open Banking Conformance Suite [here](https://github.com/OpenBankingUK/conformance-suite).

## Model-bank smoke check

The first Ozone model-bank interaction is available as a small manual runner. It reads a JSON config and fetches the OpenID discovery document. The runner can also fetch the discovered JWKS endpoint when `followUp.mode` is set to `jwks` and the required certificate trust chain is available locally.

```bash
uv run python main.py config/model-bank-example.json
```

The runner writes a structured result JSON to the configured `resultOutputPath`, which defaults to `out/test-results.json`, and exits with `0` when all smoke-check steps pass, `1` when the model-bank check fails, `2` when the config is invalid, or `3` when the result file or the structured execution log cannot be written. Relative `resultOutputPath` and `executionLogPath` values are resolved from the current working directory, while certificate paths are resolved from the config file location.

Alongside the result file the runner writes a structured **execution log** (NDJSON, one event per line) to `executionLogPath` (default `out/execution-log.ndjson`). The log records `run-started`, `step-started`, `request-sent`, `response-received`, `assertion-evaluated`, `step-completed`, `run-completed` and the error events, with credentials and sensitive headers masked exactly as in the result file. The same log is exposed by the REST API as `GET /api/runs/<id>/log/` (`application/x-ndjson`), which returns the current full NDJSON snapshot in a single response — CI scripts can poll the endpoint to observe an in-flight run, but it is not a streaming/tail endpoint. Set `CONFORMANCE_DEVELOPER_MODE=true` to disable masking for local engineering debugging only — a `WARN` line is logged at startup whenever this is set, and it must never be enabled in release builds.

The config is JSON-only for now. Certificate paths, when supplied, are resolved under `tls.certificatePathRoot`; do not commit real certificates, private keys, or inline secret material.

## Config-selected suite runs

Participant config can also select a bundled conformance-suite manifest instead of requiring callers to pass or paste manifest JSON manually. Add a `testSuite` object to the model-bank config:

```json
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

The supported suite combinations are `ob-read-write` × (`v3.1.11` | `v4.0`) × `fapi1-advanced` × (`discovery-jwks` | `psu-auth-starter`). **Both bundled suites are explicitly `partial` coverage — neither can satisfy certification eligibility.** A complete certification suite will be published as a separate follow-up.

| Suite name | Steps | OAuth config required |
|---|---|---|
| `discovery-jwks` | OpenID discovery + JWKS fetch | No |
| `psu-auth-starter` | OpenID discovery + JWKS fetch + manual PSU authorisation | Yes (`oauth.clientId`, `oauth.redirectUri`) |

The `psu-auth-starter` suite requires an `oauth` section in the participant config with the client identifier registered at the ASPSP and an HTTPS redirect URI also registered with the ASPSP. The bundled PSU authorisation step resolves both values from this config:

```json
{
	"environment": "ozone-model-bank",
	"discoveryUrl": "https://auth1.obie.uk.ozoneapi.io/.well-known/openid-configuration",
	"timeoutSeconds": 10,
	"testSuite": {
		"standard": "ob-read-write",
		"specVersion": "v4.0",
		"profile": "fapi1-advanced",
		"suite": "psu-auth-starter"
	},
	"oauth": {
		"clientId": "your-client-id-here",
		"redirectUri": "https://conformance.example.com/callback"
	},
	"tls": {
		"certificatePathRoot": "./certs"
	},
	"resultOutputPath": "./out/test-results.json",
	"executionLogPath": "./out/execution-log.ndjson"
}
```

Only `${config.discoveryUrl}`, `${config.environment}`, `${config.oauth.clientId}`, and `${config.oauth.redirectUri}` are exposed to manifests. TLS paths, certificates, private keys, client secrets, and arbitrary config traversal are not placeholder-addressable.

Run a config-selected suite from the CLI by omitting `--manifest`:

```bash
# Smoke suite (no OAuth config required)
uv run python main.py config/model-bank-suite-example.json

# PSU auth starter suite (requires oauth section in config)
uv run python main.py config/model-bank-psu-auth-starter-example.json
```

`--manifest` remains an explicit override for authoring and certification-validation workflows. `--deselect` works with either an explicit manifest or a config-selected `testSuite`, and remains invalid for plain model-bank smoke checks that have neither.

The REST API follows the same precedence: an inline `manifest` in `POST /api/runs/` wins; otherwise `config.testSuite` resolves a bundled suite; otherwise the legacy smoke check runs. `deselectStepIds` is accepted with inline or config-resolved manifests only. In the browser plan builder at `/plan/`, leave the manifest textarea blank to preview and launch the suite selected by config, or paste a manifest to override the catalog for authoring/testing.

Browser-launched runs can also drive manual PSU authorisation manifests. While a manual PSU step is waiting for the ASPSP callback, the run detail and status views show an `Open authorisation` action for the current step. The raw authorisation URL is held only in active in-memory run state for that browser prompt; result JSON, NDJSON execution logs, API log snapshots, downloadable artifacts, and the existing CLI/API masked-log behaviour remain unchanged.

Both bundled suites set `certificationCoverage: partial` in their manifests. This blocks `certificationEligibility.eligible` in the result JSON and OBL-side `validate_report`, even when all mandatory steps pass and the tool version is approved. The result JSON includes a `certificationCoverage` block under `certificationEligibility` so the blocker is visible for audit.

The bundled `psu-auth-starter` manifests also act as the first authoring proof for the expanded generic response-assertion vocabulary. They stay deliberately partial, but now demonstrate response-header checks plus richer JSON rules on the discovery and JWKS responses: `header` assertions (`present`, `absent`, `equals`, `contains`) and `json_field` rules including `required`, `absent`, `string`, `number`, `boolean`, `object`, `https_url`, `array`, `non_empty_array`, `min_items`, `equals`, `one_of`, and `all_items_have_field`.

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

This is still an enabling layer for suite authors. It does not publish full Read/Write certification coverage, and no bundled suite should be treated as certifying until Standards author the complete mandatory manifest coverage separately.

## Certification report validation

OBL reviewers can validate a submitted result JSON against the manifest used for the run and an approved-release policy:

```bash
uv run python -m conformance.certification_cli out/test-results.json \
	--manifest config/manifest-v1-openid-jwks-example.json \
	--approved-releases config/approved-fcs-releases-example.json
```

The command prints a Confluence-ready summary to stdout and exits with `0` when the report is valid, `1` when validation finds blocking certification issues, `2` when an input file is malformed or missing required fields, and `3` when `--summary-output` cannot be written.

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
