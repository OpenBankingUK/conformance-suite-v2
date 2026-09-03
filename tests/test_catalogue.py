"""Unit tests for :mod:`conformance.catalogue`."""

from __future__ import annotations

import pytest

from conformance.catalogue import (
    AssertionOverride,
    CatalogueAssertion,
    CatalogueError,
    CatalogueKey,
    CatalogueRequestHeader,
    CatalogueRequestStep,
    CatalogueTestCase,
    EndpointCapability,
    EndpointRef,
    ImplementedEndpoint,
    PlanDocumentV2,
    RuntimeInputRequirement,
    SecurityProfile,
    SecurityProfileApplicability,
    TestCaseApplicability,
    TestCatalogue,
    TestPlanSpec,
    compile_test_plan,
    compile_test_plan_document,
    parse_test_plan_document,
    parse_test_plan_spec,
    plan_document_to_json_object,
)
from conformance.json_types import JsonValue

CATALOGUE_KEY = CatalogueKey(standard="open-banking", version="v4.0", api="ais")


def _profile(*profiles: SecurityProfile) -> SecurityProfileApplicability:
    return SecurityProfileApplicability(profiles=profiles)


def _case(
    test_case_id: str,
    *,
    endpoint_refs: tuple[EndpointRef, ...] = (),
    capability_ids: tuple[str, ...] = (),
    dependencies: tuple[str, ...] = (),
    mandatory: bool = True,
    runtime_requirements: tuple[RuntimeInputRequirement, ...] = (),
    specification_versions: tuple[str, ...] = (),
) -> CatalogueTestCase:
    return CatalogueTestCase(
        test_case_id=test_case_id,
        name=test_case_id.replace("-", " ").title(),
        role="resource",
        compliance_scope=("OBRW v4.0",),
        applicability=TestCaseApplicability(
            security_profiles=_profile("all"),
            endpoint_refs=endpoint_refs,
            required_capability_ids=capability_ids,
            specification_versions=specification_versions,
        ),
        mandatory=mandatory,
        dependencies=dependencies,
        runtime_input_requirements=runtime_requirements,
        request_steps=(
            CatalogueRequestStep(
                step_id=f"{test_case_id}-request",
                name="Fetch resource",
                method="GET",
                path="/open-banking/v4.0/aisp/accounts",
            ),
        ),
        assertions=(
            CatalogueAssertion(
                assertion_id="status-200",
                kind="http_status",
                description="HTTP status is 200",
                rule={"expected": 200},
            ),
        ),
    )


def _catalogue(*test_cases: CatalogueTestCase) -> TestCatalogue:
    return TestCatalogue(
        key=CATALOGUE_KEY,
        catalogue_version="2026.7.0",
        test_cases=test_cases,
    )


def _catalogue_with_key(
    key: CatalogueKey,
    *,
    version: str,
    test_cases: tuple[CatalogueTestCase, ...],
    capabilities: tuple[EndpointCapability, ...] = (),
) -> TestCatalogue:
    return TestCatalogue(key=key, catalogue_version=version, test_cases=test_cases, capabilities=capabilities)


def _spec(
    *,
    endpoints: tuple[ImplementedEndpoint, ...] = (
        ImplementedEndpoint(
            method="GET",
            path="/open-banking/v4.0/aisp/accounts",
            resource_group="Accounts",
        ),
    ),
    runtime_inputs: dict[str, JsonValue] | None = None,
    specification_version: str | None = None,
    deselected: tuple[str, ...] = (),
    overrides: tuple[AssertionOverride, ...] = (),
) -> TestPlanSpec:
    return TestPlanSpec(
        schema_version="v1",
        catalogue_key=CATALOGUE_KEY,
        security_profile="fapi1-advanced",
        implemented_endpoints=endpoints,
        runtime_inputs={} if runtime_inputs is None else runtime_inputs,
        specification_version=specification_version,
        deselected_test_case_ids=deselected,
        assertion_overrides=overrides,
    )


def _capability(
    capability_id: str,
    *,
    endpoint_refs: tuple[EndpointRef, ...] = (EndpointRef(method="GET", path="/open-banking/v4.0/aisp/accounts"),),
    required: bool = False,
) -> EndpointCapability:
    return EndpointCapability(
        capability_id=capability_id,
        label=capability_id.replace(".", " ").title(),
        description=f"{capability_id} implementation support",
        required=required,
        endpoint_refs=endpoint_refs,
    )


@pytest.mark.unit
def test_parse_v2_plan_derives_runtime_inputs_from_structured_config() -> None:
    """Structured v2 config keeps fixture data separate from runtime inputs."""
    document = parse_test_plan_document(
        {
            "schemaVersion": "v2",
            "scheme": "open-banking-uk",
            "specification": "read-write",
            "version": "4.0.1",
            "securityProfile": "fapi1-advanced",
            "scope": {"resourceGroups": []},
            "config": {
                "resourceServer": {"baseUrl": "https://rs.example.com"},
                "ais": {
                    "resourceIds": {"accountIds": [{"accountId": "account-123"}]},
                    "transactionFromDate": "2026-01-01T00:00:00Z",
                    "transactionToDate": "2026-01-31T23:59:59Z",
                },
                "cbpii": {
                    "debtorAccount": {
                        "schemeName": "UK.OBIE.SortCodeAccountNumber",
                        "identification": "12345678901234",
                        "name": "Model Bank Account",
                    }
                },
            },
        }
    )

    assert isinstance(document, PlanDocumentV2)
    assert document.runtime_inputs["resourceBaseUrl"] == "https://rs.example.com"
    assert document.runtime_inputs["consentedAccountId"] == "account-123"
    assert document.runtime_inputs["fromBookingDateTime"] == "2026-01-01T00:00:00Z"
    assert document.runtime_inputs["toBookingDateTime"] == "2026-01-31T23:59:59Z"
    assert document.runtime_inputs["debtorAccountSchemeName"] == "UK.OBIE.SortCodeAccountNumber"
    assert document.runtime_inputs["debtorAccountIdentification"] == "12345678901234"
    assert document.runtime_inputs["debtorAccountName"] == "Model Bank Account"


