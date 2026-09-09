# Testing Strategy

## Framework recommendation

Use `pytest` with `pytest-django`. The supported suite is entirely deterministic
and offline: no test may contact Ozone, a bank, a model bank, or any other
external endpoint. HTTP is mocked at the client boundary (e.g. `httpx.MockTransport`)
by default; a loopback service is retained only where socket, HTTP framing, TLS,
mTLS, or certificate behaviour is itself under test.

`tests/conftest.py` enforces that rather than trusting it. A session-scoped
autouse fixture wraps `socket.socket.connect`/`connect_ex` for the whole run and
raises `ExternalNetworkAccessError` for any destination that is not a loopback
address. Every client the product can build — `httpx`, `ssl`, and the standard
library — routes through those two methods, so a test that reaches for Ozone or
a real bank fails immediately and names the address it tried. The four loopback
transport tests are unaffected: they connect to `127.0.0.1`/`::1` and are
explicitly permitted.

## Test categories

There are exactly two supported pytest categories. Both are mandatory — the
distinction exists for ownership and focused iteration, not for differing
quality bars.

| Category | Marker | Scope |
| --- | --- | --- |
| Unit | `unit` | Deterministic in-process behaviour: parser/compiler logic, result serialization, CLI/config validation, isolated helpers. No sockets, no Django request/response boundary or database, and no background run worker. In-process thread-safety checks that start and join their own threads stay here. |
| Component | `component` | Offline collaboration through meaningful boundaries: Django request/response handling, persistence, filesystem behaviour, complete executor flows, background run-worker behaviour, and narrowly justified transport fixtures (e.g. loopback TLS/mTLS). |

`make test` runs both categories offline with coverage; `make unit` and
`make component` give focused iteration without coverage. Every collected test
must declare exactly one category — `tests/conftest.py` fails collection and
names the offending node ids otherwise. There is no separate
live-network or container-level pytest tier: Docker image build, start-up, and
health-check verification are handled by the main CI workflow as a packaging
concern, not as a pytest marker.

## Layout

Category is a directory, not a per-test decision. Tests live under
`tests/unit/` or `tests/component/`, grouped by behavioural domain rather than
by production module, and no test file sits directly in `tests/`.

```text
tests/
  conftest.py          # category enforcement + offline network guard
  unit/
    api/               # run store, auth-session store, builder forms, web settings
    catalogue/         # catalogue definitions, compilation, parity contracts
    execution/         # executor, context, HTTP, logging, CLI entry points, DCR adapter
    security/          # signing, response signatures, masking, PSU authorisation
    validation/        # manifest, plan, config, certification, result-gate validation
  component/
    api/               # loopback REST endpoints
    django/            # browser routing, views, builder flow
    execution/         # complete offline CLI/REST/browser execution flows
    protocol/          # loopback TLS/mTLS transport
  support/             # shared fakes, fixtures, and builders (no tests)
```

Each module declares its category once with a module-level
`pytestmark = pytest.mark.unit` or `pytest.mark.component`. A module that would
need both belongs in two modules. Fixtures live in the narrowest conftest that
consumes them: `tests/conftest.py` owns category enforcement and the offline
network guard only,
`tests/component/conftest.py` owns the API singleton and DCR service fixtures,
and `tests/unit/execution/conftest.py` exposes the in-process DCR services.
Shared fakes and builders — the deterministic DCR service, the run-execution
stub, catalogue plan payloads, DCR adapter builders, executor signing material,
PSU step builders, and manifest documents — live in `tests/support/`,
which contains no tests.

## Suite decomposition and consolidation map

Large modules are split by subject, and repetitive acceptance/rejection cases
are expressed as typed parameter matrices with descriptive ids. Nothing is
deleted silently: every retired test name maps to a retained module or matrix
case id below.

### Module decomposition

