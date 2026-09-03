"""Tests for the typed Open Banking DCR 3.4 execution adapter."""

from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import Mock, patch

import httpx
import pytest
from joserfc import jwk, jwt

from conformance import dcr_execution
from conformance.approved_releases import ApprovedReleasePolicy
from conformance.catalogue import CompiledTestPlan, PlanDocumentV2, compile_test_plan_document, parse_test_plan_document
from conformance.catalogues.dcr import DCR_3_4_CATALOGUE
from conformance.dcr_execution import (
    DcrCatalogueExecutionAdapter,
    DcrExecutionError,
    DcrScenarioState,
    build_dcr_mtls_client,
    certificate_subject_dn,
    validate_dcr_subject_dn,
)
from conformance.execution_log import BufferedExecutionLogger, NullExecutionLogger
from conformance.executor import run_compiled_test_plan
from conformance.json_types import JsonObject, JsonValue
from conformance.masking import MASKED_VALUE
from conformance.model_bank_config import ConfigError
from conformance.plan_configuration import (
    DcrPlanConfiguration,
    dcr_execution_runtime_inputs,
    parse_dcr_execution_runtime_inputs,
    parse_dcr_plan_configuration,
)
from conformance.test_plan_validation import PreparedTestPlan, prepare_test_plan_for_run
from tests.dcr_test_service import DcrTestService

_FIXED_TIME = datetime.fromtimestamp(1_800_000_000, tz=UTC)
_MANDATORY_POST_CASE_IDS = (
    "DCR-001-C01",
    "DCR-002-C01",
    "DCR-002-C02",
    "DCR-004-C01",
    "DCR-004-C02",
    "DCR-004-C03",
    "DCR-004-C04",
    "DCR-004-C05",
    "DCR-011-C01",
)
_MANDATORY_POST_STEP_IDS = (
    "DCR-001-C01-S01",
    "DCR-002-C01-S01",
    "DCR-002-C01-S02",
    "DCR-002-C01-S03",
    "DCR-002-C01-S04",
    "DCR-002-C02-S01",
    "DCR-004-C01-S01",
    "DCR-004-C01-S02",
    "DCR-004-C01-S03",
    "DCR-004-C02-S01",
    "DCR-004-C02-S02",
    "DCR-004-C02-S03",
    "DCR-004-C03-S01",
    "DCR-004-C03-S02",
    "DCR-004-C03-S03",
    "DCR-004-C04-S01",
    "DCR-004-C04-S02",
    "DCR-004-C04-S03",
    "DCR-004-C05-S01",
    "DCR-004-C05-S02",
    "DCR-004-C05-S03",
    "DCR-011-C01-S01",
    "DCR-011-C01-S02",
    "DCR-011-C01-S03",
    "DCR-011-C01-S04",
)


def _compiled_plan(methods: Iterable[str]) -> CompiledTestPlan:
    """Compile a DCR plan selecting the requested endpoint methods.

    Args:
        methods: Direct registration/management methods.

    Returns:
        Compiled deterministic DCR catalogue plan.
    """
    endpoints = [
        {
            "method": method,
            "path": "/register" if method == "POST" else "/register/{ClientId}",
            "required": method == "POST",
            "locked": method == "POST",
        }
        for method in methods
    ]
    document = parse_test_plan_document(
        {
            "schemaVersion": "1.0",
            "specification": {
                "family": "OBL_DCR",
                "scheme": "open-banking-uk",
                "name": "dynamic-client-registration",
                "version": "3.4",
            },
            "securityEnvironment": {"discoveryUrl": "https://aspsp.example.test/.well-known/openid-configuration"},
            "dynamicClientRegistration": {},
            "endpoints": endpoints,
            "metadata": {},
        }
    )
    assert isinstance(document, PlanDocumentV2)
    return compile_test_plan_document(document, (DCR_3_4_CATALOGUE,))


def _config(service: DcrTestService, root: Path, auth_method: str = "tls_client_auth") -> DcrPlanConfiguration:
    """Build executable typed configuration for the deterministic service.

    Args:
        service: Running mTLS DCR service.
        root: Directory receiving the SSA file.
        auth_method: Token endpoint client-auth method.

    Returns:
        Validated DCR plan configuration.
    """
    ssa_path = root / "ssa.jwt"
    ssa_path.write_text(service.protocol.software_statement_assertion, encoding="utf-8")
    return parse_dcr_plan_configuration(
        {
            "discoveryUrl": service.discovery_url,
            "clientAuthMethod": auth_method,
            "signingPrivateKeyPath": str(service.protocol.signing_private_key_path),
            "signingKeyId": "fixture-signing-key",
            "mtls": {
                "certificatePath": str(service.tls.client_certificate_path),
                "privateKeyPath": str(service.tls.client_private_key_path),
                "caBundlePath": str(service.tls.ca_certificate_path),
            },
        },
        {
            "softwareStatementAssertionPath": str(ssa_path),
            "registrationAudience": "aspsp123",
        },
        {},
    )