@pytest.mark.unit
def test_compile_rejects_unsupported_request_header_runtime_inputs() -> None:
    """Header-like runtime inputs must be declared by the selected catalogue."""
    catalogue = _catalogue(_case("accounts-list"))

    with pytest.raises(
        CatalogueError,
        match="Unsupported request header runtime input\\(s\\) for selected scope: xFapiFinancialId",
    ):
        compile_test_plan(catalogue, _spec(runtime_inputs={"xFapiFinancialId": "financial-id"}))


@pytest.mark.unit
def test_compile_accepts_declared_version_specific_request_header_runtime_input() -> None:
    """Future catalogue imports can opt in to currently unsupported headers."""
    header_requirement = RuntimeInputRequirement(
        input_id="xFapiFinancialId",
        input_type="string",
        label="x-fapi-financial-id header",
        sensitive=True,
    )
    case = CatalogueTestCase(
        test_case_id="accounts-list-with-financial-id",
        name="Accounts list with financial id",
        role="resource",
        compliance_scope=("future-version fixture",),
        applicability=TestCaseApplicability(
            security_profiles=_profile("all"),
            endpoint_refs=(EndpointRef(method="GET", path="/open-banking/v4.0/aisp/accounts"),),
        ),
        mandatory=True,
        runtime_input_requirements=(header_requirement,),
        request_steps=(
            CatalogueRequestStep(
                step_id="accounts-list-with-financial-id-request",
                name="Fetch resource",
                method="GET",
                path="/open-banking/v4.0/aisp/accounts",
                headers=(CatalogueRequestHeader(name="x-fapi-financial-id", input_id="xFapiFinancialId"),),
            ),
        ),
        assertions=(CatalogueAssertion("status-200", "http_status", "HTTP status is 200", {"expected": 200}),),
    )

    compiled = compile_test_plan(_catalogue(case), _spec(runtime_inputs={"xFapiFinancialId": "financial-id"}))

    snapshot = {entry.input_id: entry for entry in compiled.traceability.runtime_input_snapshot}
    assert snapshot["xFapiFinancialId"].provided is True
    assert snapshot["xFapiFinancialId"].value is None


@pytest.mark.unit
def test_compile_selects_applicable_endpoint_cases_and_dependencies() -> None:
    setup_case = _case("setup-discovery")
    account_case = _case(
        "accounts-read",
        endpoint_refs=(EndpointRef(method="GET", path="/open-banking/v4.0/aisp/accounts"),),
        dependencies=("setup-discovery",),
    )
    balance_case = _case(
        "balances-read",
        endpoint_refs=(EndpointRef(method="GET", path="/open-banking/v4.0/aisp/balances"),),
    )

    compiled = compile_test_plan(_catalogue(setup_case, account_case, balance_case), _spec())

    assert [case.test_case_id for case in compiled.test_cases] == ["setup-discovery", "accounts-read"]
    assert compiled.traceability.generated_test_case_ids == ("setup-discovery", "accounts-read")
    setup_decision = compiled.traceability.applicability_decisions[0]
    assert setup_decision.selected is True
    assert setup_decision.dependency_of == ("accounts-read",)
    assert compiled.certifying is True


@pytest.mark.unit
def test_compile_filters_applicable_cases_by_specification_version() -> None:
    account_ref = EndpointRef(method="GET", path="/open-banking/v4.0/aisp/accounts")
    v31_case = _case("accounts-read-v31", endpoint_refs=(account_ref,), specification_versions=("3.1.11",))
    v40_case = _case("accounts-read-v40", endpoint_refs=(account_ref,), specification_versions=("4.0.1",))

    compiled = compile_test_plan(_catalogue(v31_case, v40_case), _spec(specification_version="4.0.1"))

    assert [case.test_case_id for case in compiled.test_cases] == ["accounts-read-v40"]
    decisions = {decision.test_case_id: decision for decision in compiled.traceability.applicability_decisions}
    assert decisions["accounts-read-v31"].selected is False
    assert decisions["accounts-read-v31"].reason == "not applicable to specification version 4.0.1"