| Retired module | Replacement modules | Subject boundary |
| --- | --- | --- |
| `tests/unit/execution/test_executor.py` (5,662 lines) | `test_executor_http.py`, `test_executor_dependencies.py`, `test_executor_token_auth.py`, `test_executor_detached_jws.py`, `test_executor_response_signatures.py`, `test_executor_evidence.py`, `test_executor_run_orchestration.py`, `test_executor_compiled_payments.py`, `test_executor_compiled_accounts.py`, `test_executor_psu_manual.py`, `test_executor_psu_headless.py` | Request dispatch; step dependencies and skip semantics; token-endpoint client auth and signing-credential lifecycle; detached JWS write profiles; response-signature validation; outcome/evidence masking; run orchestration (plan selection, eligibility, run identity, engine errors); compiled PIS/VRP plans; compiled AIS/CBPII plans; PSU manual handoff; PSU headless redirects. |
| `tests/unit/validation/test_manifest.py` (3,185 lines) | `test_manifest_v0.py`, `test_manifest_assertions.py`, `test_manifest_requests.py`, `test_manifest_placeholders.py`, `test_manifest_steps.py`, `test_manifest_psu.py`, `test_manifest_policies.py` | Legacy v0 documents; assertion vocabulary and response-schema assertions; request shape (methods, headers, bodies); placeholder and step-dependency validation; step structure, metadata, and certification coverage; PSU authorisation steps; runtime signing directives. |
| `tests/component/api/test_run_endpoints.py` (1,544 lines) | `test_run_endpoints.py`, `test_auth_session_endpoints.py` | Run lifecycle endpoints (create/status/result/log, loopback guard) versus PSU auth-session endpoints (register/read/discard). |
| `tests/component/django/test_ui_views.py` (1,113 lines) | `test_builder_wizard_views.py`, `test_run_detail_views.py` | Builder wizard routes and rendered pages versus run detail routes and rendered run state. Builder form and draft-state behaviour stays in the unit suite. |
| `tests/unit/execution/test_dcr_execution.py` (1,108 lines) | `test_dcr_execution.py`, `test_dcr_registration_responses.py` | DCR plan execution flows versus DCR 3.4 registration-response cardinality validation. |
| `tests/unit/catalogue/test_catalogue.py` (1,079 lines) | `test_catalogue_compilation.py`, `test_catalogue_plan_documents.py` | Plan compilation against a catalogue versus plan-document parsing, validation, and export. |
| `tests/unit/api/test_builder_wizard.py` (1,067 lines) | `test_builder_wizard_scope.py`, `test_builder_wizard_config.py` | Wizard forms, draft persistence, and catalogue scope selection versus runtime/business configuration and visibility derived from that scope. |
| `tests/unit/validation/test_model_bank_config.py` (1,013 lines) | `test_model_bank_config.py`, `test_model_bank_config_oauth.py`, `test_model_bank_config_signing.py` | Core config (discovery, transport, output paths, release policy) versus the OAuth 2.0 section versus the FAPI signing section. |

Shared builders and fakes extracted during the split live in `tests/support/`:
`executor_signing.py` (RSA keypairs, FAPI signing config, Open Banking
response-signature material), `executor_psu.py` (parsed PSU steps and the
deterministic clock), and `manifest_documents.py` (valid v0/v1/PSU manifest
documents plus the one- and two-step builders the matrices vary).

`tests/unit/catalogue/test_v311_parity.py`, `test_v40_pis_parity.py`, and
`test_dcr_parity_contract.py` were reviewed and deliberately left intact: each
test pins a distinct legacy FCS traceability guarantee (pinned source hashes,
selected-row inventory, per-API replay of legacy request constants) against a
different fixture, so folding them into a matrix would hide which legacy row a
failure belongs to.

Two unused module-level helpers, `_file_reference_catalogue` and
`_file_reference_plan_spec_json`, were removed from the run-endpoint component
tests. They were already dead before this work — no test referenced them.

### Manifest parser matrices

Each retired manifest-parser test below is a case in a typed parameter matrix
in the module named by the matrix. Exception types, message patterns, and the
field data that triggers them are carried unchanged into the case data.

`test_parse_v1_manifest_accepts_request_shape`

