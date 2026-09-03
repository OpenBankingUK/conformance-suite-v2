"""Focused tests for the executable Open Banking DCR 3.4 catalogue."""

from __future__ import annotations

from collections.abc import Iterable

import pytest

from conformance.catalogue import (
    CatalogueKey,
    CompiledTestPlan,
    EndpointRef,
    PlanDocumentV2,
    TestCatalogue,
    compile_test_plan_document,
    parse_test_plan_document,
)
from conformance.catalogues.dcr import DCR_3_4_CATALOGUE
from conformance.json_types import JsonObject, JsonValue

_EXPECTED_SCENARIOS = (
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
)

_EXPECTED_STATUSES = (
    (),
    (201,),
    (200,),
    (204,),
    (201,),
    (200,),
    (204,),
    (401,),
    (400,),
    (400,),
    (400,),
    (400,),
    (400,),
    (201,),
    (200,),
    (200,),
    (204,),
    (201,),
    (401,),
    (200,),
    (204,),
    (201,),
    (200,),
    (200,),
    (204,),
    (201,),
    (200,),
    (204,),
    (401,),
    (201,),
    (200,),
    (204,),
    (401,),
    (400,),
)


def _dcr_plan(methods: Iterable[str]) -> JsonObject:
    """Build a canonical DCR plan selecting the supplied methods.

    Args:
        methods: Participant-selected direct DCR endpoint methods.

    Returns:
        Canonical family-discriminated DCR plan.
    """
    endpoints: list[JsonValue] = []
    operation_ids = {
        "POST": "RegisterClient",
        "GET": "GetClient",
        "PUT": "UpdateClient",
        "DELETE": "DeleteClient",
    }
    for method in methods:
        endpoints.append(
            {
                "method": method,
                "path": "/register" if method == "POST" else "/register/{ClientId}",
                "operationId": operation_ids[method],
                "required": method == "POST",
                "locked": method == "POST",
            }
        )
    return {
        "schemaVersion": "1.0",
        "specification": {
            "family": "OBL_DCR",
            "scheme": "open-banking-uk",
            "name": "dynamic-client-registration",
            "version": "3.4",
        },
        "executionMode": "certification",
        "securityEnvironment": {"discoveryUrl": "https://aspsp.example.com/.well-known/openid-configuration"},
        "endpoints": endpoints,
        "metadata": {},
    }


def _compile(methods: Iterable[str] = ("POST", "GET", "PUT", "DELETE")) -> CompiledTestPlan:
    """Compile a canonical DCR plan against the bundled DCR catalogue.

    Args:
        methods: Participant-selected direct DCR endpoint methods.

    Returns:
        Deterministically compiled DCR plan.
    """
    document = parse_test_plan_document(_dcr_plan(methods))
    assert isinstance(document, PlanDocumentV2)
    return compile_test_plan_document(document, (DCR_3_4_CATALOGUE,))


@pytest.mark.unit
def test_dcr_catalogue_has_exact_pinned_inventory_and_trace_ids() -> None:
    """Catalogue preserves all ten scenarios, 34 cases, and 79 ordered steps."""
    cases = DCR_3_4_CATALOGUE.test_cases
    groups = tuple(dict.fromkeys(case.trace_group.group_id for case in cases if case.trace_group is not None))
    step_ids = tuple(step.step_id for case in cases for step in case.execution_steps)

    assert groups == _EXPECTED_SCENARIOS
    assert len(cases) == 34
    assert len(step_ids) == 79
    assert len(set(step_ids)) == 79
    assert all(step.step_id.startswith(f"{case.test_case_id}-S") for case in cases for step in case.execution_steps)
    assert [case.test_case_id for case in cases if case.trace_group and case.trace_group.group_id == "DCR-011"] == [
        "DCR-011-C01"
    ]


@pytest.mark.unit
def test_dcr_full_endpoint_selection_compiles_all_cases_in_contract_order() -> None:
    """Full direct endpoint scope deterministically compiles every pinned case."""
    compiled = _compile()

    assert compiled.catalogue_key == CatalogueKey(
        standard="open-banking-uk",
        version="3.4",
        api="dynamic-client-registration",
    )
    assert compiled.traceability.generated_test_case_ids == tuple(
        case.test_case_id for case in DCR_3_4_CATALOGUE.test_cases
    )
    assert len(compiled.test_cases) == 34
    assert sum(len(case.execution_steps) for case in compiled.test_cases) == 79