def _prepared_post_plan(service: DcrTestService, root: Path) -> PreparedTestPlan:
    """Prepare a canonical mandatory POST-only DCR plan for execution.

    Args:
        service: Running deterministic DCR service.
        root: Directory receiving the plan's SSA reference.

    Returns:
        Validated, compiled, and trace-safe canonical execution bundle.
    """
    config = _config(service, root)
    shared = config.shared
    dcr = config.dynamic_client_registration
    return prepare_test_plan_for_run(
        {
            "schemaVersion": "1.0",
            "specification": {
                "family": "OBL_DCR",
                "scheme": "open-banking-uk",
                "name": "dynamic-client-registration",
                "version": "3.4",
            },
            "executionMode": "certification",
            "securityEnvironment": {
                "discoveryUrl": shared.discovery_url,
                "clientAuthMethod": shared.client_auth_method,
                "signingPrivateKeyPath": str(shared.signing.private_key_path),
                "signingKeyId": shared.signing.key_id,
                "mtls": {
                    "enabled": True,
                    "certificatePath": str(shared.mtls.client_certificate_path),
                    "privateKeyPath": str(shared.mtls.client_private_key_path),
                    "caBundlePath": str(shared.mtls.ca_bundle_path),
                },
            },
            "endpoints": [
                {
                    "method": "POST",
                    "path": "/register",
                    "operationId": "RegisterClient",
                    "required": True,
                    "locked": True,
                }
            ],
            "dynamicClientRegistration": {
                "softwareStatementAssertionPath": str(dcr.software_statement_assertion_path),
                "registrationAudience": dcr.registration_audience,
                "disableKeepAlive": False,
            },
            "metadata": {"aspspName": "Deterministic DCR service"},
        },
        base_dir=root,
    )


def _adapter(
    service: DcrTestService,
    root: Path,
    *,
    methods: Iterable[str] = ("POST",),
    auth_method: str = "tls_client_auth",
    logger: BufferedExecutionLogger | NullExecutionLogger | None = None,
    client: httpx.Client | None = None,
    approved_release_policy: ApprovedReleasePolicy | None = None,
) -> DcrCatalogueExecutionAdapter:
    """Build an adapter with deterministic JOSE time and ids.

    Args:
        service: Running deterministic DCR service.
        root: Directory receiving runtime references.
        methods: Selected direct DCR endpoint methods.
        auth_method: Token endpoint client-auth method.
        logger: Optional execution logger.
        client: Optional caller-owned HTTP client.
        approved_release_policy: Optional certification release policy.

    Returns:
        Configured execution adapter.
    """
    ids = iter(f"test-jti-{index}" for index in range(1000))
    return DcrCatalogueExecutionAdapter(
        compiled_plan=_compiled_plan(methods),
        config=_config(service, root, auth_method),
        execution_logger=logger or NullExecutionLogger(),
        client=client,
        clock=lambda: _FIXED_TIME,
        jwt_id_factory=lambda: next(ids),
        approved_release_policy=approved_release_policy,
    )


def _registration_validation_fixture(
    service: DcrTestService,
    root: Path,
    *,
    request_redirects: list[str] | None = None,
    request_grants: list[str] | None = None,
    request_response_types: list[str] | None = None,
) -> tuple[DcrCatalogueExecutionAdapter, JsonObject, DcrScenarioState]:
    """Build response-validation state with a two-URI SSA master set.

    Args:
        service: Running deterministic DCR service.
        root: Directory receiving runtime references.
        request_redirects: Redirect URIs requested by the client.
        request_grants: Grant types requested by the client.
        request_response_types: Response types requested by the client.

    Returns:
        Adapter, valid response body, and corresponding scenario state.
    """
    master_redirects = [
        "https://client.example.test/callback",
        "https://client.example.test/alternate-callback",
    ]
    software_statement = jwt.encode(
        {"alg": "PS256", "kid": "fixture-signing-key"},
        {
            "software_id": "fixturesoftwareid",
            "software_redirect_uris": master_redirects,
        },
        service.protocol.signing_key,
        algorithms=["PS256"],
    )
    with service.client() as client:
        adapter = _adapter(service, root, client=client)
        adapter._require_discovery()  # noqa: SLF001 - focused validation-unit fixture.
        _, claims = adapter.build_registration_jose()
    effective_redirects = request_redirects or master_redirects[:1]
    effective_grants = request_grants or ["client_credentials", "authorization_code"]
    effective_response_types = request_response_types or ["code id_token"]
    claims["software_statement"] = software_statement
    claims["redirect_uris"] = cast("JsonValue", effective_redirects)
    claims["grant_types"] = cast("JsonValue", effective_grants)
    claims["response_types"] = cast("JsonValue", effective_response_types)
    body: JsonObject = {
        "client_id": "response-semantics-client",
        "client_secret": "response-semantics-secret",  # pragma: allowlist secret
        "client_id_issued_at": 1_800_000_000,
        "client_secret_expires_at": 0,
        "registration_access_token": "response-semantics-registration-token",
        "registration_client_uri": f"{service.registration_endpoint}/response-semantics-client",
        "application_type": claims["application_type"],
        "redirect_uris": cast("JsonValue", list(effective_redirects)),
        "grant_types": cast("JsonValue", list(effective_grants)),
        "response_types": cast("JsonValue", list(effective_response_types)),
        "scope": claims["scope"],
        "software_statement": software_statement,
        "software_id": "fixturesoftwareid",
        "id_token_signed_response_alg": claims["id_token_signed_response_alg"],
        "request_object_signing_alg": claims["request_object_signing_alg"],
        "token_endpoint_auth_method": claims["token_endpoint_auth_method"],
        "tls_client_auth_subject_dn": claims["tls_client_auth_subject_dn"],
    }
    return adapter, body, DcrScenarioState(registration_claims=claims)