| Retired test | Retained case id |
| --- | --- |
| `test_parse_v1_manifest_accepts_single_step_without_placeholders` | `get-without-placeholders` |
| `test_parse_v1_manifest_accepts_non_get_methods[POST]` | `method-post` |
| `test_parse_v1_manifest_accepts_non_get_methods[PUT]` | `method-put` |
| `test_parse_v1_manifest_accepts_non_get_methods[PATCH]` | `method-patch` |
| `test_parse_v1_manifest_accepts_non_get_methods[DELETE]` | `method-delete` |
| `test_parse_v1_manifest_accepts_header_value_with_htab` | `header-value-with-htab` |
| `test_parse_v1_manifest_accepts_json_body` | `untagged-json-body` |
| `test_parse_v1_manifest_accepts_tagged_json_body` | `tagged-json-body` |
| `test_parse_v1_manifest_accepts_body_on_delete` | `json-body-on-delete` |
| `test_parse_v1_manifest_accepts_form_body` | `form-body` |

`test_parse_v1_manifest_rejects_invalid_request_shape`

| Retired test | Retained case id |
| --- | --- |
| `test_parse_v1_manifest_rejects_unknown_method` | `unknown-method` |
| `test_parse_v1_manifest_rejects_non_https_url_without_placeholder` | `non-https-url-without-placeholder` |
| `test_parse_v1_manifest_rejects_non_string_header_value` | `non-string-header-value` |
| `test_parse_v1_manifest_rejects_empty_header_value` | `empty-header-value` |
| `test_parse_v1_manifest_rejects_invalid_header_name` | `invalid-header-name` |
| `test_parse_v1_manifest_rejects_body_on_get` | `json-body-on-get` |
| `test_parse_v1_manifest_rejects_null_body` | `null-body` |
| `test_parse_v1_manifest_rejects_form_body_on_get` | `form-body-on-get` |
| `test_parse_v1_manifest_rejects_empty_form_fields` | `empty-form-fields` |
| `test_parse_v1_manifest_rejects_missing_form_fields` | `missing-form-fields` |
| `test_parse_v1_manifest_rejects_unknown_encoding` | `unknown-body-encoding` |

`test_parse_v1_manifest_rejects_non_transportable_header_value`

| Retired test | Retained case id |
| --- | --- |
| `test_parse_v1_manifest_rejects_header_value_with_crlf[Bearer\\r\\nX-Injected: evil]` | `crlf-injection` |
| `test_parse_v1_manifest_rejects_header_value_with_crlf[token\\nfoo]` | `lf` |
| `test_parse_v1_manifest_rejects_header_value_with_crlf[token\\rfoo]` | `cr` |
| `test_parse_v1_manifest_rejects_header_value_with_control_chars[NUL]` | `control-nul` |
| `test_parse_v1_manifest_rejects_header_value_with_control_chars[DEL]` | `control-del` |
| `test_parse_v1_manifest_rejects_header_value_with_control_chars[SOH]` | `control-soh` |
| `test_parse_v1_manifest_rejects_header_value_with_control_chars[US]` | `control-us` |
| `test_parse_v1_manifest_rejects_header_value_with_obs_text[obs-text-e9]` | `obs-text-e9` |
| `test_parse_v1_manifest_rejects_header_value_with_obs_text[obs-text-80]` | `obs-text-80` |
| `test_parse_v1_manifest_rejects_header_value_with_obs_text[obs-text-ff]` | `obs-text-ff` |

`test_parse_v1_manifest_accepts_bundled_response_schema_document`

| Retired test | Retained case id |
| --- | --- |
| `test_parse_v1_manifest_accepts_v4_0_1_response_schema_document` | `account-info-v4.0.1` |
| `test_parse_v1_manifest_accepts_payment_initiation_response_schema_documents[ob-read-write-v4.0-payment-initiation-openapi]` | `payment-initiation-v4.0` |
| `test_parse_v1_manifest_accepts_payment_initiation_response_schema_documents[ob-read-write-v4.0.1-payment-initiation-openapi]` | `payment-initiation-v4.0.1` |

`test_parse_v1_manifest_rejects_forward_reference`

| Retired test | Retained case id |
| --- | --- |
| `test_parse_v1_manifest_rejects_forward_reference` | `url` |
| `test_parse_v1_manifest_rejects_forward_reference_in_header` | `header` |
| `test_parse_v1_manifest_rejects_forward_reference_in_body` | `body` |

`test_parse_v1_manifest_rejects_malformed_placeholder`

