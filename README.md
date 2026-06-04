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

The supported combinations are `ob-read-write` `v3.1.11` or `v4.0`, `fapi1-advanced`, and `discovery-jwks`. The bundled manifests use `${config.discoveryUrl}` for the OpenID discovery request and then follow the discovered JWKS URI with ordinary step placeholders. Only `${config.discoveryUrl}` and `${config.environment}` are exposed to manifests; TLS paths, certificates, future secrets, and arbitrary config traversal are not placeholder-addressable.

Run a config-selected suite from the CLI by omitting `--manifest`:

```bash
uv run python main.py config/model-bank-suite-example.json
```

`--manifest` remains an explicit override for authoring and certification-validation workflows. `--deselect` works with either an explicit manifest or a config-selected `testSuite`, and remains invalid for plain model-bank smoke checks that have neither.

The REST API follows the same precedence: an inline `manifest` in `POST /api/runs/` wins; otherwise `config.testSuite` resolves a bundled suite; otherwise the legacy smoke check runs. `deselectStepIds` is accepted with inline or config-resolved manifests only. In the browser plan builder at `/plan/`, leave the manifest textarea blank to preview and launch the suite selected by config, or paste a manifest to override the catalog for authoring/testing.

Browser-launched runs can also drive manual PSU authorisation manifests. While a manual PSU step is waiting for the ASPSP callback, the run detail and status views show an `Open authorisation` action for the current step. The raw authorisation URL is held only in active in-memory run state for that browser prompt; result JSON, NDJSON execution logs, API log snapshots, downloadable artifacts, and the existing CLI/API masked-log behaviour remain unchanged.

Current bundled entries are smoke-level OpenID discovery and JWKS checks. They exercise the config-driven catalog, placeholder, plan, result, and UI rails, but they are not full Open Banking Read/Write v3.1.11 or v4.0 certification coverage.

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