@pytest.mark.unit
def test_dcr_ca_bundle_augments_default_trust_store() -> None:
    """A participant CA bundle must not replace trusted public certificate authorities."""
    ca_bundle_path = Path("/configured-ca.pem")
    config = cast(
        "DcrPlanConfiguration",
        SimpleNamespace(
            shared=SimpleNamespace(
                mtls=SimpleNamespace(
                    client_certificate_path=Path("/client.pem"),
                    client_private_key_path=Path("/client.key"),
                    ca_bundle_path=ca_bundle_path,
                )
            ),
            dynamic_client_registration=SimpleNamespace(disable_keep_alive=False),
        ),
    )
    context = Mock()

    with (
        patch("conformance.dcr_execution.ssl.create_default_context", return_value=context) as create_context,
        patch("conformance.dcr_execution.httpx.Client") as client,
    ):
        build_dcr_mtls_client(config)

    create_context.assert_called_once_with()
    context.load_verify_locations.assert_called_once_with(cafile=str(ca_bundle_path))
    assert client.call_args.kwargs["verify"] is context


@pytest.mark.unit
def test_dcr_jwks_accepts_rfc7517_extension_use_value() -> None:
    """An Open Banking Directory transport key with ``use=tls`` remains a valid public JWK."""
    public_key = jwk.generate_key("RSA", 2048, private=True, auto_kid=False).as_dict(is_private=False)
    public_key["use"] = "tls"

    dcr_execution._validate_jwks(  # noqa: SLF001 - focused RFC 7517 extension-use validation.
        cast("JsonObject", {"keys": [public_key]})
    )


@pytest.mark.integration
def test_post_primitives_execute_raw_jose_token_and_negative_variants(
    dcr_test_service: DcrTestService,
    tmp_path: Path,
) -> None:
    """POST-only execution passes discovery, registration, token, and negatives."""
    result = _adapter(dcr_test_service, tmp_path).run()

    assert result.status == "passed"
    compiled_plan = result.compiled_plan
    assert compiled_plan is not None
    assert len(result.steps) == sum(len(case.execution_steps) for case in compiled_plan.test_cases)
    assert all(step.status == "passed" for step in result.steps)
    registration_events = [event for event in dcr_test_service.events if event.path == "/register"]
    assert registration_events
    assert all(event.content_type == "application/jose" for event in registration_events)
    assert any(
        event.path == "/token" and event.content_type == "application/x-www-form-urlencoded"
        for event in dcr_test_service.events
    )


@pytest.mark.integration
def test_canonical_post_only_plan_executes_exact_mandatory_hierarchy_and_statuses(
    dcr_test_service: DcrTestService,
    tmp_path: Path,
) -> None:
    """Canonical preparation and shared execution preserve mandatory DCR evidence."""
    prepared = _prepared_post_plan(dcr_test_service, tmp_path)
    ids = iter(f"canonical-jti-{index}" for index in range(1000))

    with httpx.Client() as unused_read_write_client:
        result = run_compiled_test_plan(
            prepared.compiled_plan,
            runtime_inputs=prepared.runtime_inputs,
            runtime_input_base_dir=tmp_path,
            client=unused_read_write_client,
            dcr_clock=lambda: _FIXED_TIME,
            dcr_jwt_id_factory=lambda: next(ids),
        )

    assert prepared.validation.valid is True
    assert prepared.compiled_plan.traceability.generated_test_case_ids == _MANDATORY_POST_CASE_IDS
    compiled_provenance = prepared.compiled_plan.traceability.provenance
    assert compiled_provenance is not None
    assert compiled_provenance.commit == "cc00a0065494e8e180c915621b9996bc2259ec8d"  # pragma: allowlist secret
    assert tuple(step.name for step in result.steps) == _MANDATORY_POST_STEP_IDS
    assert tuple(step.status for step in result.steps) == ("passed",) * 25
    assert tuple(step.status_code for step in result.steps) == (
        None,
        None,
        201,
        201,
        None,
        200,
        None,
        400,
        400,
        None,
        400,
        400,
        None,
        400,
        400,
        None,
        400,
        400,
        None,
        400,
        400,
        None,
        400,
        400,
        400,
    )
    assert result.status == "passed"
    assert [(event.method, event.path, event.status_code) for event in dcr_test_service.events] == [
        ("GET", "/.well-known/openid-configuration", 200),
        ("GET", "/jwks", 200),
        ("POST", "/register", 201),
        ("POST", "/token", 200),
        *(("POST", "/register", 400) for _ in range(6)),
    ]

    rendered = result.to_json_object()
    catalogue = rendered["catalogue"]
    assert isinstance(catalogue, dict)
    assert catalogue["selectedEndpoints"] == [
        {
            "method": "POST",
            "path": "/register",
            "resourceGroup": "dynamic-client-registration",
            "operationId": "RegisterClient",
        }
    ]
    assert catalogue["selectedCapabilities"] == [
        {
            "method": "POST",
            "path": "/register",
            "capabilityId": "dcr.registration.post",
            "label": "Client registration",
            "required": True,
        }
    ]
    provenance = catalogue["provenance"]
    assert isinstance(provenance, dict)
    assert provenance["release"] == "v1.4.0"
    assert provenance["commit"] == "cc00a0065494e8e180c915621b9996bc2259ec8d"  # pragma: allowlist secret
    trace_groups = catalogue["traceGroups"]
    assert isinstance(trace_groups, list)
    assert [group["traceGroupId"] for group in trace_groups if isinstance(group, dict)] == [
        "DCR-001",
        "DCR-002",
        "DCR-003",
        "DCR-004",
        "DCR-005",
        "DCR-007",
        "DCR-008",
        "DCR-009",
        "DCR-010",
        "DCR-011",
    ]
    skipped_cases = [
        case
        for group in trace_groups
        if isinstance(group, dict)
        for case in cast(list[JsonObject], group["testCases"])
        if case.get("status") == "skipped"
    ]
    assert skipped_cases
    assert all(case["skipReason"] == "endpoint-not-selected" for case in skipped_cases)
    assert all(step["status"] == "skipped" for case in skipped_cases for step in cast(list[JsonObject], case["steps"]))
    assert [
        case["testCaseId"]
        for group in trace_groups
        if isinstance(group, dict)
        for case in cast(list[JsonObject], group["testCases"])
        if case.get("status") != "skipped"
    ] == list(_MANDATORY_POST_CASE_IDS)
    assert [
        step["stepId"]
        for group in trace_groups
        if isinstance(group, dict)
        for case in cast(list[JsonObject], group["testCases"])
        if case.get("status") != "skipped"
        for step in cast(list[JsonObject], case["steps"])
    ] == list(_MANDATORY_POST_STEP_IDS)


