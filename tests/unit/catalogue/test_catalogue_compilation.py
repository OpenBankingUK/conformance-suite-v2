"""Compilation of a test plan against a catalogue: selection, capabilities, and runtime inputs."""

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
    RuntimeInputRequirement,
    SecurityProfile,
    SecurityProfileApplicability,
    TestCaseApplicability,
    TestCatalogue,
    TestPlanSpec,
    compile_test_plan,
    compile_test_plan_document,
    parse_test_plan_document,
)
from conformance.json_types import JsonValue

pytestmark = pytest.mark.unit

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


def test_compile_rejects_unsupported_request_header_runtime_inputs() -> None:
    """Header-like runtime inputs must be declared by the selected catalogue."""
    catalogue = _catalogue(_case("accounts-list"))

    with pytest.raises(
        CatalogueError,
        match="Unsupported request header runtime input\\(s\\) for selected scope: xFapiFinancialId",
    ):
        compile_test_plan(catalogue, _spec(runtime_inputs={"xFapiFinancialId": "financial-id"}))


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


def test_compile_filters_applicable_cases_by_specification_version() -> None:
    account_ref = EndpointRef(method="GET", path="/open-banking/v4.0/aisp/accounts")
    v31_case = _case("accounts-read-v31", endpoint_refs=(account_ref,), specification_versions=("3.1.11",))
    v40_case = _case("accounts-read-v40", endpoint_refs=(account_ref,), specification_versions=("4.0.1",))

    compiled = compile_test_plan(_catalogue(v31_case, v40_case), _spec(specification_version="4.0.1"))

    assert [case.test_case_id for case in compiled.test_cases] == ["accounts-read-v40"]
    decisions = {decision.test_case_id: decision for decision in compiled.traceability.applicability_decisions}
    assert decisions["accounts-read-v31"].selected is False
    assert decisions["accounts-read-v31"].reason == "not applicable to specification version 4.0.1"


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


def test_compile_rejects_unknown_selected_capability() -> None:
    endpoint = ImplementedEndpoint(
        method="GET",
        path="/open-banking/v4.0/aisp/accounts",
        resource_group="Accounts",
        capability_ids=("accounts.unknown",),
    )

    with pytest.raises(CatalogueError, match="unknown capability 'accounts.unknown'"):
        compile_test_plan(_catalogue(_case("accounts-read")), _spec(endpoints=(endpoint,)))


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


def test_compile_rejects_mandatory_applicable_deselection() -> None:
    account_case = _case(
        "accounts-read",
        endpoint_refs=(EndpointRef(method="GET", path="/open-banking/v4.0/aisp/accounts"),),
        mandatory=True,
    )

    with pytest.raises(CatalogueError, match="Mandatory applicable test case 'accounts-read' cannot be deselected"):
        compile_test_plan(_catalogue(account_case), _spec(deselected=("accounts-read",)))


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


def test_compile_rejects_duplicate_catalogue_ids() -> None:
    duplicate = _case("accounts-read")

    with pytest.raises(CatalogueError, match="duplicate"):
        compile_test_plan(_catalogue(duplicate, duplicate), _spec())


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