@pytest.mark.unit
def test_compile_selects_required_endpoint_capability_when_omitted() -> None:
    account_ref = EndpointRef(method="GET", path="/open-banking/v4.0/aisp/accounts")
    account_case = _case(
        "accounts-read",
        endpoint_refs=(account_ref,),
        capability_ids=("accounts.read",),
    )
    catalogue = TestCatalogue(
        key=CATALOGUE_KEY,
        catalogue_version="2026.7.0",
        test_cases=(account_case,),
        capabilities=(_capability("accounts.read", required=True),),
    )

    compiled = compile_test_plan(catalogue, _spec())

    assert [case.test_case_id for case in compiled.test_cases] == ["accounts-read"]
    assert compiled.traceability.selected_capabilities[0].capability_id == "accounts.read"
    assert compiled.traceability.selected_capabilities[0].required is True


@pytest.mark.unit
def test_compile_excludes_optional_capability_case_when_omitted() -> None:
    account_ref = EndpointRef(method="GET", path="/open-banking/v4.0/aisp/accounts")
    account_case = _case("accounts-read", endpoint_refs=(account_ref,))
    balances_case = _case(
        "accounts-balances",
        endpoint_refs=(account_ref,),
        capability_ids=("accounts.balances",),
    )
    catalogue = TestCatalogue(
        key=CATALOGUE_KEY,
        catalogue_version="2026.7.0",
        test_cases=(account_case, balances_case),
        capabilities=(_capability("accounts.balances"),),
    )

    compiled = compile_test_plan(catalogue, _spec())

    assert [case.test_case_id for case in compiled.test_cases] == ["accounts-read"]
    assert compiled.traceability.applicability_decisions[1].reason == (
        "required capability not selected: accounts.balances"
    )


@pytest.mark.unit
def test_compile_selects_optional_capability_case_when_declared() -> None:
    account_ref = EndpointRef(method="GET", path="/open-banking/v4.0/aisp/accounts")
    account_case = _case("accounts-read", endpoint_refs=(account_ref,))
    balances_case = _case(
        "accounts-balances",
        endpoint_refs=(account_ref,),
        capability_ids=("accounts.balances",),
    )
    endpoint = ImplementedEndpoint(
        method="GET",
        path="/open-banking/v4.0/aisp/accounts",
        resource_group="Accounts",
        capability_ids=("accounts.balances",),
    )
    catalogue = TestCatalogue(
        key=CATALOGUE_KEY,
        catalogue_version="2026.7.0",
        test_cases=(account_case, balances_case),
        capabilities=(_capability("accounts.balances"),),
    )

    compiled = compile_test_plan(catalogue, _spec(endpoints=(endpoint,)))

    assert [case.test_case_id for case in compiled.test_cases] == ["accounts-read", "accounts-balances"]
    assert compiled.traceability.selected_capabilities[0].capability_id == "accounts.balances"
    assert compiled.traceability.selected_capabilities[0].required is False


@pytest.mark.unit
def test_compile_rejects_unknown_selected_capability() -> None:
    endpoint = ImplementedEndpoint(
        method="GET",
        path="/open-banking/v4.0/aisp/accounts",
        resource_group="Accounts",
        capability_ids=("accounts.unknown",),
    )

    with pytest.raises(CatalogueError, match="unknown capability 'accounts.unknown'"):
        compile_test_plan(_catalogue(_case("accounts-read")), _spec(endpoints=(endpoint,)))


@pytest.mark.unit
def test_compile_rejects_capability_that_does_not_apply_to_endpoint() -> None:
    endpoint = ImplementedEndpoint(
        method="GET",
        path="/open-banking/v4.0/aisp/accounts",
        resource_group="Accounts",
        capability_ids=("balances.read",),
    )
    catalogue = TestCatalogue(
        key=CATALOGUE_KEY,
        catalogue_version="2026.7.0",
        test_cases=(_case("accounts-read"),),
        capabilities=(
            _capability(
                "balances.read",
                endpoint_refs=(EndpointRef(method="GET", path="/open-banking/v4.0/aisp/balances"),),
            ),
        ),
    )

    expected_error = "does not apply to implemented endpoint GET /open-banking/v4.0/aisp/accounts"
    with pytest.raises(CatalogueError, match=expected_error):
        compile_test_plan(catalogue, _spec(endpoints=(endpoint,)))


@pytest.mark.unit
def test_compile_rejects_mandatory_applicable_deselection() -> None:
    account_case = _case(
        "accounts-read",
        endpoint_refs=(EndpointRef(method="GET", path="/open-banking/v4.0/aisp/accounts"),),
        mandatory=True,
    )

    with pytest.raises(CatalogueError, match="Mandatory applicable test case 'accounts-read' cannot be deselected"):
        compile_test_plan(_catalogue(account_case), _spec(deselected=("accounts-read",)))


@pytest.mark.unit
def test_compile_allows_optional_deselection_and_traces_it() -> None:
    account_case = _case(
        "accounts-read",
        endpoint_refs=(EndpointRef(method="GET", path="/open-banking/v4.0/aisp/accounts"),),
        mandatory=False,
    )

    compiled = compile_test_plan(_catalogue(account_case), _spec(deselected=("accounts-read",)))

    assert compiled.test_cases == ()
    assert compiled.traceability.generated_test_case_ids == ()
    assert compiled.traceability.applicability_decisions[0].reason == "deselected by participant"