| Retired test | Retained case id |
| --- | --- |
| `test_parse_v1_manifest_rejects_malformed_placeholder` | `url` |
| `test_parse_v1_manifest_rejects_malformed_placeholder_in_body` | `json-body` |
| `test_parse_v1_manifest_rejects_malformed_placeholder_in_form_field` | `form-field` |

`test_parse_v1_manifest_accepts_certification_coverage`

| Retired test | Retained case id |
| --- | --- |
| `test_parse_v1_manifest_defaults_coverage_to_partial_when_absent` | `absent-defaults-to-partial` |
| `test_parse_v1_manifest_accepts_explicit_partial_coverage` | `explicit-partial` |
| `test_parse_v1_manifest_accepts_complete_coverage` | `complete` |
| `test_parse_v1_manifest_coverage_type_is_literal` | `complete` |

`test_parse_v1_step_accepts_optional_metadata`

| Retired test | Retained case id |
| --- | --- |
| `test_parse_v1_step_accepts_optional_warning` | `warning` |
| `test_parse_v1_step_warning_defaults_to_none` | `omitted-metadata-defaults` |
| `test_parse_v1_step_accepts_mandatory_true` | `mandatory-true` |
| `test_parse_v1_step_mandatory_defaults_to_false` | `omitted-metadata-defaults` |
| `test_parse_v1_step_group_and_phase_default_values` | `omitted-metadata-defaults` |
| `test_parse_v1_step_accepts_explicit_group_and_setup_phase` | `explicit-group-and-setup-phase` |

`test_parse_v1_step_rejects_invalid_metadata`

| Retired test | Retained case id |
| --- | --- |
| `test_parse_v1_step_rejects_invalid_phase` | `invalid-phase` |
| `test_parse_v1_step_rejects_invalid_group` | `invalid-group` |
| `test_parse_v1_manifest_rejects_unknown_keys_in_step` | `unknown-step-field` |
| `test_parse_v1_http_step_rejects_psu_fields` | `psu-only-field-on-http-step` |

`test_parse_v1_step_kind_selects_an_http_step`

| Retired test | Retained case id |
| --- | --- |
| `test_parse_v1_step_kind_defaults_to_http` | `omitted` |
| `test_parse_v1_step_explicit_kind_http_is_accepted` | `explicit-http` |

`test_parse_v1_psu_step_rejects_invalid_field`

| Retired test | Retained case id |
| --- | --- |
| `test_parse_v1_psu_step_rejects_invalid_phase` | `invalid-phase` |
| `test_parse_v1_psu_step_rejects_invalid_group` | `invalid-group` |
| `test_parse_v1_psu_step_rejects_request_field` | `http-only-request-field` |
| `test_parse_v1_psu_step_rejects_unknown_mode` | `unknown-mode` |
| `test_parse_v1_psu_step_rejects_non_https_authorization_endpoint` | `non-https-authorization-endpoint` |
| `test_parse_v1_psu_step_rejects_non_https_redirect_uri` | `non-https-redirect-uri` |
| `test_parse_v1_psu_step_rejects_unsupported_placeholder_in_redirect_uri` | `unsupported-redirect-uri-placeholder` |
| `test_parse_v1_psu_step_rejects_placeholder_in_response_type` | `placeholder-in-response-type` |
| `test_parse_v1_psu_step_rejects_placeholder_in_scope` | `placeholder-in-scope` |
| `test_parse_v1_psu_step_rejects_short_literal_state` | `short-literal-state` |
| `test_parse_v1_psu_step_rejects_empty_request_object` | `blank-request-object` |
| `test_parse_v1_psu_step_rejects_unknown_generated_request_object_source` | `unknown-generated-request-object-source` |
| `test_parse_v1_psu_step_rejects_unknown_generated_request_object_key` | `unknown-generated-request-object-key` |
| `test_parse_v1_psu_step_rejects_empty_generated_request_object_openbanking_intent_id` | `blank-generated-request-object-intent-id` |
| `test_parse_v1_psu_step_rejects_secret_bearing_config_placeholder_in_generated_request_object_source` | `secret-bearing-placeholder-in-request-object-source` |
| `test_parse_v1_psu_step_rejects_timeout_seconds` | `participant-configurable-timeout` |
| `test_parse_v1_psu_step_rejects_mandatory_and_optional_both_true` | `mandatory-and-optional-both-true` |
| `test_parse_v1_psu_step_rejects_unknown_key` | `unknown-key` |