@pytest.mark.integration
def test_failed_registration_prerequisite_explicitly_skips_token_dependency(
    dcr_test_service: DcrTestService,
    tmp_path: Path,
) -> None:
    """A failed mandatory registration cannot produce a false token pass."""
    prepared = _prepared_post_plan(dcr_test_service, tmp_path)
    dcr_test_service.fail_next_registration()
    ids = iter(f"failure-jti-{index}" for index in range(1000))
    logger = BufferedExecutionLogger(run_id="dcr-failure-mask-test")

    with httpx.Client() as unused_read_write_client:
        result = run_compiled_test_plan(
            prepared.compiled_plan,
            runtime_inputs=prepared.runtime_inputs,
            runtime_input_base_dir=tmp_path,
            client=unused_read_write_client,
            execution_logger=logger,
            dcr_clock=lambda: _FIXED_TIME,
            dcr_jwt_id_factory=lambda: next(ids),
        )

    by_name = {step.name: step for step in result.steps}
    assert result.status == "failed"
    assert by_name["DCR-002-C01-S02"].status_code == 500
    assert by_name["DCR-002-C01-S03"].status == "failed"
    assert by_name["DCR-002-C01-S04"].status == "skipped"
    assert by_name["DCR-002-C02-S01"].status == "skipped"
    assert by_name["DCR-002-C02-S01"].details["skipReason"] == "failed-prerequisite"
    assert not any(event.path == "/token" for event in dcr_test_service.events)
    assert by_name["DCR-004-C01-S03"].status == "passed"
    persisted = (
        json.dumps(prepared.snapshot) + json.dumps(result.to_json_object()) + logger.to_ndjson_bytes().decode("utf-8")
    )
    assert dcr_test_service.protocol.software_statement_assertion not in persisted
    assert "fixture-client-material-" not in persisted
    assert "fixture-registration-token-" not in persisted
    assert "signedRegistrationJose" in persisted
    assert MASKED_VALUE in persisted


@pytest.mark.integration
@pytest.mark.parametrize(
    "auth_method",
    ["tls_client_auth", "private_key_jwt", "client_secret_jwt", "client_secret_basic"],
)
def test_all_executable_token_auth_methods_run_against_deterministic_service(
    dcr_test_service: DcrTestService,
    tmp_path: Path,
    auth_method: str,
) -> None:
    """Every advertised executable auth method completes the generated token call."""
    result = _adapter(dcr_test_service, tmp_path, auth_method=auth_method).run()

    assert result.status == "passed"
    assert any(step.name == "DCR-002-C02-S01" and step.status == "passed" for step in result.steps)