@pytest.mark.unit
def test_compile_does_not_keep_orphan_dependencies_for_deselected_optional_case() -> None:
    setup_case = _case("setup-discovery", endpoint_refs=(EndpointRef(method="GET", path="/setup"),))
    account_case = _case(
        "accounts-read",
        endpoint_refs=(EndpointRef(method="GET", path="/open-banking/v4.0/aisp/accounts"),),
        dependencies=("setup-discovery",),
        mandatory=False,
    )

    compiled = compile_test_plan(_catalogue(setup_case, account_case), _spec(deselected=("accounts-read",)))

    assert compiled.test_cases == ()
    assert compiled.traceability.generated_test_case_ids == ()


@pytest.mark.unit
def test_compile_rejects_missing_required_runtime_input() -> None:
    account_case = _case(
        "accounts-read",
        endpoint_refs=(EndpointRef(method="GET", path="/open-banking/v4.0/aisp/accounts"),),
        runtime_requirements=(
            RuntimeInputRequirement(
                input_id="resourceBaseUrl",
                input_type="url",
                label="Resource base URL",
            ),
        ),
    )

    with pytest.raises(CatalogueError, match="Required runtime input 'resourceBaseUrl' is missing"):
        compile_test_plan(_catalogue(account_case), _spec())


@pytest.mark.unit
def test_compile_snapshots_runtime_inputs_without_sensitive_values() -> None:
    account_case = _case(
        "accounts-read",
        endpoint_refs=(EndpointRef(method="GET", path="/open-banking/v4.0/aisp/accounts"),),
        runtime_requirements=(
            RuntimeInputRequirement(
                input_id="resourceBaseUrl",
                input_type="url",
                label="Resource base URL",
            ),
            RuntimeInputRequirement(
                input_id="clientSecret",
                input_type="string",
                label="Client secret",
                sensitive=True,
            ),
        ),
    )

    compiled = compile_test_plan(
        _catalogue(account_case),
        _spec(runtime_inputs={"resourceBaseUrl": "https://rs.example.com", "clientSecret": "secret-value"}),
    )

    assert compiled.traceability.runtime_input_snapshot[0].value == "https://rs.example.com"
    assert compiled.traceability.runtime_input_snapshot[1].provided is True
    assert compiled.traceability.runtime_input_snapshot[1].value is None


@pytest.mark.unit
def test_compile_marks_assertion_overrides_non_certifying() -> None:
    account_case = _case(
        "accounts-read",
        endpoint_refs=(EndpointRef(method="GET", path="/open-banking/v4.0/aisp/accounts"),),
    )

    compiled = compile_test_plan(
        _catalogue(account_case),
        _spec(
            overrides=(
                AssertionOverride(
                    test_case_id="accounts-read",
                    assertion_id="status-200",
                    reason="Local ASPSP test environment variance",
                ),
            )
        ),
    )

    assert compiled.certifying is False
    assert compiled.traceability.non_certifying_reasons == (
        "Assertion override supplied for accounts-read.status-200: Local ASPSP test environment variance",
    )


@pytest.mark.unit
def test_compile_rejects_duplicate_catalogue_ids() -> None:
    duplicate = _case("accounts-read")

    with pytest.raises(CatalogueError, match="duplicate"):
        compile_test_plan(_catalogue(duplicate, duplicate), _spec())


@pytest.mark.unit
def test_parse_test_plan_spec_validates_exportable_json_shape() -> None:
    raw_spec: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "catalogue": {"standard": "open-banking", "version": "v4.0", "api": "ais"},
        "securityProfile": "fapi1-advanced",
        "implementedEndpoints": [
            {
                "method": "get",
                "path": "/open-banking/v4.0/aisp/accounts/",
                "resourceGroup": "Accounts",
                "operationId": "GetAccounts",
                "capabilities": ["accounts.balances"],
            }
        ],
        "runtimeInputs": {"resourceBaseUrl": "https://rs.example.com"},
        "deselectedTestCaseIds": ["optional-accounts-extension"],
        "assertionOverrides": [
            {
                "testCaseId": "accounts-read",
                "assertionId": "status-200",
                "reason": "Local non-certifying diagnostic",
            }
        ],
    }

    spec = parse_test_plan_spec(raw_spec)

    assert spec.catalogue_key == CATALOGUE_KEY
    assert spec.implemented_endpoints[0].method == "GET"
    assert spec.implemented_endpoints[0].path == "/open-banking/v4.0/aisp/accounts"
    assert spec.implemented_endpoints[0].capability_ids == ("accounts.balances",)
    assert spec.deselected_test_case_ids == ("optional-accounts-extension",)
    assert spec.assertion_overrides[0].assertion_id == "status-200"