`test_parse_v1_http_step_rejects_invalid_signing_policy`

| Retired test | Retained case id |
| --- | --- |
| `test_parse_v1_http_step_rejects_invalid_token_endpoint_auth_policy_type` | `token-endpoint-auth-policy-not-an-object` |
| `test_parse_v1_http_step_rejects_token_endpoint_auth_policy_on_non_post` | `token-endpoint-auth-policy-on-non-post` |
| `test_parse_v1_http_step_rejects_token_endpoint_auth_policy_without_form_body` | `token-endpoint-auth-policy-without-form-body` |
| `test_parse_v1_http_step_rejects_secret_bearing_config_placeholder_in_token_endpoint_auth_policy` | `token-endpoint-auth-policy-secret-bearing-placeholder` |
| `test_parse_v1_http_step_rejects_invalid_detached_jws_policy_type` | `detached-jws-not-an-object` |
| `test_parse_v1_http_step_rejects_unknown_detached_jws_source` | `detached-jws-unknown-source` |
| `test_parse_v1_http_step_rejects_secret_bearing_config_placeholder_in_detached_jws_source` | `detached-jws-secret-bearing-placeholder` |
| `test_parse_v1_http_step_rejects_detached_jws_on_unsupported_method` | `detached-jws-on-unsupported-method` |

Three retired cases merge into an existing case rather than adding one, which
is the entire 207 → 204 manifest test-count change:

- `test_parse_v1_step_warning_defaults_to_none`,
  `test_parse_v1_step_mandatory_defaults_to_false`, and
  `test_parse_v1_step_group_and_phase_default_values` all assert one default of
  the same parsed step. The `omitted-metadata-defaults` case asserts `warning`,
  `mandatory`, `group`, and `phase` together, so all three guarantees hold in
  one case.
- `test_parse_v1_manifest_coverage_type_is_literal` asserted that
  `certification_coverage` is typed `CertificationCoverage`. The coverage matrix
  body performs the same typed assignment, so mypy strict still enforces it.

## Result-file assertion pattern

CLI-facing tests must assert on structured result files, not incidental
side effects. Result JSON assertions should cover:

- `summary` totals and statuses.
- `catalogue` traceability for compiled plan runs.
- Selected endpoint capabilities and applicability decisions.
- `certificationEligibility` reasons.
- Masked request, response, token, signing, and PSU evidence.

## Catalogue regression coverage

The catalogue model is the participant-facing contract. Keep focused coverage
for:

- Shared plan-document parsing and unknown-field rejection for v1 compatibility
  specs and v2 browser/API/CLI documents.
- Duplicate catalogue/test/request/assertion ID detection.
- Applicability filtering by catalogue key or v2 boundary, profile,
  implemented endpoint, and selected endpoint capabilities.
- Required capability defaulting, optional capability inclusion/exclusion, and
  invalid capability rejection.
- Dependency inclusion and deterministic ordering.
- Runtime input requirement validation and sensitive-value snapshots.
- Assertion override non-certifying behaviour.
- Bundled catalogue registry coverage for AIS, PIS, CBPII, VRP, and DCR 3.4, with
  retained cVRP catalogue code covered outside the participant-facing registry.
- Aggregate v2 Read/Write compilation across AIS, PIS, CBPII, and VRP catalogue
  areas, with cVRP rejected from the Open Banking UK boundary.
- Legacy FCS provenance in compliance-scope traceability.

Primary tests:

```bash
DJANGO_DEBUG=true uv run pytest tests/unit/catalogue -v
```

## Execution/API/CLI/UI compiled-plan coverage

Compiled catalogue plans execute through the existing hardened executor path.
Regression coverage should prove that replacing public manifests did not weaken:

- HTTP execution, status/header/body assertions, and response-schema assertions.
- PSU authorisation handoff and headless test helpers.
- FAPI signing, token endpoint auth policy, detached JWS signing, and mTLS checks.
- Masking in result JSON, NDJSON logs, browser downloads, and API log snapshots.
- CLI `--test-plan` validation and rejection of public `--manifest` and
  `--plan-spec`.