@pytest.mark.unit
@pytest.mark.parametrize(
    ("methods", "expected_count", "included", "excluded"),
    [
        (("POST",), 9, "DCR-004-C05", "DCR-005-C01"),
        (("POST", "GET"), 15, "DCR-005-C03", "DCR-005-C04"),
        (("POST", "PUT"), 12, "DCR-008-C03", "DCR-009-C01"),
        (("POST", "DELETE"), 10, "DCR-002-C03", "DCR-003-C01"),
    ],
)
def test_dcr_compile_gates_optional_endpoint_coverage(
    methods: tuple[str, ...],
    expected_count: int,
    included: str,
    excluded: str,
) -> None:
    """Optional management operations select only their applicable DCR coverage."""
    compiled = _compile(methods)
    selected = set(compiled.traceability.generated_test_case_ids)
    decisions = {decision.test_case_id: decision for decision in compiled.traceability.applicability_decisions}

    assert len(selected) == expected_count
    assert included in selected
    assert excluded not in selected
    assert decisions[excluded].reason == "no matching implemented endpoint"
    assert all(endpoint.path != "/token" for endpoint in compiled.traceability.selected_endpoints)
    assert excluded in {case.test_case_id for case in compiled.skipped_test_cases}
    assert not set(compiled.traceability.generated_test_case_ids) & {
        case.test_case_id for case in compiled.skipped_test_cases
    }


@pytest.mark.unit
def test_dcr_dependencies_and_generated_token_cases_are_ordered() -> None:
    """Registration and token prerequisites precede management operations."""
    compiled = _compile()
    positions = {test_case.test_case_id: index for index, test_case in enumerate(compiled.test_cases)}

    for test_case in compiled.test_cases:
        assert all(positions[dependency] < positions[test_case.test_case_id] for dependency in test_case.dependencies)
    assert positions["DCR-008-C01"] < positions["DCR-008-C02"] < positions["DCR-008-C03"]
    token_capability = next(
        capability
        for capability in DCR_3_4_CATALOGUE.runtime_capabilities
        if capability.capability_id == "dcr.client-credentials-token"
    )
    assert token_capability.generated_dependency is True
    assert token_capability.supported_values == (
        "tls_client_auth",
        "private_key_jwt",
        "client_secret_jwt",
        "client_secret_basic",
    )
    assert token_capability.unsupported_values == ("client_secret_post",)


@pytest.mark.unit
def test_dcr_catalogue_locks_status_state_and_response_assertions() -> None:
    """Cases retain locked statuses, state flow, and DCR 3.4 response rules."""
    cases = DCR_3_4_CATALOGUE.test_cases
    assert tuple(case.expected_http_statuses for case in cases) == _EXPECTED_STATUSES
    assert all(assertion.locked for case in cases for assertion in case.assertions)

    retrieve = next(case for case in cases if case.test_case_id == "DCR-005-C03")
    assert retrieve.state_inputs == ("clientId", "clientCredentialsAccessToken")
    response_assertion = next(
        assertion for assertion in retrieve.assertions if assertion.assertion_id == "DCR-005-C03-S03"
    )
    assert response_assertion.kind == "response_schema"
    assert response_assertion.rule["schema"] == "open-banking-dcr-3.4"
    assert response_assertion.rule["hostIndependent"] is True

    invalid_response_types = cases[-1]
    assert invalid_response_types.execution_steps[-1].deviation_id == "DCR-DEV-005"
    assert invalid_response_types.state_outputs == ()
    delete_case = next(case for case in cases if case.test_case_id == "DCR-002-C03")
    assert delete_case.execution_steps[0].sensitive is True
    assert delete_case.gate_absence_behavior == "skip"


@pytest.mark.unit
def test_dcr_catalogue_pins_provenance_gates_and_sensitive_requirements() -> None:
    """Catalogue metadata pins legacy/normative sources and secret requirements."""
    provenance = DCR_3_4_CATALOGUE.provenance
    assert provenance is not None
    assert provenance.release == "v1.4.0"
    assert provenance.commit == "cc00a0065494e8e180c915621b9996bc2259ec8d"
    assert provenance.references["ob-dcr-34"].startswith("https://openbankinguk.github.io/dcr-docs-pub/v3.4/")
    assert all(
        reference_id in provenance.references
        for case in DCR_3_4_CATALOGUE.test_cases
        if case.trace_group is not None
        for reference_id in case.trace_group.normative_reference_ids
    )

    dcr_009 = next(case for case in DCR_3_4_CATALOGUE.test_cases if case.test_case_id == "DCR-009-C04")
    assert dcr_009.trace_group is not None
    assert dcr_009.trace_group.required_endpoint_refs == (
        EndpointRef("POST", "/register"),
        EndpointRef("PUT", "/register/{ClientId}"),
        EndpointRef("DELETE", "/register/{ClientId}"),
    )
    requirements = {
        requirement.config_path: requirement for requirement in DCR_3_4_CATALOGUE.configuration_requirements
    }
    assert requirements["dynamicClientRegistration.softwareStatementAssertionPath"].sensitive is True
    assert requirements["dynamicClientRegistration.registrationAudience"].required is True
    assert requirements["securityEnvironment.signingPrivateKeyPath"].sensitive is True
    assert requirements["securityEnvironment.mtls.privateKeyPath"].sensitive is True


@pytest.mark.unit
def test_read_write_catalogue_defaults_have_no_dcr_trace_metadata() -> None:
    """Generic DCR metadata additions leave existing catalogue defaults empty."""
    empty_catalogue = TestCatalogue(key=CatalogueKey("example", "v1", "api"), catalogue_version="1", test_cases=())

    assert empty_catalogue.configuration_requirements == ()
    assert empty_catalogue.runtime_capabilities == ()
    assert empty_catalogue.provenance is None