@pytest.mark.unit
def test_parse_test_plan_document_v2_serializes_nested_scope_and_config() -> None:
    """Legacy v2 plan documents serialize back to the canonical JSON-first shape."""
    raw_spec: dict[str, JsonValue] = {
        "schemaVersion": "v2",
        "scheme": "open-banking-uk",
        "specification": "read-write",
        "version": "4.0.1",
        "securityProfile": "fapi1-advanced",
        "scope": {
            "resourceGroups": [
                {
                    "id": "ais.accounts",
                    "label": "Accounts",
                    "endpoints": [
                        {
                            "method": "get",
                            "path": "/open-banking/v4.0/aisp/accounts/",
                            "capabilities": ["ais.accounts.list.core"],
                        }
                    ],
                }
            ]
        },
        "config": {
            "discoveryUrl": "https://auth.example.com/.well-known/openid-configuration",
            "resourceBaseUrl": "https://rs.example.com",
            "inputs": {"accessToken": {"value": "secret-access-token"}},
        },
    }

    document = parse_test_plan_document(raw_spec)

    assert isinstance(document, PlanDocumentV2)
    assert document.scheme == "open-banking-uk"
    assert document.specification == "read-write"
    assert document.version == "4.0.1"
    assert document.resource_groups[0].resource_group_id == "ais.accounts"
    assert document.resource_groups[0].endpoints[0].method == "GET"
    assert document.resource_groups[0].endpoints[0].path == "/open-banking/v4.0/aisp/accounts"
    assert document.resource_groups[0].endpoints[0].capability_ids == ("ais.accounts.list.core",)
    assert document.runtime_inputs["resourceBaseUrl"] == "https://rs.example.com"
    assert document.runtime_inputs["accessToken"] == "secret-access-token"
    assert plan_document_to_json_object(document) == {
        "schemaVersion": "1.0",
        "specification": {
            "family": "OBL_READ_WRITE",
            "version": "4.0.1",
            "profile": "FAPI1_ADVANCED",
        },
        "executionMode": "certification",
        "securityEnvironment": {"discoveryUrl": "https://auth.example.com/.well-known/openid-configuration"},
        "resourceGroups": [
            {
                "id": "AIS",
                "label": "Accounts",
                "endpoints": [
                    {
                        "method": "GET",
                        "path": "/open-banking/v4.0/aisp/accounts",
                        "capabilities": ["ais.accounts.list.core"],
                    }
                ],
            }
        ],
        "businessTestData": {
            "runtimeInputs": {"resourceBaseUrl": "https://rs.example.com"},
        },
        "metadata": {},
    }


@pytest.mark.unit
def test_legacy_v2_to_canonical_export_allows_missing_discovery_url() -> None:
    """Legacy documents can serialize manual security config without discovery."""
    document = parse_test_plan_document(
        {
            "schemaVersion": "v2",
            "scheme": "open-banking-uk",
            "specification": "read-write",
            "version": "4.0.1",
            "securityProfile": "fapi1-advanced",
            "scope": {"resourceGroups": []},
            "config": {"resourceServer": {"baseUrl": "https://rs.example.com"}},
        }
    )

    assert isinstance(document, PlanDocumentV2)
    exported = plan_document_to_json_object(document)
    assert exported["securityEnvironment"] == {"resourceBaseUrl": "https://rs.example.com"}


@pytest.mark.unit
def test_legacy_v2_to_canonical_export_omits_empty_mtls_block() -> None:
    """Legacy TLS config without recognised fields does not emit empty mTLS metadata."""
    document = parse_test_plan_document(
        {
            "schemaVersion": "v2",
            "scheme": "open-banking-uk",
            "specification": "read-write",
            "version": "4.0.1",
            "securityProfile": "fapi1-advanced",
            "scope": {"resourceGroups": []},
            "config": {
                "discoveryUrl": "https://auth.example.com/.well-known/openid-configuration",
                "tls": {},
            },
        }
    )

    assert isinstance(document, PlanDocumentV2)
    exported = plan_document_to_json_object(document)
    security_environment = exported["securityEnvironment"]
    assert isinstance(security_environment, dict)
    assert "mtls" not in security_environment


@pytest.mark.unit
def test_parse_canonical_plan_document_maps_prd_business_and_security_fields() -> None:
    """Canonical PRD-shaped test plans derive runner config and runtime inputs."""
    raw_spec: dict[str, JsonValue] = {
        "schemaVersion": "1.0",
        "specification": {
            "family": "OBL_READ_WRITE",
            "version": "4.0.1",
            "profile": "FAPI1_ADVANCED",
        },
        "securityEnvironment": {
            "name": "Primary Authorization Server",
            "discoveryUrl": "https://auth.example.com/.well-known/openid-configuration",
            "clientAuthMethod": "private_key_jwt",
            "signingAlgorithm": "PS256",
            "resourceBaseUrl": "https://rs.example.com",
            "mtls": {"enabled": True, "certificatePath": "/absolute/path/transport.pem"},
        },
        "resourceGroups": ["AIS"],
        "businessTestData": {
            "ais": {
                "accountIds": ["account-123"],
                "transactionFromDate": "2026-01-01T00:00:00Z",
            },
            "inputs": {"accessToken": {"value": "secret-access-token"}},
        },
        "metadata": {"aspspName": "Example Bank", "brandName": "Example Retail"},
        "executionMode": "development",
    }

    document = parse_test_plan_document(raw_spec)

    assert isinstance(document, PlanDocumentV2)
    assert document.schema_version == "1.0"
    assert document.execution_mode == "development"
    assert document.security_profile == "fapi1-advanced"
    assert document.resource_groups[0].resource_group_id == "account-and-transaction"
    assert document.resource_groups[0].select_all is True
    assert document.config["discoveryUrl"] == "https://auth.example.com/.well-known/openid-configuration"
    assert document.config["resourceServer"] == {"baseUrl": "https://rs.example.com"}
    ais_config = document.config["ais"]
    assert isinstance(ais_config, dict)
    resource_ids = ais_config["resourceIds"]
    assert isinstance(resource_ids, dict)
    assert resource_ids["accountIds"] == [{"accountId": "account-123"}]
    assert document.runtime_inputs["resourceBaseUrl"] == "https://rs.example.com"
    assert document.runtime_inputs["consentedAccountId"] == "account-123"
    assert document.runtime_inputs["accessToken"] == "secret-access-token"