- REST canonical test-plan validation and rejection of public `manifest`,
  `planSpec`, and `deselectStepIds`.
- Browser main menu, session-backed draft creation, scheme/specification/version
  selection, security-environment capture before resource groups,
  endpoint/feature drill-down for selected groups, server-rendered dynamic
  feature filtering, locked required capabilities, unchecked optional
  capabilities, grouped business data, generated runtime artifact prompts,
  import/review, safe export, explicit export-with-secrets, launch, and
  collapsed read-only generated-test rows.
- REST and CLI parity for the same capability-selected schemaVersion `1.0`
  contract.
- Run-detail rendering of catalogue traceability evidence from completed result
  JSON.
- DCR CLI, local REST, browser import/review/launch, persisted run lifecycle,
  scenario/case/step statuses, optional-operation skips, safe evidence, and
  certification eligibility.

Focused run:

```bash
DJANGO_DEBUG=true uv run pytest \
  tests/unit/execution \
  tests/unit/api/test_builder_wizard_scope.py \
  tests/unit/api/test_builder_wizard_config.py \
  tests/component \
  -v
```

The command above intentionally omits a marker filter because this coverage
spans both categories; the directories decide which category each test is in.

## Certification validator coverage

The OBL-side validator is an internal-tool surface. It intentionally still
accepts the manifest representation used for the original run and an independent
approved-release policy.

Focused run:

```bash
DJANGO_DEBUG=true uv run pytest \
  tests/unit/execution/test_results.py \
  tests/unit/execution/test_version.py \
  tests/unit/validation/test_approved_releases.py \
  tests/unit/validation/test_model_bank_config.py \
  tests/unit/validation/test_model_bank_config_oauth.py \
  tests/unit/validation/test_model_bank_config_signing.py \
  tests/unit/validation/test_certification_validator.py \
  tests/unit/validation/test_certification_cli.py \
  -v
```

Coverage must include approved versions, unapproved versions, absent policies,
mandatory passed/warn acceptance, mandatory failed/skipped/missing rejection,
malformed report rejection, and Confluence summary rendering. Tests must also
show that mandatory coverage and release approval are recomputed from
independently supplied inputs rather than accepted from any self-assessment in
the report.

These are consistency and certification-readiness tests. Phase 1 tests must not
treat a passing local validation as proof of report authenticity. Tests for
trusted plan/result binding, provenance, and tamper-evident audit history belong
to the Phase 2 portal.

## DCR result gate

The deterministic DCR protocol service covers DCR protocol, orchestration, and
result parsing offline. It is transport independent: protocol and orchestration
behaviour runs in process through an `httpx.MockTransport` at the same client
boundary the product builds, while the loopback mTLS listener is retained only
where transport, TLS, mTLS, certificates, or the product's own mTLS client
configuration are themselves under test (the `dcr_test_service` fixture).
Cryptographic material is generated once per session and shared because it is
immutable; mutable protocol state is created fresh per test.

Run:

```bash
uv run pytest \
  tests/unit/execution/test_dcr_execution.py \
  tests/unit/execution/test_dcr_registration_responses.py \
  tests/unit/execution/test_dcr_protocol_service.py \
  tests/component/execution/test_dcr_product_flows.py \
  tests/component/protocol/test_dcr_mtls.py \
  -v
uv run python -m conformance.result_gate out/test-results.json
```

The result gate reads JSON only and requires a passing aggregate, zero reported
failed steps, and no failed scenario/case/step in `catalogue.traceGroups`.
Expected endpoint-not-selected skips are permitted. There is no live Ozone or
model-bank workflow that runs this gate today; live verification against a
real Ozone/model-bank environment is out of scope for the supported offline
suite and must not be described as running in CI unless it actually does.

## Code quality and coverage targets

| Tool | Role |
| --- | --- |
| `ruff` | Linting, import sorting, and formatting checks. |
| `mypy` | Strict static type checking. |
| `pytest-cov` | Coverage reporting and 80% minimum enforcement. |
| `detect-secrets` | Secret scanning through the pre-commit hook and CI. |

Run selectively while iterating, then run `make check` before pushing.