@pytest.mark.integration
def test_passing_dcr_result_honours_approved_release_certification_policy(
    dcr_test_service: DcrTestService,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Complete mandatory DCR coverage is eligible with an approved tool release."""
    monkeypatch.setenv("CONFORMANCE_TOOL_VERSION", "3.4.0")
    policy = ApprovedReleasePolicy(schema_version="v1", approved_tool_versions=("3.4.0",))

    result = _adapter(
        dcr_test_service,
        tmp_path,
        approved_release_policy=policy,
    ).run()

    eligibility = cast(JsonObject, result.to_json_object()["certificationEligibility"])
    assert eligibility["eligible"] is True
    assert eligibility["mandatoryTotal"] == 25
    assert eligibility["mandatoryPassed"] == 25
    assert eligibility["mandatoryFailed"] == 0
    assert eligibility["mandatorySkipped"] == 0


@pytest.mark.integration
@pytest.mark.parametrize("auth_method", ["tls_client_auth", "private_key_jwt"])
@pytest.mark.parametrize(
    ("response_method", "methods"),
    [
        ("POST", ("POST",)),
        ("GET", ("POST", "GET")),
        ("PUT", ("POST", "PUT")),
    ],
)
def test_secretless_auth_accepts_optional_fields_omitted_from_success_responses(
    tmp_path: Path,
    auth_method: str,
    response_method: str,
    methods: tuple[str, ...],
) -> None:
    """POST, GET, and PUT remain usable when legacy-optional fields are absent."""
    optional_fields = frozenset({"client_id_issued_at", "client_secret_expires_at", "client_secret"})
    with DcrTestService(
        tmp_path / f"optional-{response_method.lower()}-{auth_method}",
        response_omissions={response_method: optional_fields},
    ) as service:
        result = _adapter(service, tmp_path, methods=methods, auth_method=auth_method).run()

    assert result.status == "passed"
    if response_method == "POST":
        parse_step = next(step for step in result.steps if step.name == "DCR-002-C01-S04")
        assert parse_step.details["clientSecretCaptured"] is False
    assert any(step.name == "DCR-002-C02-S01" and step.status == "passed" for step in result.steps)


@pytest.mark.integration
@pytest.mark.parametrize(
    ("response_method", "methods", "failure_step"),
    [
        ("POST", ("POST",), "DCR-002-C01-S04"),
        ("GET", ("POST", "GET"), "DCR-005-C03-S03"),
        ("PUT", ("POST", "PUT"), "DCR-008-C03-S02"),
    ],
)
def test_success_response_surfaces_reject_present_optional_field_with_wrong_type(
    tmp_path: Path,
    response_method: str,
    methods: tuple[str, ...],
    failure_step: str,
) -> None:
    """POST, GET, and PUT validate a supplied optional timestamp's type."""
    with DcrTestService(
        tmp_path / f"wrong-type-{response_method.lower()}",
        response_overrides={response_method: {"client_id_issued_at": "not-an-integer"}},
    ) as service:
        result = _adapter(service, tmp_path, methods=methods).run()

    by_name = {step.name: step for step in result.steps}
    assert result.status == "failed"
    assert by_name[failure_step].status == "failed"
    assert "client_id_issued_at must be an integer" in by_name[failure_step].message


@pytest.mark.integration
@pytest.mark.parametrize("auth_method", ["client_secret_jwt", "client_secret_basic"])
def test_secret_auth_fails_at_token_prerequisite_and_skips_dependents_when_secret_omitted(
    tmp_path: Path,
    auth_method: str,
) -> None:
    """Secret auth reports the missing prerequisite and never executes dependents."""
    with DcrTestService(
        tmp_path / f"missing-secret-{auth_method}",
        response_omissions={"POST": frozenset({"client_secret"})},
    ) as service:
        result = _adapter(
            service,
            tmp_path,
            methods=("POST", "GET", "PUT", "DELETE"),
            auth_method=auth_method,
        ).run()

    by_name = {step.name: step for step in result.steps}
    token_step = by_name["DCR-002-C02-S01"]
    assert result.status == "failed"
    assert token_step.status == "failed"
    assert token_step.message == (
        f"Registration response omitted client_secret required for {auth_method} token authentication"
    )
    assert by_name["DCR-002-C03-S01"].status == "skipped"
    assert by_name["DCR-005-C03-S01"].status == "skipped"
    assert by_name["DCR-008-C03-S01"].status == "skipped"
    assert not any(event.path == "/token" for event in service.events)


@pytest.mark.integration
def test_full_management_state_is_sequential_isolated_and_cleaned(
    dcr_test_service: DcrTestService,
    tmp_path: Path,
) -> None:
    """Full execution passes GET/PUT/DELETE and deleted-client transitions."""
    result = _adapter(
        dcr_test_service,
        tmp_path,
        methods=("POST", "GET", "PUT", "DELETE"),
    ).run()

    assert result.status == "passed"
    assert len(result.steps) == 79
    compiled_plan = result.compiled_plan
    assert compiled_plan is not None
    assert [step.name for step in result.steps] == [
        step.step_id for case in compiled_plan.test_cases for step in case.execution_steps
    ]
    assert all(step.status == "passed" for step in result.steps)
    assert {event.method for event in dcr_test_service.events if event.path.startswith("/register/")} == {
        "GET",
        "PUT",
        "DELETE",
    }
    management_statuses = {
        method: {
            event.status_code
            for event in dcr_test_service.events
            if event.path.startswith("/register/") and event.method == method
        }
        for method in ("GET", "PUT", "DELETE")
    }
    assert management_statuses == {"GET": {200, 401}, "PUT": {200, 401}, "DELETE": {204}}
    by_name = {step.name: step for step in result.steps}
    assert by_name["DCR-005-C03-S02"].status_code == 200
    assert by_name["DCR-007-C02-S03"].status_code == 401
    assert by_name["DCR-008-C03-S03"].status_code == 200
    assert by_name["DCR-009-C04-S03"].status_code == 401
    assert by_name["DCR-010-C04-S02"].status_code == 401
    rendered = result.to_json_object()
    catalogue = cast(JsonObject, rendered["catalogue"])
    assert catalogue["skippedTestCaseIds"] == []
    trace_groups = cast(list[JsonObject], catalogue["traceGroups"])
    assert len(trace_groups) == 10
    assert sum(len(cast(list[JsonObject], group["testCases"])) for group in trace_groups) == 34
    assert (
        sum(
            len(cast(list[JsonObject], case["steps"]))
            for group in trace_groups
            for case in cast(list[JsonObject], group["testCases"])
        )
        == 79
    )
    snapshot_clients = dcr_test_service.snapshot()["clients"]
    assert isinstance(snapshot_clients, list)
    assert all(isinstance(client, dict) and client["deleted"] is True for client in snapshot_clients)


@pytest.mark.integration
@pytest.mark.parametrize(
    ("methods", "expected_management_methods"),
    [
        (("POST",), set()),
        (("POST", "GET"), {"GET"}),
        (("POST", "PUT"), {"PUT"}),
        (("POST", "DELETE"), {"DELETE"}),
        (("POST", "GET", "PUT"), {"GET", "PUT"}),
    ],
)
def test_endpoint_subsets_never_execute_unselected_management_methods(
    dcr_test_service: DcrTestService,
    tmp_path: Path,
    methods: tuple[str, ...],
    expected_management_methods: set[str],
) -> None:
    """Endpoint gates constrain actual management traffic, including cleanup."""
    result = _adapter(dcr_test_service, tmp_path, methods=methods).run()
    management_methods = {event.method for event in dcr_test_service.events if event.path.startswith("/register/")}

    assert result.status == "passed"
    assert management_methods == expected_management_methods
    rendered = result.to_json_object()
    catalogue = cast(JsonObject, rendered["catalogue"])
    skipped_ids = cast(list[JsonValue], catalogue["skippedTestCaseIds"])
    assert bool(skipped_ids) is (len(methods) < 4)


@pytest.mark.integration
def test_selected_but_unsupported_get_fails_and_skips_dependent_assertions(
    tmp_path: Path,
) -> None:
    """A selected optional endpoint failure is primary and later steps are skipped."""
    with DcrTestService(tmp_path / "no-management", management_methods=frozenset()) as service:
        result = _adapter(service, tmp_path, methods=("POST", "GET")).run()

    by_name = {step.name: step for step in result.steps}
    assert result.status == "failed"
    assert by_name["DCR-005-C03-S01"].status_code == 405
    assert by_name["DCR-005-C03-S02"].status == "failed"
    assert by_name["DCR-005-C03-S03"].status == "skipped"
    assert {event.method for event in service.events if event.path.startswith("/register/")} == {"GET"}


@pytest.mark.integration
def test_adapter_masks_every_dcr_credential_evidence_surface(
    dcr_test_service: DcrTestService,
    tmp_path: Path,
) -> None:
    """Results and execution logs never persist SSA, JOSE, secrets, or tokens."""
    logger = BufferedExecutionLogger(run_id="dcr-mask-test")
    result = _adapter(dcr_test_service, tmp_path, logger=logger).run()
    serialized = json.dumps(result.to_json_object()) + logger.to_ndjson_bytes().decode()

    assert dcr_test_service.protocol.software_statement_assertion not in serialized
    assert "fixture-client-material-" not in serialized
    assert "fixture-registration-token-" not in serialized
    assert "fixture-grant-token-" not in serialized
    assert '"client_secret": "***"' in serialized
    assert '"registration_access_token": "***"' in serialized
    assert '"signedRegistrationJose": "***"' in serialized


@pytest.mark.unit
def test_discovery_failure_skips_every_dependent_catalogue_step(
    dcr_test_service: DcrTestService,
    tmp_path: Path,
) -> None:
    """An unsafe discovery endpoint fails once and explicitly skips dependents."""
    requests: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        """Return discovery metadata containing an insecure registration URL.

        Args:
            request: Outbound adapter request.

        Returns:
            Deterministic mock response.
        """
        requests.append(request.url.path)
        return httpx.Response(
            200,
            json={
                "issuer": "https://issuer.example.test",
                "registration_endpoint": "http://registration.example.test/register",
                "token_endpoint": "https://issuer.example.test/token",
                "jwks_uri": "https://issuer.example.test/jwks",
                "token_endpoint_auth_methods_supported": ["tls_client_auth"],
                "token_endpoint_auth_signing_alg_values_supported": ["PS256"],
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        result = _adapter(
            dcr_test_service,
            tmp_path,
            methods=("POST", "GET", "PUT", "DELETE"),
            client=client,
        ).run()

    assert result.steps[0].status == "failed"
    assert all(step.status == "skipped" for step in result.steps[1:])
    assert requests == ["/.well-known/openid-configuration"]


@pytest.mark.unit
def test_registration_response_array_order_is_semantically_irrelevant(
    dcr_test_service: DcrTestService,
    tmp_path: Path,
) -> None:
    """Reordered set-valued registration metadata remains conformant."""
    redirects = [
        "https://client.example.test/callback",
        "https://client.example.test/alternate-callback",
    ]
    adapter, body, state = _registration_validation_fixture(
        dcr_test_service,
        tmp_path,
        request_redirects=redirects,
        request_response_types=["code", "code id_token"],
    )
    body["redirect_uris"] = list(reversed(redirects))
    body["grant_types"] = ["authorization_code", "client_credentials"]
    body["response_types"] = ["code id_token", "code"]

    adapter._validate_registration_response(  # noqa: SLF001 - directly tests the reviewed validation boundary.
        body,
        state,
        require_consistency=True,
    )


@pytest.mark.unit
def test_registration_response_accepts_omitted_optional_issuance_and_secret_fields(
    dcr_test_service: DcrTestService,
    tmp_path: Path,
) -> None:
    """Legacy-optional issuance timestamps and client secret may be omitted."""
    adapter, body, state = _registration_validation_fixture(dcr_test_service, tmp_path)
    for field in ("client_id_issued_at", "client_secret_expires_at", "client_secret"):
        body.pop(field)

    adapter._validate_registration_response(  # noqa: SLF001 - directly tests the reviewed validation boundary.
        body,
        state,
        require_consistency=True,
    )


@pytest.mark.unit
def test_registration_response_allows_rfc7592_management_fields_to_be_omitted(
    dcr_test_service: DcrTestService,
    tmp_path: Path,
) -> None:
    """Open Banking responses need not include RFC 7592 management credentials."""
    adapter, body, state = _registration_validation_fixture(dcr_test_service, tmp_path)
    body.pop("registration_access_token")
    body.pop("registration_client_uri")

    adapter._validate_registration_response(  # noqa: SLF001 - directly tests the reviewed validation boundary.
        body,
        state,
        require_consistency=True,
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("client_id_issued_at", "1800000000"),
        ("client_id_issued_at", True),
        ("client_secret_expires_at", "0"),
        ("client_secret_expires_at", False),
        ("client_secret", None),
        ("client_secret", 7),
        ("client_secret", ""),
        ("client_secret", "s" * 37),
    ],
)
def test_registration_response_rejects_invalid_present_optional_credential_fields(
    dcr_test_service: DcrTestService,
    tmp_path: Path,
    field: str,
    replacement: JsonValue,
) -> None:
    """Present optional issuance and secret fields retain type and size rules."""
    adapter, body, state = _registration_validation_fixture(dcr_test_service, tmp_path)
    body[field] = replacement

    with pytest.raises(DcrExecutionError, match=field):
        adapter._validate_registration_response(  # noqa: SLF001 - directly tests the reviewed validation boundary.
            body,
            state,
            require_consistency=True,
        )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("field", "permitted"),
    [
        ("redirect_uris", False),
        ("grant_types", False),
        ("response_types", True),
    ],
)
def test_registration_response_array_omission_follows_dcr_occurrence(
    dcr_test_service: DcrTestService,
    tmp_path: Path,
    field: str,
    permitted: bool,
) -> None:
    """Only the optional DCR response-types array may be omitted."""
    adapter, body, state = _registration_validation_fixture(dcr_test_service, tmp_path)
    body.pop(field)

    if permitted:
        adapter._validate_registration_response(  # noqa: SLF001 - directly tests the reviewed validation boundary.
            body,
            state,
            require_consistency=True,
        )
    else:
        with pytest.raises(DcrExecutionError, match=field):
            adapter._validate_registration_response(  # noqa: SLF001 - directly tests the reviewed validation boundary.
                body,
                state,
                require_consistency=True,
            )


@pytest.mark.unit
def test_registration_response_nonempty_array_subsets_are_permitted(
    dcr_test_service: DcrTestService,
    tmp_path: Path,
) -> None:
    """Supported subsets describe the metadata actually registered by the AS."""
    adapter, body, state = _registration_validation_fixture(
        dcr_test_service,
        tmp_path,
        request_redirects=[
            "https://client.example.test/callback",
            "https://client.example.test/alternate-callback",
        ],
        request_response_types=["code", "code id_token"],
    )
    body["redirect_uris"] = ["https://client.example.test/callback"]
    body["grant_types"] = ["client_credentials"]
    body["response_types"] = ["code"]

    adapter._validate_registration_response(  # noqa: SLF001 - directly tests the reviewed validation boundary.
        body,
        state,
        require_consistency=True,
    )


@pytest.mark.unit
def test_registration_response_allows_software_statement_replacement(
    dcr_test_service: DcrTestService,
    tmp_path: Path,
) -> None:
    """The returned software statement is validated by shape, not request equality."""
    adapter, body, state = _registration_validation_fixture(dcr_test_service, tmp_path)
    body["software_statement"] = "server-registered-software-statement"

    adapter._validate_registration_response(  # noqa: SLF001 - directly tests the reviewed validation boundary.
        body,
        state,
        require_consistency=True,
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("field", "permitted"),
    [
        ("redirect_uris", False),
        ("grant_types", False),
        ("response_types", True),
    ],
)
def test_registration_response_empty_array_subsets_follow_dcr_cardinality(
    dcr_test_service: DcrTestService,
    tmp_path: Path,
    field: str,
    permitted: bool,
) -> None:
    """Empty subsets are accepted only for the zero-cardinality response field."""
    adapter, body, state = _registration_validation_fixture(dcr_test_service, tmp_path)
    body[field] = []

    if permitted:
        adapter._validate_registration_response(  # noqa: SLF001 - directly tests the reviewed validation boundary.
            body,
            state,
            require_consistency=True,
        )
    else:
        with pytest.raises(DcrExecutionError, match=field):
            adapter._validate_registration_response(  # noqa: SLF001 - directly tests the reviewed validation boundary.
                body,
                state,
                require_consistency=True,
            )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("field", "replacement", "permitted"),
    [
        ("redirect_uris", ["https://client.example.test/alternate-callback"], True),
        ("redirect_uris", ["https://outside-ssa.example.test/callback"], False),
        ("grant_types", ["urn:openid:params:grant-type:ciba"], True),
        ("grant_types", ["urn:ietf:params:oauth:grant-type:jwt-bearer"], True),
        ("grant_types", ["unsupported_grant"], False),
        ("response_types", ["code"], True),
        ("response_types", ["token"], False),
    ],
)
def test_registration_response_array_replacement_follows_normative_constraints(
    dcr_test_service: DcrTestService,
    tmp_path: Path,
    field: str,
    replacement: list[str],
    permitted: bool,
) -> None:
    """AS replacements are accepted only within DCR values and the SSA URI set."""
    adapter, body, state = _registration_validation_fixture(dcr_test_service, tmp_path)
    body[field] = cast("JsonValue", replacement)

    if permitted:
        adapter._validate_registration_response(  # noqa: SLF001 - directly tests the reviewed validation boundary.
            body,
            state,
            require_consistency=True,
        )
    else:
        with pytest.raises(DcrExecutionError, match=field):
            adapter._validate_registration_response(  # noqa: SLF001 - directly tests the reviewed validation boundary.
                body,
                state,
                require_consistency=True,
            )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("application_type", "mobile"),
        ("software_id", "not-valid-hyphen"),
        ("scope", ""),
        ("id_token_signed_response_alg", "PS256X"),
        ("request_object_signing_alg", "none!"),
        ("tls_client_auth_subject_dn", "O=Missing leading common name"),
        ("registration_client_uri", "http://client.example.test/register/client"),
        ("redirect_uris", ["http://localhost/callback"]),
    ],
)
def test_registration_response_rejects_complete_schema_boundary_violations(
    dcr_test_service: DcrTestService,
    tmp_path: Path,
    field: str,
    replacement: JsonValue,
) -> None:
    """DCR 3.4 scalar, URI, algorithm, and subject-DN rules are mandatory."""
    adapter, body, state = _registration_validation_fixture(dcr_test_service, tmp_path)
    body[field] = replacement

    with pytest.raises(DcrExecutionError):
        adapter._validate_registration_response(  # noqa: SLF001 - directly tests the reviewed validation boundary.
            body,
            state,
            require_consistency=True,
        )