@pytest.mark.unit
def test_parse_canonical_plan_document_rejects_security_timeout() -> None:
    """Canonical security environments do not accept configurable HTTP timeouts."""
    raw_spec: dict[str, JsonValue] = {
        "schemaVersion": "1.0",
        "specification": {
            "family": "OBL_READ_WRITE",
            "version": "4.0.1",
            "profile": "FAPI1_ADVANCED",
        },
        "securityEnvironment": {
            "discoveryUrl": "https://auth.example.com/.well-known/openid-configuration",
            "timeoutSeconds": 60,
        },
        "resourceGroups": ["AIS"],
        "businessTestData": {},
        "metadata": {},
    }

    with pytest.raises(CatalogueError, match="Unknown testPlan.securityEnvironment field\\(s\\): timeoutSeconds"):
        parse_test_plan_document(raw_spec)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("openBankingIntentId", "consent-789"),
        ("acrValuesSupported", ["urn:openbanking:psd2:sca"]),
        ("jwksUri", "https://auth.example.com/jwks"),
        ("tppSignatureIssuer", "synthetic-org-id"),
        ("tppSignatureTan", "openbanking.org.uk"),
        ("xFapiFinancialId", "financial-id"),
        ("sendXFapiCustomerIpAddress", True),
        ("xFapiCustomerIpAddress", "203.0.113.10"),
    ],
)
def test_parse_canonical_plan_document_rejects_removed_security_fields(
    field_name: str,
    value: JsonValue,
) -> None:
    """Canonical security environments reject removed participant config fields."""
    raw_spec: dict[str, JsonValue] = {
        "schemaVersion": "1.0",
        "specification": {
            "family": "OBL_READ_WRITE",
            "version": "4.0.1",
            "profile": "FAPI1_ADVANCED",
        },
        "securityEnvironment": {
            "discoveryUrl": "https://auth.example.com/.well-known/openid-configuration",
            field_name: value,
        },
        "resourceGroups": ["AIS"],
        "businessTestData": {},
        "metadata": {},
    }

    with pytest.raises(CatalogueError, match=rf"Unknown testPlan.securityEnvironment field\(s\): {field_name}"):
        parse_test_plan_document(raw_spec)


@pytest.mark.unit
def test_parse_canonical_plan_document_rejects_mtls_certificate_path_root() -> None:
    """Canonical mTLS config uses direct path fields rather than a shared root."""
    raw_spec: dict[str, JsonValue] = {
        "schemaVersion": "1.0",
        "specification": {
            "family": "OBL_READ_WRITE",
            "version": "4.0.1",
            "profile": "FAPI1_ADVANCED",
        },
        "securityEnvironment": {
            "discoveryUrl": "https://auth.example.com/.well-known/openid-configuration",
            "mtls": {"certificatePathRoot": "/absolute/path/certs"},
        },
        "resourceGroups": ["AIS"],
        "businessTestData": {},
        "metadata": {},
    }

    with pytest.raises(
        CatalogueError, match=r"Unknown testPlan.securityEnvironment\.mtls field\(s\): certificatePathRoot"
    ):
        parse_test_plan_document(raw_spec)


@pytest.mark.unit
def test_parse_canonical_plan_document_requires_business_data_and_metadata() -> None:
    """Canonical parser enforces required empty-object sections without relying on JSON Schema."""
    raw_spec: dict[str, JsonValue] = {
        "schemaVersion": "1.0",
        "specification": {"family": "OBL_READ_WRITE", "version": "4.0.1"},
        "securityEnvironment": {"discoveryUrl": "https://auth.example.com/.well-known/openid-configuration"},
        "resourceGroups": ["AIS"],
    }

    with pytest.raises(CatalogueError, match="testPlan.businessTestData is required"):
        parse_test_plan_document(raw_spec)

    raw_spec["businessTestData"] = {}
    with pytest.raises(CatalogueError, match="testPlan.metadata is required"):
        parse_test_plan_document(raw_spec)


@pytest.mark.unit
def test_parse_canonical_plan_document_rejects_conflicting_profile_aliases() -> None:
    """Canonical specification profile aliases must not disagree."""
    raw_spec: dict[str, JsonValue] = {
        "schemaVersion": "1.0",
        "specification": {
            "family": "OBL_READ_WRITE",
            "version": "4.0.1",
            "profile": "FAPI1_ADVANCED",
            "securityProfile": "FAPI2",
        },
        "securityEnvironment": {"discoveryUrl": "https://auth.example.com/.well-known/openid-configuration"},
        "resourceGroups": ["AIS"],
        "businessTestData": {},
        "metadata": {},
    }

    with pytest.raises(CatalogueError, match="profile and testPlan.specification.securityProfile must match"):
        parse_test_plan_document(raw_spec)


