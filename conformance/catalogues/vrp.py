"""Legacy FCS-derived catalogue coverage for VRP and cVRP APIs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from conformance.catalogue import (
    AssertionKind,
    CatalogueAssertion,
    CatalogueKey,
    CatalogueRequestStep,
    CatalogueTestCase,
    EndpointCapability,
    EndpointRef,
    HttpMethod,
    RuntimeInputRequirement,
    SecurityProfileApplicability,
    TestCaseApplicability,
    TestCaseRole,
    TestCatalogue,
)
from conformance.catalogues.common import open_banking_request_headers_for
from conformance.json_types import JsonValue

type _CatalogueFamily = Literal["vrp", "cvrp"]
"""Catalogue families represented in this module."""

_CATALOGUE_VERSION = "2026.07.legacy-fcs-vrp-cvrp.1"
"""Content version for the imported legacy VRP/cVRP catalogue coverage."""

_VRP_V31_MANIFEST = "manifests/ob_3.1_variable_recurring_payments.json"
"""Legacy FCS manifest path for OB Read/Write v3.1 VRP coverage."""

_VRP_V40_MANIFEST = "manifests/ob_4.0_variable_recurring_payments.json"
"""Legacy FCS manifest path for OB Read/Write v4.0 VRP coverage."""

_CVRP_V40_MANIFEST = "manifests/cVRP_4.0_variable_recurring_payments.json"
"""Legacy FCS manifest path for OB Read/Write v4.0 cVRP coverage."""

_RESOURCE_BASE_URL = RuntimeInputRequirement(
    input_id="resourceBaseUrl",
    input_type="url",
    label="Resource Server base URL",
)
"""Shared runtime requirement for the ASPSP resource-server endpoint base URL."""

_ACCESS_TOKEN = RuntimeInputRequirement(
    input_id="accessToken",
    input_type="string",
    label="Authorised access token",
    sensitive=True,
    source="token",
)
"""Shared runtime requirement for an OAuth2 access token used in API calls."""

_CONSENT_ID = RuntimeInputRequirement(
    input_id="domesticVrpConsentId",
    input_type="string",
    label="Domestic VRP consent identifier",
    required=False,
    source="captured",
)
"""Runtime identifier for VRP consent resources captured or supplied at execution time."""

_INITIAL_PAYMENT_ID = RuntimeInputRequirement(
    input_id="domesticVrpInitialPaymentId",
    input_type="string",
    label="Initial domestic VRP payment identifier",
    required=False,
    source="captured",
)
"""Runtime identifier for the first created domestic VRP payment resource."""

_REPEATED_PAYMENT_ID = RuntimeInputRequirement(
    input_id="domesticVrpRepeatedPaymentId",
    input_type="string",
    label="Repeated domestic VRP payment identifier",
    required=False,
    source="captured",
)
_VRP_RESOURCE_AUTH_ID = "vrp-payment-access"
"""Semantic authorization id for VRP and cVRP resource API requests."""

_VRP_CAPTURED_PATH_VALUES = {
    "{consentId}": "${steps.vrp-consent-create-awaiting-authorisation-request.response.body.Data.ConsentId}",
    "{initialPaymentId}": (
        "${steps.vrp-initial-payment-create-request.response.body.Data.DomesticVRPId}"
    ),
    "{repeatedPaymentId}": (
        "${steps.vrp-repeated-payment-create-request.response.body.Data.DomesticVRPId}"
    ),
}
"""Captured response-field placeholders for VRP path parameters."""
"""Runtime identifier for repeated domestic VRP payment resources."""

_VRP_CORE_CAPABILITY = "vrp.core"
"""Baseline capability for domestic VRP/cVRP endpoint coverage."""

_VRP_FUNDS_CONFIRMATION_CAPABILITY = "vrp.funds-confirmation"
"""Optional capability for funds-confirmation support on VRP endpoints."""

_CVRP_CORE_CAPABILITY = "cvrp.core"
"""Baseline capability for domestic cVRP endpoint coverage."""

_CVRP_FUNDS_CONFIRMATION_CAPABILITY = "cvrp.funds-confirmation"
"""Optional capability for funds-confirmation support on cVRP endpoints."""

_LEGACY_ASSERTIONS: dict[str, tuple[AssertionKind, str, dict[str, JsonValue]]] = {
    "OB3GLOAssertOn200": (
        "http_status",
        "Expected HTTP 200 (OK).",
        {"expected": 200},
    ),
    "OB3GLOAssertOn201": (
        "http_status",
        "Expected HTTP 201 (Created).",
        {"expected": 201},
    ),
    "OB3GLOAssertOn204": (
        "http_status",
        "Expected HTTP 204 (No Content).",
        {"expected": 204},
    ),
    "OB3GLOAssertOn400": (
        "http_status",
        "Expected HTTP 400 (Bad Request) for invalid/deleted resources.",
        {"expected": 400},
    ),
    "OB3GLOFAPIHeader": (
        "header",
        "Expected x-fapi-interaction-id response header.",
        {"header": "x-fapi-interaction-id", "presence": "required"},
    ),
    "OB3GLOAssertContentType": (
        "header",
        "Expected JSON content type response header.",
        {"header": "content-type", "expected": "application/json; charset=utf-8"},
    ),
    "OB3DOPAssertAwaitingAuthorisation": (
        "json_field",
        "Expected Data.Status to be AwaitingAuthorisation.",
        {"path": "Data.Status", "expected": "AwaitingAuthorisation"},
    ),
    "OB3DOPAssertAwaitingAuthorisationV4": (
        "json_field",
        "Expected Data.Status to be AWAU.",
        {"path": "Data.Status", "expected": "AWAU"},
    ),
    "OB3DOPAssertAuthorised": (
        "json_field",
        "Expected Data.Status to be Authorised.",
        {"path": "Data.Status", "expected": "Authorised"},
    ),
    "OB3DOPAssertAuthorisedV4": (
        "json_field",
        "Expected Data.Status to be AUTH.",
        {"path": "Data.Status", "expected": "AUTH"},
    ),
    "OB3GLOAAssertConsentId": (
        "json_field",
        "Expected Data.ConsentId to be present.",
        {"path": "Data.ConsentId", "expected": "present"},
    ),
}
"""Subset of legacy assertion definitions used by the imported VRP/cVRP cases."""


@dataclass(frozen=True)
class _LegacyCaseBlueprint:
    """Blueprint for building equivalent VRP and cVRP catalogue test cases.

    Attributes:
        id_suffix: Stable case id suffix shared by VRP and cVRP cases.
        name: Human-readable test case name.
        method: HTTP method covered by the case.
        path: Operation path covered by the case.
        role: Catalogue execution role.
        dependencies: Case suffixes this case depends on.
        mandatory: Whether the case is mandatory when applicable.
        runtime_input_requirements: Runtime inputs needed for execution.
        assertion_ids: Legacy assertion identifiers attached to the case.
        requires_funds_confirmation_capability: Whether the case depends on the
            optional funds-confirmation implementation feature.
        legacy_vrp_sources: Legacy manifest/script references for VRP provenance.
        legacy_cvrp_sources: Legacy manifest/script references for cVRP provenance.
    """

    id_suffix: str
    name: str
    method: HttpMethod
    path: str
    role: TestCaseRole = "resource"
    dependencies: tuple[str, ...] = ()
    mandatory: bool = True
    runtime_input_requirements: tuple[RuntimeInputRequirement, ...] = ()
    assertion_ids: tuple[str, ...] = ()
    requires_funds_confirmation_capability: bool = False
    legacy_vrp_sources: tuple[tuple[str, str], ...] = ()
    legacy_cvrp_sources: tuple[tuple[str, str], ...] = ()


_BLUEPRINTS: tuple[_LegacyCaseBlueprint, ...] = (
    _LegacyCaseBlueprint(
        id_suffix="consent-create-awaiting-authorisation",
        name="Create domestic VRP consent in awaiting-authorisation state",
        method="POST",
        path="/domestic-vrp-consents",
        runtime_input_requirements=(_RESOURCE_BASE_URL, _ACCESS_TOKEN),
        assertion_ids=(
            "OB3GLOAssertOn201",
            "OB3GLOFAPIHeader",
            "OB3DOPAssertAwaitingAuthorisationV4",
            "OB3GLOAAssertConsentId",
        ),
        legacy_vrp_sources=(
            (_VRP_V31_MANIFEST, "OB-301-VRP-100100"),
            (_VRP_V31_MANIFEST, "OB-301-VRP-100101"),
            (_VRP_V40_MANIFEST, "OB-400-VRP-100100"),
        ),
        legacy_cvrp_sources=((_CVRP_V40_MANIFEST, "OB-400-CVRP-100100"),),
    ),
    _LegacyCaseBlueprint(
        id_suffix="payment-create-initial",
        name="Create first domestic VRP payment from authorised consent",
        method="POST",
        path="/domestic-vrps",
        dependencies=("consent-create-awaiting-authorisation",),
        runtime_input_requirements=(_RESOURCE_BASE_URL, _ACCESS_TOKEN, _CONSENT_ID),
        assertion_ids=("OB3GLOAssertOn201",),
        legacy_vrp_sources=(
            (_VRP_V31_MANIFEST, "OB-301-VRP-100600"),
            (_VRP_V31_MANIFEST, "OB-301-VRP-100601"),
            (_VRP_V40_MANIFEST, "OB-400-VRP-100600"),
        ),
        legacy_cvrp_sources=((_CVRP_V40_MANIFEST, "OB-400-CVRP-100600"),),
    ),
    _LegacyCaseBlueprint(
        id_suffix="consent-get-authorised",
        name="Retrieve authorised domestic VRP consent",
        method="GET",
        path="/domestic-vrp-consents/{consentId}",
        dependencies=("consent-create-awaiting-authorisation",),
        runtime_input_requirements=(_RESOURCE_BASE_URL, _ACCESS_TOKEN, _CONSENT_ID),
        assertion_ids=(
            "OB3GLOAssertOn200",
            "OB3GLOFAPIHeader",
            "OB3GLOAssertContentType",
            "OB3DOPAssertAuthorisedV4",
        ),
        legacy_vrp_sources=(
            (_VRP_V31_MANIFEST, "OB-301-VRP-100610"),
            (_VRP_V40_MANIFEST, "OB-400-VRP-100610"),
        ),
        legacy_cvrp_sources=((_CVRP_V40_MANIFEST, "OB-400-CVRP-100610"),),
    ),
    _LegacyCaseBlueprint(
        id_suffix="consent-funds-confirmation",
        name="Confirm domestic VRP consent funds",
        method="POST",
        path="/domestic-vrp-consents/{consentId}/funds-confirmation",
        dependencies=("consent-create-awaiting-authorisation",),
        runtime_input_requirements=(_RESOURCE_BASE_URL, _ACCESS_TOKEN, _CONSENT_ID),
        legacy_vrp_sources=(
            (_VRP_V31_MANIFEST, "OB-301-VRP-100650"),
            (_VRP_V40_MANIFEST, "OB-400-VRP-100650"),
        ),
        requires_funds_confirmation_capability=True,
        legacy_cvrp_sources=((_CVRP_V40_MANIFEST, "OB-400-CVRP-100650"),),
    ),
    _LegacyCaseBlueprint(
        id_suffix="payment-get-initial",
        name="Retrieve initial domestic VRP payment",
        method="GET",
        path="/domestic-vrps/{vrpId}",
        dependencies=("payment-create-initial",),
        runtime_input_requirements=(_RESOURCE_BASE_URL, _ACCESS_TOKEN, _INITIAL_PAYMENT_ID),
        assertion_ids=("OB3GLOAssertOn200", "OB3GLOFAPIHeader", "OB3GLOAssertContentType"),
        legacy_vrp_sources=(
            (_VRP_V31_MANIFEST, "OB-301-VRP-10670"),
            (_VRP_V40_MANIFEST, "OB-400-VRP-10170"),
        ),
        legacy_cvrp_sources=((_CVRP_V40_MANIFEST, "OB-400-CVRP-10170"),),
    ),
    _LegacyCaseBlueprint(
        id_suffix="payment-create-repeated",
        name="Create repeated domestic VRP payment",
        method="POST",
        path="/domestic-vrps",
        dependencies=("payment-create-initial",),
        runtime_input_requirements=(_RESOURCE_BASE_URL, _ACCESS_TOKEN, _CONSENT_ID),
        assertion_ids=("OB3GLOAssertOn201",),
        legacy_vrp_sources=(
            (_VRP_V31_MANIFEST, "OB-301-VRP-100700"),
            (_VRP_V31_MANIFEST, "OB-301-VRP-100701"),
            (_VRP_V40_MANIFEST, "OB-400-VRP-100700"),
        ),
        legacy_cvrp_sources=((_CVRP_V40_MANIFEST, "OB-400-CVRP-100700"),),
    ),
    _LegacyCaseBlueprint(
        id_suffix="payment-get-repeated",
        name="Retrieve repeated domestic VRP payment",
        method="GET",
        path="/domestic-vrps/{vrpId}",
        dependencies=("payment-create-repeated",),
        runtime_input_requirements=(_RESOURCE_BASE_URL, _ACCESS_TOKEN, _REPEATED_PAYMENT_ID),
        assertion_ids=("OB3GLOAssertOn200", "OB3GLOFAPIHeader", "OB3GLOAssertContentType"),
        legacy_vrp_sources=(
            (_VRP_V31_MANIFEST, "OB-301-VRP-101100"),
            (_VRP_V40_MANIFEST, "OB-400-VRP-101100"),
        ),
        legacy_cvrp_sources=((_CVRP_V40_MANIFEST, "OB-400-CVRP-101100"),),
    ),
    _LegacyCaseBlueprint(
        id_suffix="payment-get-details",
        name="Retrieve domestic VRP payment details",
        method="GET",
        path="/domestic-vrps/{vrpId}/payment-details",
        dependencies=("payment-get-repeated",),
        runtime_input_requirements=(_RESOURCE_BASE_URL, _ACCESS_TOKEN, _REPEATED_PAYMENT_ID),
        assertion_ids=("OB3GLOAssertOn200", "OB3GLOFAPIHeader", "OB3GLOAssertContentType"),
        legacy_vrp_sources=(
            (_VRP_V31_MANIFEST, "OB-301-VRP-101200"),
            (_VRP_V40_MANIFEST, "OB-400-VRP-101200"),
        ),
        legacy_cvrp_sources=((_CVRP_V40_MANIFEST, "OB-400-CVRP-101200"),),
    ),
    _LegacyCaseBlueprint(
        id_suffix="consent-delete",
        name="Delete domestic VRP consent",
        method="DELETE",
        path="/domestic-vrp-consents/{consentId}",
        dependencies=("consent-create-awaiting-authorisation",),
        runtime_input_requirements=(_RESOURCE_BASE_URL, _ACCESS_TOKEN, _CONSENT_ID),
        assertion_ids=("OB3GLOAssertOn204", "OB3GLOFAPIHeader"),
        legacy_vrp_sources=(
            (_VRP_V31_MANIFEST, "OB-301-VRP-102100"),
            (_VRP_V40_MANIFEST, "OB-400-VRP-102100"),
        ),
        legacy_cvrp_sources=((_CVRP_V40_MANIFEST, "OB-400-CVRP-102100"),),
    ),
    _LegacyCaseBlueprint(
        id_suffix="consent-get-after-delete",
        name="Retrieve deleted domestic VRP consent returns bad request",
        method="GET",
        path="/domestic-vrp-consents/{consentId}",
        dependencies=("consent-delete",),
        runtime_input_requirements=(_RESOURCE_BASE_URL, _ACCESS_TOKEN, _CONSENT_ID),
        assertion_ids=("OB3GLOAssertOn400",),
        legacy_vrp_sources=(
            (_VRP_V31_MANIFEST, "OB-301-VRP-102150"),
            (_VRP_V40_MANIFEST, "OB-400-VRP-102150"),
        ),
        legacy_cvrp_sources=((_CVRP_V40_MANIFEST, "OB-400-CVRP-102150"),),
    ),
    _LegacyCaseBlueprint(
        id_suffix="consent-delete-after-delete",
        name="Delete already deleted domestic VRP consent",
        method="DELETE",
        path="/domestic-vrp-consents/{consentId}",
        dependencies=("consent-delete",),
        runtime_input_requirements=(_RESOURCE_BASE_URL, _ACCESS_TOKEN, _CONSENT_ID),
        legacy_vrp_sources=(
            (_VRP_V31_MANIFEST, "OB-301-VRP-102200"),
            (_VRP_V40_MANIFEST, "OB-400-VRP-102200"),
        ),
        legacy_cvrp_sources=((_CVRP_V40_MANIFEST, "OB-400-CVRP-102200"),),
    ),
)
"""Legacy VRP/cVRP operation coverage blueprints mapped into the catalogue model."""

_RESPONSE_SIGNATURE_SCRIPT_IDS = frozenset(
    {
        "OB-301-VRP-100100",
        "OB-301-VRP-100101",
        "OB-301-VRP-100600",
        "OB-301-VRP-100601",
        "OB-301-VRP-100610",
        "OB-301-VRP-100650",
        "OB-301-VRP-10670",
        "OB-301-VRP-100700",
        "OB-301-VRP-100701",
        "OB-301-VRP-101100",
        "OB-301-VRP-101200",
        "OB-400-VRP-100100",
        "OB-400-VRP-100600",
        "OB-400-VRP-100610",
        "OB-400-VRP-100650",
        "OB-400-VRP-10170",
        "OB-400-VRP-100700",
        "OB-400-VRP-101100",
        "OB-400-VRP-101200",
    }
)
"""Legacy VRP script ids whose responses required JWS signature validation."""


def _legacy_manifest_scope_entry(manifest_path: str, script_id: str) -> str:
    """Build a stable compliance-scope entry for a legacy manifest script.

    Args:
        manifest_path: Repository-relative path to the legacy manifest JSON file.
        script_id: Legacy FCS script id from the manifest ``scripts`` array.

    Returns:
        A stable scope token containing manifest provenance and script id.
    """
    return f"legacy-manifest:{manifest_path}#{script_id}"


def _case_id(family: _CatalogueFamily, suffix: str) -> str:
    """Build a stable per-family catalogue test case id.

    Args:
        family: Catalogue family name.
        suffix: Blueprint id suffix shared between families.

    Returns:
        A family-scoped catalogue test case id.
    """
    return f"{family}-{suffix}"


def _build_assertions(assertion_ids: tuple[str, ...]) -> tuple[CatalogueAssertion, ...]:
    """Convert legacy assertion ids into catalogue assertion definitions.

    Args:
        assertion_ids: Legacy assertion ids referenced by a coverage blueprint.

    Returns:
        Tuple of catalogue assertions, preserving source order.

    Raises:
        ValueError: If a referenced legacy assertion id has no local mapping.
    """
    assertions: list[CatalogueAssertion] = []
    for legacy_assertion_id in assertion_ids:
        if legacy_assertion_id not in _LEGACY_ASSERTIONS:
            raise ValueError(f"Unsupported legacy assertion id: {legacy_assertion_id}")
        kind, description, rule = _LEGACY_ASSERTIONS[legacy_assertion_id]
        rule_with_provenance: dict[str, JsonValue] = {
            **rule,
            "legacyAssertionId": legacy_assertion_id,
            "legacyAssertionSource": "manifests/assertions.json",
        }
        assertions.append(
            CatalogueAssertion(
                assertion_id=f"legacy-{legacy_assertion_id.lower()}",
                kind=kind,
                description=description,
                rule=rule_with_provenance,
            )
        )
    return tuple(assertions)


def _build_compliance_scope(family: _CatalogueFamily, blueprint: _LegacyCaseBlueprint) -> tuple[str, ...]:
    """Build compliance-scope metadata for one family-specific test case.

    Args:
        family: Catalogue family name.
        blueprint: Legacy coverage blueprint being converted.

    Returns:
        Compliance-scope tokens including legacy provenance.
    """
    legacy_sources = blueprint.legacy_vrp_sources if family == "vrp" else blueprint.legacy_cvrp_sources
    return (
        f"legacy-fcs-family:{family}",
        *(
            _legacy_manifest_scope_entry(manifest_path=manifest_path, script_id=script_id)
            for manifest_path, script_id in legacy_sources
        ),
    )


def _build_family_capabilities(family: _CatalogueFamily) -> tuple[EndpointCapability, ...]:
    """Build endpoint capabilities for one VRP family.

    Args:
        family: Catalogue family name.

    Returns:
        Ordered capability definitions for the family.
    """
    if family == "vrp":
        baseline_capability_id = _VRP_CORE_CAPABILITY
        funds_confirmation_capability_id = _VRP_FUNDS_CONFIRMATION_CAPABILITY
    else:
        baseline_capability_id = _CVRP_CORE_CAPABILITY
        funds_confirmation_capability_id = _CVRP_FUNDS_CONFIRMATION_CAPABILITY
    endpoint_refs = tuple(EndpointRef(method=blueprint.method, path=blueprint.path) for blueprint in _BLUEPRINTS)
    return (
        EndpointCapability(
            capability_id=baseline_capability_id,
            label=f"{family.upper()} core coverage",
            description=f"Baseline domestic {family.upper()} consent and payment endpoint support.",
            required=True,
            endpoint_refs=endpoint_refs,
        ),
        EndpointCapability(
            capability_id=funds_confirmation_capability_id,
            label=f"{family.upper()} funds confirmation support",
            description=f"Optional domestic {family.upper()} funds-confirmation endpoint support.",
            required=False,
            endpoint_refs=(
                EndpointRef(
                    method="POST",
                    path="/domestic-vrp-consents/{consentId}/funds-confirmation",
                ),
            ),
        ),
    )


def _build_family_case(family: _CatalogueFamily, blueprint: _LegacyCaseBlueprint) -> CatalogueTestCase:
    """Build a family-specific catalogue test case from a legacy blueprint.

    Args:
        family: Catalogue family name.
        blueprint: Shared operation/assertion blueprint.

    Returns:
        A concrete catalogue test case for the chosen family.
    """
    runtime_input_refs = tuple(
        requirement.input_id for requirement in blueprint.runtime_input_requirements if requirement.source == "plan"
    )
    request_path = _path_with_captured_vrp_values(family, blueprint.path)
    required_token_id = _VRP_RESOURCE_AUTH_ID if _ACCESS_TOKEN in blueprint.runtime_input_requirements else None
    required_capability_ids = [_VRP_CORE_CAPABILITY] if family == "vrp" else [_CVRP_CORE_CAPABILITY]
    if blueprint.requires_funds_confirmation_capability:
        required_capability_ids.append(
            _VRP_FUNDS_CONFIRMATION_CAPABILITY if family == "vrp" else _CVRP_FUNDS_CONFIRMATION_CAPABILITY
        )
    return CatalogueTestCase(
        test_case_id=_case_id(family, blueprint.id_suffix),
        name=blueprint.name,
        role=blueprint.role,
        compliance_scope=_build_compliance_scope(family, blueprint),
        applicability=TestCaseApplicability(
            security_profiles=SecurityProfileApplicability(profiles=("all",)),
            endpoint_refs=(EndpointRef(method=blueprint.method, path=blueprint.path),),
            required_capability_ids=tuple(required_capability_ids),
        ),
        mandatory=blueprint.mandatory,
        dependencies=tuple(_case_id(family, suffix) for suffix in blueprint.dependencies),
        runtime_input_requirements=blueprint.runtime_input_requirements,
        request_steps=(
            CatalogueRequestStep(
                step_id=f"{_case_id(family, blueprint.id_suffix)}-request",
                name=blueprint.name,
                method=blueprint.method,
                path=request_path,
                runtime_input_refs=runtime_input_refs,
                headers=open_banking_request_headers_for(
                    require_idempotency=blueprint.method in {"POST", "PUT", "PATCH"}
                ),
                required_token_id=required_token_id,
            ),
        ),
        assertions=_build_assertions(blueprint.assertion_ids),
        response_signature_required=any(
            script_id in _RESPONSE_SIGNATURE_SCRIPT_IDS
            for _manifest_path, script_id in (
                blueprint.legacy_vrp_sources if family == "vrp" else blueprint.legacy_cvrp_sources
            )
        ),
    )


def _path_with_captured_vrp_values(family: _CatalogueFamily, path: str) -> str:
    """Return a VRP path with captured resource ids bound to this family.

    Args:
        family: Catalogue family name.
        path: Standards path template that may contain OpenAPI path variables.

    Returns:
        Request path containing execution-context placeholders for captured ids.
    """
    resolved_path = path
    for variable, placeholder in _VRP_CAPTURED_PATH_VALUES.items():
        resolved_path = resolved_path.replace(variable, placeholder.replace("steps.vrp-", f"steps.{family}-"))
    return resolved_path


def _build_family_cases(family: _CatalogueFamily) -> tuple[CatalogueTestCase, ...]:
    """Build the ordered test-case set for a VRP catalogue family.

    Args:
        family: Catalogue family name.

    Returns:
        Ordered catalogue test cases for the requested family.
    """
    return tuple(_build_family_case(family, blueprint) for blueprint in _BLUEPRINTS)


VRP_LEGACY_FCS_CATALOGUE = TestCatalogue(
    key=CatalogueKey(standard="open-banking", version="v4.0", api="vrp"),
    catalogue_version=_CATALOGUE_VERSION,
    test_cases=_build_family_cases("vrp"),
    capabilities=_build_family_capabilities("vrp"),
)
"""Catalogue mapping legacy OB v3.1/v4.0 VRP FCS coverage into the v2 model."""

CVRP_LEGACY_FCS_CATALOGUE = TestCatalogue(
    key=CatalogueKey(standard="open-banking", version="v4.0", api="cvrp"),
    catalogue_version=_CATALOGUE_VERSION,
    test_cases=_build_family_cases("cvrp"),
    capabilities=_build_family_capabilities("cvrp"),
)
"""Catalogue mapping legacy OB v4.0 cVRP FCS coverage into the v2 model."""

__all__ = ["CVRP_LEGACY_FCS_CATALOGUE", "VRP_LEGACY_FCS_CATALOGUE"]
