"""Unit tests for the Open Banking DCR 3.4 plan and registry foundation."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import cast

import pytest

from conformance.catalogue import (
    CatalogueKey,
    CatalogueTestCase,
    EndpointRef,
    HttpMethod,
    PlanDocumentV2,
    SecurityProfileApplicability,
    TestCaseApplicability,
    TestCatalogue,
    parse_test_plan_document,
    plan_document_to_json_object,
    supported_plan_document_boundaries,
)
from conformance.json_types import JsonObject, JsonValue
from conformance.specification_registry import (
    derived_security_profile_for_boundary,
    security_profiles_for_boundary,
    specification_for_boundary,
    specification_for_family,
    supported_specifications,
)
from conformance.test_plan_validation import (
    prepare_test_plan_for_run,
    validate_test_plan_for_load,
)


def _dcr_plan(
    *,
    optional_methods: tuple[str, ...] = ("GET", "PUT", "DELETE"),
    secure_root: Path = Path("/secure"),
) -> JsonObject:
    """Build a canonical Open Banking DCR 3.4 plan.

    Args:
        optional_methods: Optional management operations to select.
        secure_root: Absolute directory used for security file references.

    Returns:
        Canonical DCR plan with mandatory POST registration scope.
    """

    operation_ids = {
        "POST": "RegisterClient",
        "GET": "GetClient",
        "PUT": "UpdateClient",
        "DELETE": "DeleteClient",
    }
    endpoints: list[JsonValue] = [
        {
            "method": "POST",
            "path": "/register",
            "operationId": operation_ids["POST"],
            "required": True,
            "locked": True,
        }
    ]
    endpoints.extend(
        {
            "method": method,
            "path": "/register/{ClientId}",
            "operationId": operation_ids[method],
            "required": False,
            "locked": False,
        }
        for method in optional_methods
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
        "securityEnvironment": {
            "discoveryUrl": "https://aspsp.example.com/.well-known/openid-configuration",
            "clientAuthMethod": "private_key_jwt",
            "signingPrivateKeyPath": str(secure_root / "signing.key"),
            "signingKeyId": "kid",
            "mtls": {
                "enabled": True,
                "certificatePath": str(secure_root / "transport.crt"),
                "privateKeyPath": str(secure_root / "transport.key"),
            },
        },
        "endpoints": endpoints,
        "dynamicClientRegistration": {
            "softwareStatementAssertionPath": str(secure_root / "software-statement.jwt"),
            "registrationAudience": "aspsp123",
            "disableKeepAlive": False,
        },
        "metadata": {"aspspName": "Example Bank"},
    }


def _dcr_catalogue() -> TestCatalogue:
    """Build a narrow synthetic DCR catalogue for compiler-boundary tests.

    Returns:
        Catalogue with one case per participant-selectable DCR endpoint.
    """

    cases = tuple(
        CatalogueTestCase(
            test_case_id=f"dcr-{method.lower()}",
            name=f"DCR {method}",
            role="resource",
            compliance_scope=("DCR 3.4",),
            applicability=TestCaseApplicability(
                security_profiles=SecurityProfileApplicability(("all",)),
                endpoint_refs=(
                    EndpointRef(
                        method=cast(HttpMethod, method),
                        path="/register" if method == "POST" else "/register/{ClientId}",
                    ),
                ),
            ),
            mandatory=True,
        )
        for method in ("POST", "GET", "PUT", "DELETE")
    )
    return TestCatalogue(
        key=CatalogueKey(standard="open-banking", version="v3.4", api="dcr"),
        catalogue_version="dcr-foundation-test",
        test_cases=cases,
    )


@pytest.mark.unit
def test_specification_registry_covers_read_write_and_dcr_3_4() -> None:
    """Registry exposes only the approved families, versions, and scope policies."""
    definitions = supported_specifications()

    assert [definition.family for definition in definitions] == ["OBL_READ_WRITE", "OBL_DCR"]
    dcr = specification_for_family("OBL_DCR")
    assert dcr.specification == "dynamic-client-registration"
    assert dcr.uses_resource_groups is False
    assert dcr.scope_presentation == "direct-endpoints"
    assert dcr.execution_scheduling == "sequential"
    assert [version.version for version in dcr.versions] == ["3.4"]
    _definition, binding = specification_for_boundary("open-banking-uk", dcr.specification, "3.4")
    assert (binding.catalogue_standard, binding.catalogue_version, binding.catalogue_apis) == (
        "open-banking",
        "v3.4",
        ("dcr",),
    )
    assert security_profiles_for_boundary("open-banking-uk", "read-write", "4.0.1") == ("fapi1-advanced",)
    assert derived_security_profile_for_boundary("open-banking-uk", dcr.specification, "3.4") == "all"


@pytest.mark.unit
def test_dcr_plan_round_trips_direct_endpoint_scope() -> None:
    """DCR plans preserve mandatory, optional, and DCR-owned canonical fields."""
    raw_plan = _dcr_plan()

    document = parse_test_plan_document(raw_plan)

    assert isinstance(document, PlanDocumentV2)
    assert document.specification == "dynamic-client-registration"
    assert document.security_profile == "all"
    assert document.resource_groups == ()
    assert [endpoint.method for endpoint in document.endpoints] == ["POST", "GET", "PUT", "DELETE"]
    assert document.endpoints[0].required is True
    assert document.endpoints[0].locked is True
    assert plan_document_to_json_object(document) == raw_plan


@pytest.mark.unit
def test_dcr_optional_management_endpoints_compile_through_shared_boundary(tmp_path: Path) -> None:
    """Direct DCR endpoint selections compile without a synthetic resource group."""
    (tmp_path / "transport.crt").touch()
    (tmp_path / "transport.key").touch()
    (tmp_path / "signing.key").touch()
    (tmp_path / "software-statement.jwt").touch()
    raw_plan = _dcr_plan(optional_methods=("GET",), secure_root=tmp_path)
    catalogue = _dcr_catalogue()

    prepared = prepare_test_plan_for_run(raw_plan, base_dir=tmp_path, catalogues=(catalogue,))

    assert prepared.validation.valid is True
    assert prepared.compiled_plan.catalogue_key == CatalogueKey(
        standard="open-banking-uk",
        version="3.4",
        api="dynamic-client-registration",
    )
    assert prepared.compiled_plan.traceability.generated_test_case_ids == ("dcr-post", "dcr-get")
    assert [endpoint.resource_group for endpoint in prepared.compiled_plan.traceability.selected_endpoints] == [
        "dynamic-client-registration",
        "dynamic-client-registration",
    ]


@pytest.mark.unit
def test_dcr_development_mode_requires_standards_audience() -> None:
    """Development plans preserve the Open Banking registration audience rule."""
    raw_plan = _dcr_plan()
    raw_plan["executionMode"] = "development"

    validation = validate_test_plan_for_load(raw_plan)

    assert validation.valid is True


@pytest.mark.unit
def test_dcr_development_mode_rejects_issuer_url_audience() -> None:
    """Development plans cannot weaken the Open Banking audience format."""
    raw_plan = _dcr_plan()
    raw_plan["executionMode"] = "development"
    dcr = cast(JsonObject, raw_plan["dynamicClientRegistration"])
    dcr["registrationAudience"] = "https://issuer.example.test"

    validation = validate_test_plan_for_load(raw_plan)

    assert validation.valid is False


@pytest.mark.unit
def test_dcr_boundary_is_exposed_only_with_a_matching_catalogue() -> None:
    """Compiler boundary discovery follows the registry catalogue binding."""
    boundaries = supported_plan_document_boundaries((_dcr_catalogue(),))

    assert len(boundaries) == 1
    assert (
        boundaries[0].scheme,
        boundaries[0].specification,
        boundaries[0].version,
    ) == ("open-banking-uk", "dynamic-client-registration", "3.4")


@pytest.mark.unit
@pytest.mark.parametrize(
    ("invalid_case", "expected_message"),
    [
        ("unsupported-version", "specification"),
        ("empty-endpoints", "endpoints"),
        ("missing-post", "required"),
        ("unlocked-post", "locked"),
        ("resource-groups", "resourceGroups"),
        ("token-endpoint", "/token"),
    ],
)
def test_dcr_schema_rejects_invalid_family_scope(invalid_case: str, expected_message: str) -> None:
    """DCR schema rejects unsupported versions and participant-editable scope."""
    raw_plan = deepcopy(_dcr_plan())
    specification = cast(JsonObject, raw_plan["specification"])
    endpoints = cast(list[JsonObject], raw_plan["endpoints"])
    if invalid_case == "unsupported-version":
        specification["version"] = "3.3"
    elif invalid_case == "empty-endpoints":
        raw_plan["endpoints"] = []
    elif invalid_case == "missing-post":
        endpoints.pop(0)
    elif invalid_case == "unlocked-post":
        endpoints[0]["locked"] = False
    elif invalid_case == "resource-groups":
        raw_plan["resourceGroups"] = ["AIS"]
    else:
        endpoints.append(
            {
                "method": "POST",
                "path": "/token",
                "required": False,
                "locked": False,
            }
        )

    validation = validate_test_plan_for_load(raw_plan)

    assert validation.valid is False
    assert any(expected_message in issue.message for issue in validation.issues)


@pytest.mark.unit
def test_read_write_family_still_requires_non_empty_resource_groups() -> None:
    """Read/Write plans cannot use DCR direct scope or an empty resource-group list."""
    raw_plan = _dcr_plan()
    raw_plan["specification"] = {
        "family": "OBL_READ_WRITE",
        "version": "4.0.1",
        "profile": "FAPI1_ADVANCED",
    }
    raw_plan.pop("endpoints")
    raw_plan.pop("dynamicClientRegistration")
    raw_plan["resourceGroups"] = []
    raw_plan["businessTestData"] = {}

    validation = validate_test_plan_for_load(raw_plan)

    assert validation.valid is False
    assert any("resourceGroups" in issue.message for issue in validation.issues)