@pytest.mark.unit
def test_parse_canonical_plan_document_accepts_matching_profile_aliases() -> None:
    """Canonical specification profile aliases may both be supplied when equivalent."""
    raw_spec: dict[str, JsonValue] = {
        "schemaVersion": "1.0",
        "specification": {
            "family": "OBL_READ_WRITE",
            "version": "4.0.1",
            "profile": "FAPI1_ADVANCED",
            "securityProfile": "fapi1-advanced",
        },
        "securityEnvironment": {"discoveryUrl": "https://auth.example.com/.well-known/openid-configuration"},
        "resourceGroups": ["AIS"],
        "businessTestData": {},
        "metadata": {},
    }

    document = parse_test_plan_document(raw_spec)

    assert isinstance(document, PlanDocumentV2)
    assert document.security_profile == "fapi1-advanced"


@pytest.mark.unit
def test_parse_canonical_plan_document_rejects_profile_not_declared_by_version() -> None:
    """Canonical Read/Write plans reject profiles not declared by the registry version."""
    raw_spec: dict[str, JsonValue] = {
        "schemaVersion": "1.0",
        "specification": {
            "family": "OBL_READ_WRITE",
            "version": "4.0.1",
            "profile": "FAPI2",
        },
        "securityEnvironment": {"discoveryUrl": "https://auth.example.com/.well-known/openid-configuration"},
        "resourceGroups": ["AIS"],
        "businessTestData": {},
        "metadata": {},
    }

    with pytest.raises(CatalogueError, match=r"profile must be one of: FAPI1_ADVANCED for OBL_READ_WRITE 4\.0\.1"):
        parse_test_plan_document(raw_spec)


@pytest.mark.unit
def test_parse_plan_document_uses_neutral_root_for_schema_version_errors() -> None:
    """Plan-document schema-version errors do not refer to the legacy planSpec root."""
    with pytest.raises(CatalogueError, match="planDocument.schemaVersion"):
        parse_test_plan_document({"schemaVersion": "v99"})


@pytest.mark.unit
def test_canonical_resource_group_shorthand_expands_to_catalogue_endpoints() -> None:
    """A canonical resource-group string selects all endpoints in that catalogue group."""
    account_ref = EndpointRef(method="GET", path="/open-banking/v4.0/aisp/accounts")
    balance_ref = EndpointRef(method="GET", path="/open-banking/v4.0/aisp/balances")
    catalogue = _catalogue(
        _case(
            "accounts-read",
            endpoint_refs=(account_ref,),
            runtime_requirements=(RuntimeInputRequirement("resourceBaseUrl", "url", "Resource server base URL"),),
        ),
        _case(
            "balances-read",
            endpoint_refs=(balance_ref,),
            runtime_requirements=(RuntimeInputRequirement("resourceBaseUrl", "url", "Resource server base URL"),),
        ),
    )
    raw_spec: dict[str, JsonValue] = {
        "schemaVersion": "1.0",
        "specification": {"family": "OBL_READ_WRITE", "version": "4.0.1"},
        "securityEnvironment": {
            "discoveryUrl": "https://auth.example.com/.well-known/openid-configuration",
            "resourceBaseUrl": "https://rs.example.com",
        },
        "resourceGroups": ["AIS"],
        "businessTestData": {},
        "metadata": {},
    }
    document = parse_test_plan_document(raw_spec)

    compiled = compile_test_plan_document(document, (catalogue,))

    assert [case.test_case_id for case in compiled.test_cases] == ["accounts-read", "balances-read"]
    assert [endpoint.path for endpoint in compiled.traceability.selected_endpoints] == [
        "/open-banking/v4.0/aisp/accounts",
        "/open-banking/v4.0/aisp/balances",
    ]


@pytest.mark.unit
def test_compile_test_plan_document_v2_spans_read_write_catalogue_areas() -> None:
    ais_key = CatalogueKey(standard="open-banking", version="v4.0", api="ais")
    pis_key = CatalogueKey(standard="open-banking", version="v4.0", api="pis")
    ais_ref = EndpointRef(method="GET", path="/open-banking/v4.0/aisp/accounts")
    pis_ref = EndpointRef(method="POST", path="/open-banking/v4.0/pisp/domestic-payments")
    ais_case = _case(
        "ais-accounts-read",
        endpoint_refs=(ais_ref,),
        runtime_requirements=(RuntimeInputRequirement("resourceBaseUrl", "url", "AIS resource server base URL"),),
    )
    pis_case = _case(
        "pis-domestic-payment-submit",
        endpoint_refs=(pis_ref,),
        capability_ids=("pis.domestic-payment-submission",),
        runtime_requirements=(RuntimeInputRequirement("resourceBaseUrl", "url", "PIS resource server base URL"),),
    )
    ais_catalogue = _catalogue_with_key(ais_key, version="ais.1", test_cases=(ais_case,))
    pis_catalogue = _catalogue_with_key(
        pis_key,
        version="pis.1",
        test_cases=(pis_case,),
        capabilities=(
            EndpointCapability(
                capability_id="pis.domestic-payment-submission",
                label="Domestic payment submission",
                description="Optional domestic payment submission support.",
                required=False,
                endpoint_refs=(pis_ref,),
            ),
        ),
    )
    raw_spec: dict[str, JsonValue] = {
        "schemaVersion": "v2",
        "scheme": "open-banking-uk",
        "specification": "read-write",
        "version": "4.0.1",
        "securityProfile": "fapi1-advanced",
        "scope": {
            "resourceGroups": [
                {
                    "id": "ais.accounts",
                    "endpoints": [{"method": "GET", "path": "/open-banking/v4.0/aisp/accounts"}],
                },
                {
                    "id": "pis.domestic-payments",
                    "endpoints": [
                        {
                            "method": "POST",
                            "path": "/open-banking/v4.0/pisp/domestic-payments",
                            "capabilities": ["pis.domestic-payment-submission"],
                        }
                    ],
                },
            ]
        },
        "config": {"resourceBaseUrl": "https://rs.example.com"},
    }
    document = parse_test_plan_document(raw_spec)

    compiled = compile_test_plan_document(document, (ais_catalogue, pis_catalogue))

    assert compiled.catalogue_key == CatalogueKey(
        standard="open-banking-uk",
        version="4.0.1",
        api="read-write",
    )
    assert compiled.catalogue_version == "ais:ais.1; pis:pis.1"
    assert [case.test_case_id for case in compiled.test_cases] == [
        "ais-accounts-read",
        "pis-domestic-payment-submit",
    ]
    assert [endpoint.resource_group for endpoint in compiled.traceability.selected_endpoints] == [
        "ais.accounts",
        "pis.domestic-payments",
    ]
    assert [capability.capability_id for capability in compiled.traceability.selected_capabilities] == [
        "pis.domestic-payment-submission"
    ]
    assert [runtime_input.input_id for runtime_input in compiled.traceability.runtime_input_snapshot] == [
        "resourceBaseUrl"
    ]
    assert compiled.traceability.runtime_input_snapshot[0].value == "https://rs.example.com"
    assert compiled.certifying is True