@pytest.mark.unit
def test_registration_builder_supports_claim_overrides_and_exact_variants(
    dcr_test_service: DcrTestService,
    tmp_path: Path,
) -> None:
    """Compact JOSE uses PS256/kid and applies variants before explicit overrides."""
    with dcr_test_service.client() as client:
        adapter = _adapter(dcr_test_service, tmp_path, client=client)
        compact, claims = adapter.build_registration_jose(
            variant="issuer-empty",
            overrides={"iss": "explicit-issuer", "application_type": "native"},
        )
    decoded = jwt.decode(compact, dcr_test_service.protocol.signing_public_key, algorithms=["PS256"])

    assert decoded.header.get("alg") == "PS256"
    assert decoded.header.get("kid") == "fixture-signing-key"
    assert claims["iss"] == decoded.claims["iss"] == "explicit-issuer"
    assert claims["aud"] == decoded.claims["aud"] == "aspsp123"
    assert claims["application_type"] == "native"
    assert claims["software_statement"] == dcr_test_service.protocol.software_statement_assertion


@pytest.mark.unit
def test_registration_builder_reproduces_legacy_rs256_negative_request(
    dcr_test_service: DcrTestService,
    tmp_path: Path,
) -> None:
    """The legacy RS256 case changes the registration JWS and ID-token algorithm."""
    with dcr_test_service.client() as client:
        adapter = _adapter(dcr_test_service, tmp_path, client=client)
        compact, claims = adapter.build_registration_jose(variant="registration-signing-alg-rs256")

    verification_key = jwk.import_key(dcr_test_service.protocol.signing_public_key.as_pem(), "RSA")
    decoded = jwt.decode(compact, verification_key, algorithms=["RS256"])

    assert decoded.header.get("alg") == "RS256"
    assert claims["id_token_signed_response_alg"] == "RS256"  # noqa: S105 - JOSE algorithm identifier.