@pytest.mark.unit
def test_compile_test_plan_document_v2_rejects_cvrp_resource_group_outside_open_banking_boundary() -> None:
    shared_ref = EndpointRef(method="POST", path="/domestic-vrp-consents")
    vrp_catalogue = _catalogue_with_key(
        CatalogueKey(standard="open-banking", version="v4.0", api="vrp"),
        version="vrp.1",
        test_cases=(_case("vrp-consent-create", endpoint_refs=(shared_ref,)),),
    )
    cvrp_catalogue = _catalogue_with_key(
        CatalogueKey(standard="open-banking", version="v4.0", api="cvrp"),
        version="cvrp.1",
        test_cases=(_case("cvrp-consent-create", endpoint_refs=(shared_ref,)),),
    )
    raw_spec: dict[str, JsonValue] = {
        "schemaVersion": "v2",
        "scheme": "open-banking-uk",
        "specification": "read-write",
        "version": "4.0.1",
        "securityProfile": "fapi1-advanced",
        "scope": {
            "resourceGroups": [
                {
                    "id": "vrp.domestic-vrp-consents",
                    "endpoints": [{"method": "POST", "path": "/domestic-vrp-consents"}],
                },
                {
                    "id": "cvrp.domestic-vrp-consents",
                    "endpoints": [{"method": "POST", "path": "/domestic-vrp-consents"}],
                },
            ]
        },
        "config": {},
    }
    document = parse_test_plan_document(raw_spec)

    with pytest.raises(
        CatalogueError,
        match="Resource group 'cvrp.domestic-vrp-consents' is not available for this plan boundary",
    ):
        compile_test_plan_document(document, (vrp_catalogue, cvrp_catalogue))


@pytest.mark.unit
def test_compile_test_plan_document_v2_rejects_endpoint_outside_selected_boundary() -> None:
    raw_spec: dict[str, JsonValue] = {
        "schemaVersion": "v2",
        "scheme": "open-banking-uk",
        "specification": "read-write",
        "version": "4.0.1",
        "securityProfile": "fapi1-advanced",
        "scope": {
            "resourceGroups": [
                {
                    "id": "ais.accounts",
                    "endpoints": [{"method": "GET", "path": "/open-banking/v4.0/aisp/unknown"}],
                }
            ]
        },
        "config": {},
    }
    document = parse_test_plan_document(raw_spec)

    with pytest.raises(
        CatalogueError,
        match="Resource group 'ais.accounts' does not contain endpoint GET /open-banking/v4.0/aisp/unknown",
    ):
        compile_test_plan_document(document, (_catalogue(_case("accounts-read")),))


@pytest.mark.unit
def test_parse_test_plan_spec_rejects_duplicate_endpoint_capability_selection() -> None:
    raw_spec: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "catalogue": {"standard": "open-banking", "version": "v4.0", "api": "ais"},
        "securityProfile": "fapi1-advanced",
        "implementedEndpoints": [
            {
                "method": "GET",
                "path": "/accounts",
                "resourceGroup": "Accounts",
                "capabilities": ["accounts.balances", "accounts.balances"],
            }
        ],
    }

    with pytest.raises(CatalogueError, match="duplicates capability 'accounts.balances'"):
        parse_test_plan_spec(raw_spec)


@pytest.mark.unit
def test_parse_test_plan_spec_rejects_duplicate_endpoint_selection() -> None:
    raw_spec: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "catalogue": {"standard": "open-banking", "version": "v4.0", "api": "ais"},
        "securityProfile": "fapi1-advanced",
        "implementedEndpoints": [
            {"method": "GET", "path": "/accounts", "resourceGroup": "Accounts"},
            {"method": "get", "path": "/accounts/", "resourceGroup": "Accounts"},
        ],
    }

    with pytest.raises(CatalogueError, match="duplicates implemented endpoint GET /accounts"):
        parse_test_plan_spec(raw_spec)