def test_certificate_dn_derivation_numeric_oid_override_and_512_boundary(
    dcr_test_service: DcrTestService,
) -> None:
    """Certificate subjects support named/numeric rendering and strict length."""
    certificate = dcr_test_service.tls.client_certificate_path

    assert certificate_subject_dn(certificate, override=None, numeric_oids=False) == "CN=DCR fixture client"
    assert certificate_subject_dn(certificate, override=None, numeric_oids=True) == "2.5.4.3=DCR fixture client"
    assert certificate_subject_dn(certificate, override="CN=override,O=Example", numeric_oids=True) == (
        "CN=override,O=Example"
    )
    validate_dcr_subject_dn("CN=" + "a" * 509)
    with pytest.raises(DcrExecutionError, match="1 to 512"):
        validate_dcr_subject_dn("CN=" + "a" * 510)


@pytest.mark.unit
def test_runtime_input_round_trip_and_client_secret_post_rejection(
    dcr_test_service: DcrTestService,
    tmp_path: Path,
) -> None:
    """Prepared dotted inputs round-trip while unsupported auth fails explicitly."""
    config = _config(dcr_test_service, tmp_path)
    assert parse_dcr_execution_runtime_inputs(dcr_execution_runtime_inputs(config)) == config
    unsupported = dcr_execution_runtime_inputs(config)
    unsupported["securityEnvironment.clientAuthMethod"] = "client_secret_post"

    with pytest.raises(ConfigError, match="clientAuthMethod must be one of"):
        parse_dcr_execution_runtime_inputs(unsupported)


@pytest.mark.integration
def test_shared_compiled_plan_executor_dispatches_to_typed_dcr_adapter(
    dcr_test_service: DcrTestService,
    tmp_path: Path,
) -> None:
    """The public compiled-plan executor preserves shared result compatibility."""
    config = _config(dcr_test_service, tmp_path)
    ids = iter(f"executor-jti-{index}" for index in range(1000))
    with httpx.Client() as unused_read_write_client:
        result = run_compiled_test_plan(
            _compiled_plan(("POST",)),
            runtime_inputs=dcr_execution_runtime_inputs(config),
            runtime_input_base_dir=tmp_path,
            client=unused_read_write_client,
            dcr_clock=lambda: _FIXED_TIME,
            dcr_jwt_id_factory=lambda: next(ids),
        )

    assert result.status == "passed"
    assert result.compiled_plan is not None
    catalogue_evidence = result.steps[0].details["catalogue"]
    assert isinstance(catalogue_evidence, dict)
    assert catalogue_evidence["traceGroupId"] == "DCR-001"
