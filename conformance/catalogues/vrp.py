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
    GeneratedRuntimeValue,
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

_VRP_V31_CONSENTS_PATH = "/open-banking/v3.1/pisp/domestic-vrp-consents"
"""OB Read/Write v3.1 domestic VRP consent resource path."""

_VRP_V31_PAYMENTS_PATH = "/open-banking/v3.1/pisp/domestic-vrps"
"""OB Read/Write v3.1 domestic VRP payment resource path."""

_VRP_V40_CONSENTS_PATH = "/open-banking/v4.0/pisp/domestic-vrp-consents"
"""OB Read/Write v4.0 domestic VRP consent resource path."""

_VRP_V40_PAYMENTS_PATH = "/open-banking/v4.0/pisp/domestic-vrps"
"""OB Read/Write v4.0 domestic VRP payment resource path."""

_VRP_OPEN_BANKING_PATH_PREFIXES = ("/open-banking/v3.1/pisp", "/open-banking/v4.0/pisp")
"""Versioned Open Banking path prefixes used by executable VRP operations."""

_VRP_PRE_3111_SPECIFICATION_VERSIONS = ("3.1",)
"""User-facing Read/Write versions represented by pre-3.1.11 VRP scripts."""

_VRP_3111_SPECIFICATION_VERSIONS = ("3.1.11",)
"""User-facing Read/Write versions represented by v3.1.11 VRP scripts."""

_VRP_V40_SPECIFICATION_VERSIONS = ("4.0", "4.0.0", "4.0.1")
"""User-facing Read/Write v4 versions represented by v4.0 VRP scripts."""

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
"""Runtime identifier for repeated domestic VRP payment resources."""

_VRP_CREDITOR_SCHEME_NAME = RuntimeInputRequirement(
    input_id="vrpCreditorAccountSchemeName",
    input_type="string",
    label="VRP creditor account scheme name",
    description="Scheme name for the creditor account used in domestic VRP consent and payment bodies.",
)
"""VRP creditor-account scheme name used by legacy body templates."""

_VRP_CREDITOR_IDENTIFICATION = RuntimeInputRequirement(
    input_id="vrpCreditorAccountIdentification",
    input_type="string",
    label="VRP creditor account identification",
    description="Account identification for the creditor account used in domestic VRP consent and payment bodies.",
)
"""VRP creditor-account identification used by legacy body templates."""

_VRP_CREDITOR_NAME = RuntimeInputRequirement(
    input_id="vrpCreditorAccountName",
    input_type="string",
    label="VRP creditor account name",
    description="Account name for the creditor account used in domestic VRP consent and payment bodies.",
)
"""VRP creditor-account name used by legacy body templates."""

_VRP_INSTRUCTED_AMOUNT_AMOUNT = RuntimeInputRequirement(
    input_id="vrpInstructedAmountAmount",
    input_type="string",
    label="VRP instructed amount",
    description="Amount used in domestic VRP payment and funds-confirmation bodies.",
)
"""VRP instructed amount value used by legacy body templates."""

_VRP_INSTRUCTED_AMOUNT_CURRENCY = RuntimeInputRequirement(
    input_id="vrpInstructedAmountCurrency",
    input_type="string",
    label="VRP instructed amount currency",
    description="Currency used in domestic VRP consent, payment, and funds-confirmation bodies.",
)
"""VRP instructed amount currency used by legacy body templates."""

_VRP_VALID_FROM_DATE_TIME = RuntimeInputRequirement(
    input_id="vrpValidFromDateTime",
    input_type="string",
    label="VRP consent valid-from date time",
    description="ISO 8601 start date-time used in domestic VRP consent control parameters.",
)
"""VRP consent valid-from date-time used by legacy body templates."""

_VRP_VALID_TO_DATE_TIME = RuntimeInputRequirement(
    input_id="vrpValidToDateTime",
    input_type="string",
    label="VRP consent valid-to date time",
    description="ISO 8601 end date-time used in domestic VRP consent control parameters.",
)
"""VRP consent valid-to date-time used by legacy body templates."""

_VRP_BODY_INPUTS = (
    _VRP_CREDITOR_SCHEME_NAME,
    _VRP_CREDITOR_IDENTIFICATION,
    _VRP_CREDITOR_NAME,
    _VRP_INSTRUCTED_AMOUNT_AMOUNT,
    _VRP_INSTRUCTED_AMOUNT_CURRENCY,
)
"""Runtime inputs required by domestic VRP payment body templates."""

_VRP_CONSENT_BODY_INPUTS = (
    _RESOURCE_BASE_URL,
    _ACCESS_TOKEN,
    *_VRP_BODY_INPUTS,
    _VRP_VALID_FROM_DATE_TIME,
    _VRP_VALID_TO_DATE_TIME,
)
"""Runtime inputs required by domestic VRP consent body templates."""

_VRP_PAYMENT_BODY_INPUTS = (_RESOURCE_BASE_URL, _ACCESS_TOKEN, _CONSENT_ID, *_VRP_BODY_INPUTS)
"""Runtime inputs required by domestic VRP payment body templates."""

_VRP_FUNDS_CONFIRMATION_BODY_INPUTS = (
    _RESOURCE_BASE_URL,
    _ACCESS_TOKEN,
    _CONSENT_ID,
    _VRP_CREDITOR_IDENTIFICATION,
    _VRP_INSTRUCTED_AMOUNT_AMOUNT,
    _VRP_INSTRUCTED_AMOUNT_CURRENCY,
)
"""Runtime inputs required by domestic VRP funds-confirmation body templates."""

_VRP_GENERATED_INSTRUCTION_IDS: dict[str, GeneratedRuntimeValue] = {
    "instructionIdentification": "uuid4-hex",
    "endToEndIdentification": "uuid4-hex",
}
"""Generated identifiers used to keep repeated VRP runs collision-safe."""

_VRP_REMITTANCE_V31: dict[str, JsonValue] = {
    "Reference": "${runtime.vrpCreditorAccountIdentification}",
    "Unstructured": "Test Unstructured Data",
}
"""Legacy v3.1 VRP remittance-information shape."""

_VRP_REMITTANCE_V4: dict[str, JsonValue] = {
    "Structured": [
        {
            "CreditorReferenceInformation": {
                "Reference": "${runtime.vrpCreditorAccountIdentification}",
            },
        },
    ],
    "Unstructured": ["Test Unstructured Data"],
}
"""Legacy v4 VRP/cVRP remittance-information shape."""

_VRP_CREDITOR_ACCOUNT_TEMPLATE: dict[str, JsonValue] = {
    "SchemeName": "${runtime.vrpCreditorAccountSchemeName}",
    "Identification": "${runtime.vrpCreditorAccountIdentification}",
    "Name": "${runtime.vrpCreditorAccountName}",
}
"""Creditor account template shared by legacy VRP/cVRP request bodies."""

_VRP_INSTRUCTED_AMOUNT_TEMPLATE: dict[str, JsonValue] = {
    "Amount": "${runtime.vrpInstructedAmountAmount}",
    "Currency": "${runtime.vrpInstructedAmountCurrency}",
}
"""Instructed amount template shared by legacy VRP/cVRP request bodies."""

_VRP_RESOURCE_AUTH_ID = "vrp-payment-access"
"""Semantic authorization id for VRP and cVRP resource API requests."""

_VRP_PSU_AUTH_TOKEN_ID = "vrp-psu-payment-access"  # noqa: S105 - semantic token id
"""Semantic authorization-code token id for PSU-authorised VRP/cVRP resource calls."""

_VRP_CAPTURED_CONSENT_ID = "${steps.%s-request.response.body.Data.ConsentId}"
"""Template for consent-id placeholders captured from VRP consent-create steps."""

_VRP_CAPTURED_PAYMENT_ID = "${steps.%s-request.response.body.Data.DomesticVRPId}"
"""Template for payment-id placeholders captured from VRP payment-create steps."""


def _vrp_consent_body(*, vrp_type: str, remittance: dict[str, JsonValue]) -> dict[str, JsonValue]:
    """Build a legacy domestic VRP consent body template.

    Args:
        vrp_type: Standards VRP type value for the consent control parameters.
        remittance: Remittance-information structure for the selected API version.

    Returns:
        JSON body template for domestic VRP consent creation.
    """
    return {
        "Data": {
            "ControlParameters": {
                "PSUAuthenticationMethods": ["UK.OBIE.SCANotRequired"],
                "VRPType": [vrp_type],
                "ValidFromDateTime": "${runtime.vrpValidFromDateTime}",
                "ValidToDateTime": "${runtime.vrpValidToDateTime}",
                "MaximumIndividualAmount": {
                    "Amount": "10.00",
                    "Currency": "${runtime.vrpInstructedAmountCurrency}",
                },
                "PeriodicLimits": [
                    {
                        "Amount": "10.00",
                        "Currency": "GBP",
                        "PeriodAlignment": "Consent",
                        "PeriodType": "Week",
                    }
                ],
            },
            "Initiation": {
                "CreditorAccount": _VRP_CREDITOR_ACCOUNT_TEMPLATE,
                "RemittanceInformation": remittance,
            },
        },
        "Risk": {},
    }


def _vrp_payment_body(
    *,
    consent_case_suffix: str,
    vrp_type: str | None,
    remittance: dict[str, JsonValue],
) -> dict[str, JsonValue]:
    """Build a legacy domestic VRP payment body template.

    Args:
        consent_case_suffix: Case suffix whose consent response supplies the consent id.
        vrp_type: Optional standards VRP type value for v3.1.11+ and v4 bodies.
        remittance: Remittance-information structure for the selected API version.

    Returns:
        JSON body template for domestic VRP payment creation.
    """
    data: dict[str, JsonValue] = {
        "ConsentId": _VRP_CAPTURED_CONSENT_ID % f"vrp-{consent_case_suffix}",
        "PSUAuthenticationMethod": "UK.OBIE.SCANotRequired",
        "Initiation": {
            "CreditorAccount": _VRP_CREDITOR_ACCOUNT_TEMPLATE,
            "RemittanceInformation": remittance,
        },
        "Instruction": {
            "InstructionIdentification": "${generated.instructionIdentification}",
            "EndToEndIdentification": "${generated.endToEndIdentification}",
            "CreditorAccount": _VRP_CREDITOR_ACCOUNT_TEMPLATE,
            "InstructedAmount": _VRP_INSTRUCTED_AMOUNT_TEMPLATE,
            "RemittanceInformation": remittance,
        },
    }
    if vrp_type is not None:
        data["VRPType"] = vrp_type
    return {"Data": data, "Risk": {}}


def _vrp_funds_confirmation_body(consent_case_suffix: str) -> dict[str, JsonValue]:
    """Build a legacy domestic VRP funds-confirmation body template.

    Args:
        consent_case_suffix: Case suffix whose consent response supplies the consent id.

    Returns:
        JSON body template for domestic VRP funds confirmation.
    """
    return {
        "Data": {
            "ConsentId": _VRP_CAPTURED_CONSENT_ID % f"vrp-{consent_case_suffix}",
            "Reference": "${runtime.vrpCreditorAccountIdentification}",
            "InstructedAmount": _VRP_INSTRUCTED_AMOUNT_TEMPLATE,
        }
    }


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

_LEGACY_STATUS_ASSERTIONS = {
    "OB3GLOAssertOn200": 200,
    "OB3GLOAssertOn201": 201,
    "OB3GLOAssertOn204": 204,
    "OB3GLOAssertOn400": 400,
}
"""HTTP status codes represented by legacy VRP assertion identifiers."""

_VRP_V40_SCHEMA_CHECK_SCRIPT_IDS = frozenset(
    {
        "OB-400-VRP-100100",
        "OB-400-VRP-100600",
        "OB-400-VRP-100610",
        "OB-400-VRP-100650",
        "OB-400-VRP-10170",
        "OB-400-VRP-100700",
        "OB-400-VRP-101100",
        "OB-400-VRP-101200",
        "OB-400-VRP-102100",
        "OB-400-VRP-102150",
        "OB-400-VRP-102200",
    }
)
"""Legacy v4 VRP scripts that enabled response schema checks."""

_VRP_V40_RESPONSE_SCHEMA_REFS = {
    ("POST", "/domestic-vrp-consents", 201): "#/components/schemas/OBDomesticVRPConsentResponse",
    ("GET", "/domestic-vrp-consents/{ConsentId}", 200): "#/components/schemas/OBDomesticVRPConsentResponse",
    ("GET", "/domestic-vrp-consents/{ConsentId}", 400): "#/components/schemas/OBErrorResponse1",
    ("POST", "/domestic-vrp-consents/{ConsentId}/funds-confirmation", 201): (
        "#/components/schemas/OBVRPFundsConfirmationResponse"
    ),
    ("POST", "/domestic-vrps", 201): "#/components/schemas/OBDomesticVRPResponse",
    ("GET", "/domestic-vrps/{DomesticVRPId}", 200): "#/components/schemas/OBDomesticVRPResponse",
    ("GET", "/domestic-vrps/{DomesticVRPId}/payment-details", 200): "#/components/schemas/OBDomesticVRPDetails",
}
"""Bundled v4 VRP response schemas keyed by operation and status."""


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
        assertion_one_of_ids: Legacy assertion identifiers from
            ``asserts_one_of`` entries.
        requires_funds_confirmation_capability: Whether the case depends on the
            optional funds-confirmation implementation feature.
        body_template: Optional JSON request body template for write requests.
        generated_values: Generated runtime values referenced by the body template.
        required_token_id: Semantic token id required by the request step.
        consent_case_suffix: Case suffix whose consent id should replace
            ``{consentId}`` path variables.
        initial_payment_case_suffix: Case suffix whose payment id should replace
            ``{initialPaymentId}`` and ``{vrpId}`` path variables for initial
            payment reads.
        repeated_payment_case_suffix: Case suffix whose payment id should replace
            ``{repeatedPaymentId}`` and ``{vrpId}`` path variables for repeated
            payment reads.
        legacy_vrp_sources: Legacy manifest/script references for VRP provenance.
        legacy_cvrp_sources: Legacy manifest/script references for cVRP provenance.
        specification_versions: User-facing Read/Write specification versions
            this blueprint may execute for.
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
    assertion_one_of_ids: tuple[str, ...] = ()
    requires_funds_confirmation_capability: bool = False
    body_template: JsonValue | None = None
    generated_values: dict[str, GeneratedRuntimeValue] | None = None
    required_token_id: str | None = _VRP_RESOURCE_AUTH_ID
    consent_case_suffix: str | None = None
    initial_payment_case_suffix: str | None = None
    repeated_payment_case_suffix: str | None = None
    legacy_vrp_sources: tuple[tuple[str, str], ...] = ()
    legacy_cvrp_sources: tuple[tuple[str, str], ...] = ()
    specification_versions: tuple[str, ...] = _VRP_V40_SPECIFICATION_VERSIONS


_BLUEPRINTS: tuple[_LegacyCaseBlueprint, ...] = (
    _LegacyCaseBlueprint(
        id_suffix="consent-create-awaiting-authorisation-v31-pre-3111",
        name="Create v3.1 pre-3.1.11 domestic VRP consent in awaiting-authorisation state",
        method="POST",
        path=_VRP_V31_CONSENTS_PATH,
        runtime_input_requirements=_VRP_CONSENT_BODY_INPUTS,
        assertion_ids=(
            "OB3GLOAssertOn201",
            "OB3GLOFAPIHeader",
            "OB3DOPAssertAwaitingAuthorisation",
            "OB3GLOAAssertConsentId",
        ),
        body_template=_vrp_consent_body(vrp_type="UK.OBIE.VRPType.Sweeping", remittance=_VRP_REMITTANCE_V31),
        legacy_vrp_sources=((_VRP_V31_MANIFEST, "OB-301-VRP-100100"),),
        specification_versions=_VRP_PRE_3111_SPECIFICATION_VERSIONS,
    ),
    _LegacyCaseBlueprint(
        id_suffix="consent-create-awaiting-authorisation-v31-3111",
        name="Create v3.1.11 domestic VRP consent in awaiting-authorisation state",
        method="POST",
        path=_VRP_V31_CONSENTS_PATH,
        runtime_input_requirements=_VRP_CONSENT_BODY_INPUTS,
        assertion_ids=(
            "OB3GLOAssertOn201",
            "OB3GLOFAPIHeader",
            "OB3DOPAssertAwaitingAuthorisation",
            "OB3GLOAAssertConsentId",
        ),
        body_template=_vrp_consent_body(vrp_type="UK.OBIE.VRPType.Sweeping", remittance=_VRP_REMITTANCE_V31),
        legacy_vrp_sources=((_VRP_V31_MANIFEST, "OB-301-VRP-100101"),),
        specification_versions=_VRP_3111_SPECIFICATION_VERSIONS,
    ),
    _LegacyCaseBlueprint(
        id_suffix="consent-create-awaiting-authorisation-v4",
        name="Create v4 domestic VRP consent in awaiting-authorisation state",
        method="POST",
        path=_VRP_V40_CONSENTS_PATH,
        runtime_input_requirements=_VRP_CONSENT_BODY_INPUTS,
        assertion_ids=(
            "OB3GLOAssertOn201",
            "OB3GLOFAPIHeader",
            "OB3DOPAssertAwaitingAuthorisationV4",
            "OB3GLOAAssertConsentId",
        ),
        body_template=_vrp_consent_body(vrp_type="UK.OBIE.VRPType.Sweeping", remittance=_VRP_REMITTANCE_V4),
        legacy_vrp_sources=((_VRP_V40_MANIFEST, "OB-400-VRP-100100"),),
    ),
    _LegacyCaseBlueprint(
        id_suffix="consent-create-awaiting-authorisation-v4",
        name="Create v4 domestic cVRP consent in awaiting-authorisation state",
        method="POST",
        path=_VRP_V40_CONSENTS_PATH,
        runtime_input_requirements=_VRP_CONSENT_BODY_INPUTS,
        assertion_ids=(
            "OB3GLOAssertOn201",
            "OB3GLOFAPIHeader",
            "OB3DOPAssertAwaitingAuthorisationV4",
            "OB3GLOAAssertConsentId",
        ),
        body_template=_vrp_consent_body(vrp_type="UK.OBIE.VRPType.CVRP1", remittance=_VRP_REMITTANCE_V4),
        legacy_cvrp_sources=((_CVRP_V40_MANIFEST, "OB-400-CVRP-100100"),),
    ),
    _LegacyCaseBlueprint(
        id_suffix="payment-create-initial-v31-pre-3111",
        name="Create initial v3.1 pre-3.1.11 domestic VRP payment",
        method="POST",
        path=_VRP_V31_PAYMENTS_PATH,
        dependencies=("consent-create-awaiting-authorisation-v31-pre-3111",),
        runtime_input_requirements=_VRP_PAYMENT_BODY_INPUTS,
        assertion_ids=("OB3GLOAssertOn201",),
        body_template=_vrp_payment_body(
            consent_case_suffix="consent-create-awaiting-authorisation-v31-pre-3111",
            vrp_type=None,
            remittance=_VRP_REMITTANCE_V31,
        ),
        generated_values=_VRP_GENERATED_INSTRUCTION_IDS,
        required_token_id=_VRP_PSU_AUTH_TOKEN_ID,
        consent_case_suffix="consent-create-awaiting-authorisation-v31-pre-3111",
        legacy_vrp_sources=((_VRP_V31_MANIFEST, "OB-301-VRP-100600"),),
        specification_versions=_VRP_PRE_3111_SPECIFICATION_VERSIONS,
    ),
    _LegacyCaseBlueprint(
        id_suffix="payment-create-initial-v31-3111",
        name="Create initial v3.1.11 domestic VRP payment",
        method="POST",
        path=_VRP_V31_PAYMENTS_PATH,
        dependencies=("consent-create-awaiting-authorisation-v31-3111",),
        runtime_input_requirements=_VRP_PAYMENT_BODY_INPUTS,
        assertion_ids=("OB3GLOAssertOn201",),
        body_template=_vrp_payment_body(
            consent_case_suffix="consent-create-awaiting-authorisation-v31-3111",
            vrp_type="UK.OBIE.VRPType.Sweeping",
            remittance=_VRP_REMITTANCE_V31,
        ),
        generated_values=_VRP_GENERATED_INSTRUCTION_IDS,
        required_token_id=_VRP_PSU_AUTH_TOKEN_ID,
        consent_case_suffix="consent-create-awaiting-authorisation-v31-3111",
        legacy_vrp_sources=((_VRP_V31_MANIFEST, "OB-301-VRP-100601"),),
        specification_versions=_VRP_3111_SPECIFICATION_VERSIONS,
    ),
    _LegacyCaseBlueprint(
        id_suffix="payment-create-initial-v4",
        name="Create initial v4 domestic VRP payment",
        method="POST",
        path=_VRP_V40_PAYMENTS_PATH,
        dependencies=("consent-create-awaiting-authorisation-v4",),
        runtime_input_requirements=_VRP_PAYMENT_BODY_INPUTS,
        assertion_ids=("OB3GLOAssertOn201",),
        body_template=_vrp_payment_body(
            consent_case_suffix="consent-create-awaiting-authorisation-v4",
            vrp_type="UK.OBIE.VRPType.Sweeping",
            remittance=_VRP_REMITTANCE_V4,
        ),
        generated_values=_VRP_GENERATED_INSTRUCTION_IDS,
        required_token_id=_VRP_PSU_AUTH_TOKEN_ID,
        consent_case_suffix="consent-create-awaiting-authorisation-v4",
        legacy_vrp_sources=((_VRP_V40_MANIFEST, "OB-400-VRP-100600"),),
    ),
    _LegacyCaseBlueprint(
        id_suffix="payment-create-initial-v4",
        name="Create initial v4 domestic cVRP payment",
        method="POST",
        path=_VRP_V40_PAYMENTS_PATH,
        dependencies=("consent-create-awaiting-authorisation-v4",),
        runtime_input_requirements=_VRP_PAYMENT_BODY_INPUTS,
        assertion_ids=("OB3GLOAssertOn201",),
        body_template=_vrp_payment_body(
            consent_case_suffix="consent-create-awaiting-authorisation-v4",
            vrp_type="UK.OBIE.VRPType.CVRP1",
            remittance=_VRP_REMITTANCE_V4,
        ),
        generated_values=_VRP_GENERATED_INSTRUCTION_IDS,
        required_token_id=_VRP_PSU_AUTH_TOKEN_ID,
        consent_case_suffix="consent-create-awaiting-authorisation-v4",
        legacy_cvrp_sources=((_CVRP_V40_MANIFEST, "OB-400-CVRP-100600"),),
    ),
    _LegacyCaseBlueprint(
        id_suffix="consent-get-authorised",
        name="Retrieve authorised domestic VRP consent",
        method="GET",
        path=f"{_VRP_V40_CONSENTS_PATH}/{{consentId}}",
        dependencies=("consent-create-awaiting-authorisation-v4",),
        runtime_input_requirements=(_RESOURCE_BASE_URL, _ACCESS_TOKEN, _CONSENT_ID),
        assertion_ids=("OB3GLOAssertOn200", "OB3GLOFAPIHeader", "OB3GLOAssertContentType", "OB3DOPAssertAuthorisedV4"),
        consent_case_suffix="consent-create-awaiting-authorisation-v4",
        legacy_vrp_sources=((_VRP_V31_MANIFEST, "OB-301-VRP-100610"), (_VRP_V40_MANIFEST, "OB-400-VRP-100610")),
        legacy_cvrp_sources=((_CVRP_V40_MANIFEST, "OB-400-CVRP-100610"),),
    ),
    _LegacyCaseBlueprint(
        id_suffix="consent-funds-confirmation",
        name="Confirm domestic VRP consent funds",
        method="POST",
        path=f"{_VRP_V40_CONSENTS_PATH}/{{consentId}}/funds-confirmation",
        dependencies=("consent-create-awaiting-authorisation-v4",),
        runtime_input_requirements=_VRP_FUNDS_CONFIRMATION_BODY_INPUTS,
        body_template=_vrp_funds_confirmation_body("consent-create-awaiting-authorisation-v4"),
        consent_case_suffix="consent-create-awaiting-authorisation-v4",
        requires_funds_confirmation_capability=True,
        required_token_id=_VRP_PSU_AUTH_TOKEN_ID,
        assertion_one_of_ids=("OB3GLOAssertOn201",),
        legacy_vrp_sources=((_VRP_V31_MANIFEST, "OB-301-VRP-100650"), (_VRP_V40_MANIFEST, "OB-400-VRP-100650")),
        legacy_cvrp_sources=((_CVRP_V40_MANIFEST, "OB-400-CVRP-100650"),),
    ),
    _LegacyCaseBlueprint(
        id_suffix="payment-get-initial",
        name="Retrieve initial domestic VRP payment",
        method="GET",
        path=f"{_VRP_V40_PAYMENTS_PATH}/{{vrpId}}",
        dependencies=("payment-create-initial-v4",),
        runtime_input_requirements=(_RESOURCE_BASE_URL, _ACCESS_TOKEN, _INITIAL_PAYMENT_ID),
        assertion_ids=("OB3GLOAssertOn200", "OB3GLOFAPIHeader", "OB3GLOAssertContentType"),
        initial_payment_case_suffix="payment-create-initial-v4",
        legacy_vrp_sources=((_VRP_V31_MANIFEST, "OB-301-VRP-10670"), (_VRP_V40_MANIFEST, "OB-400-VRP-10170")),
        legacy_cvrp_sources=((_CVRP_V40_MANIFEST, "OB-400-CVRP-10170"),),
    ),
    _LegacyCaseBlueprint(
        id_suffix="payment-create-repeated-v31-pre-3111",
        name="Create repeated v3.1 pre-3.1.11 domestic VRP payment",
        method="POST",
        path=_VRP_V31_PAYMENTS_PATH,
        dependencies=("payment-create-initial-v31-pre-3111",),
        runtime_input_requirements=_VRP_PAYMENT_BODY_INPUTS,
        assertion_ids=("OB3GLOAssertOn201",),
        body_template=_vrp_payment_body(
            consent_case_suffix="consent-create-awaiting-authorisation-v31-pre-3111",
            vrp_type=None,
            remittance=_VRP_REMITTANCE_V31,
        ),
        generated_values=_VRP_GENERATED_INSTRUCTION_IDS,
        required_token_id=_VRP_PSU_AUTH_TOKEN_ID,
        consent_case_suffix="consent-create-awaiting-authorisation-v31-pre-3111",
        legacy_vrp_sources=((_VRP_V31_MANIFEST, "OB-301-VRP-100700"),),
        specification_versions=_VRP_PRE_3111_SPECIFICATION_VERSIONS,
    ),
    _LegacyCaseBlueprint(
        id_suffix="payment-create-repeated-v31-3111",
        name="Create repeated v3.1.11 domestic VRP payment",
        method="POST",
        path=_VRP_V31_PAYMENTS_PATH,
        dependencies=("payment-create-initial-v31-3111",),
        runtime_input_requirements=_VRP_PAYMENT_BODY_INPUTS,
        assertion_ids=("OB3GLOAssertOn201",),
        body_template=_vrp_payment_body(
            consent_case_suffix="consent-create-awaiting-authorisation-v31-3111",
            vrp_type="UK.OBIE.VRPType.Sweeping",
            remittance=_VRP_REMITTANCE_V31,
        ),
        generated_values=_VRP_GENERATED_INSTRUCTION_IDS,
        required_token_id=_VRP_PSU_AUTH_TOKEN_ID,
        consent_case_suffix="consent-create-awaiting-authorisation-v31-3111",
        legacy_vrp_sources=((_VRP_V31_MANIFEST, "OB-301-VRP-100701"),),
        specification_versions=_VRP_3111_SPECIFICATION_VERSIONS,
    ),
    _LegacyCaseBlueprint(
        id_suffix="payment-create-repeated-v4",
        name="Create repeated v4 domestic VRP payment",
        method="POST",
        path=_VRP_V40_PAYMENTS_PATH,
        dependencies=("payment-create-initial-v4",),
        runtime_input_requirements=_VRP_PAYMENT_BODY_INPUTS,
        assertion_ids=("OB3GLOAssertOn201",),
        body_template=_vrp_payment_body(
            consent_case_suffix="consent-create-awaiting-authorisation-v4",
            vrp_type="UK.OBIE.VRPType.Sweeping",
            remittance=_VRP_REMITTANCE_V4,
        ),
        generated_values=_VRP_GENERATED_INSTRUCTION_IDS,
        required_token_id=_VRP_PSU_AUTH_TOKEN_ID,
        consent_case_suffix="consent-create-awaiting-authorisation-v4",
        legacy_vrp_sources=((_VRP_V40_MANIFEST, "OB-400-VRP-100700"),),
    ),
    _LegacyCaseBlueprint(
        id_suffix="payment-create-repeated-v4",
        name="Create repeated v4 domestic cVRP payment",
        method="POST",
        path=_VRP_V40_PAYMENTS_PATH,
        dependencies=("payment-create-initial-v4",),
        runtime_input_requirements=_VRP_PAYMENT_BODY_INPUTS,
        assertion_ids=("OB3GLOAssertOn201",),
        body_template=_vrp_payment_body(
            consent_case_suffix="consent-create-awaiting-authorisation-v4",
            vrp_type="UK.OBIE.VRPType.CVRP1",
            remittance=_VRP_REMITTANCE_V4,
        ),
        generated_values=_VRP_GENERATED_INSTRUCTION_IDS,
        required_token_id=_VRP_PSU_AUTH_TOKEN_ID,
        consent_case_suffix="consent-create-awaiting-authorisation-v4",
        legacy_cvrp_sources=((_CVRP_V40_MANIFEST, "OB-400-CVRP-100700"),),
    ),
    _LegacyCaseBlueprint(
        id_suffix="payment-get-repeated",
        name="Retrieve repeated domestic VRP payment",
        method="GET",
        path=f"{_VRP_V40_PAYMENTS_PATH}/{{vrpId}}",
        dependencies=("payment-create-repeated-v4",),
        runtime_input_requirements=(_RESOURCE_BASE_URL, _ACCESS_TOKEN, _REPEATED_PAYMENT_ID),
        assertion_ids=("OB3GLOAssertOn200", "OB3GLOFAPIHeader", "OB3GLOAssertContentType"),
        repeated_payment_case_suffix="payment-create-repeated-v4",
        legacy_vrp_sources=((_VRP_V31_MANIFEST, "OB-301-VRP-101100"), (_VRP_V40_MANIFEST, "OB-400-VRP-101100")),
        legacy_cvrp_sources=((_CVRP_V40_MANIFEST, "OB-400-CVRP-101100"),),
    ),
    _LegacyCaseBlueprint(
        id_suffix="payment-get-details",
        name="Retrieve domestic VRP payment details",
        method="GET",
        path=f"{_VRP_V40_PAYMENTS_PATH}/{{vrpId}}/payment-details",
        dependencies=("payment-get-repeated",),
        runtime_input_requirements=(_RESOURCE_BASE_URL, _ACCESS_TOKEN, _REPEATED_PAYMENT_ID),
        assertion_ids=("OB3GLOAssertOn200", "OB3GLOFAPIHeader", "OB3GLOAssertContentType"),
        repeated_payment_case_suffix="payment-create-repeated-v4",
        legacy_vrp_sources=((_VRP_V31_MANIFEST, "OB-301-VRP-101200"), (_VRP_V40_MANIFEST, "OB-400-VRP-101200")),
        legacy_cvrp_sources=((_CVRP_V40_MANIFEST, "OB-400-CVRP-101200"),),
    ),
    _LegacyCaseBlueprint(
        id_suffix="consent-delete",
        name="Delete domestic VRP consent",
        method="DELETE",
        path=f"{_VRP_V40_CONSENTS_PATH}/{{consentId}}",
        dependencies=("consent-create-awaiting-authorisation-v4",),
        runtime_input_requirements=(_RESOURCE_BASE_URL, _ACCESS_TOKEN, _CONSENT_ID),
        assertion_ids=("OB3GLOAssertOn204", "OB3GLOFAPIHeader"),
        consent_case_suffix="consent-create-awaiting-authorisation-v4",
        legacy_vrp_sources=((_VRP_V31_MANIFEST, "OB-301-VRP-102100"), (_VRP_V40_MANIFEST, "OB-400-VRP-102100")),
        legacy_cvrp_sources=((_CVRP_V40_MANIFEST, "OB-400-CVRP-102100"),),
    ),
    _LegacyCaseBlueprint(
        id_suffix="consent-get-after-delete",
        name="Retrieve deleted domestic VRP consent returns bad request",
        method="GET",
        path=f"{_VRP_V40_CONSENTS_PATH}/{{consentId}}",
        dependencies=("consent-delete",),
        runtime_input_requirements=(_RESOURCE_BASE_URL, _ACCESS_TOKEN, _CONSENT_ID),
        assertion_ids=("OB3GLOAssertOn400",),
        consent_case_suffix="consent-create-awaiting-authorisation-v4",
        legacy_vrp_sources=((_VRP_V31_MANIFEST, "OB-301-VRP-102150"), (_VRP_V40_MANIFEST, "OB-400-VRP-102150")),
        legacy_cvrp_sources=((_CVRP_V40_MANIFEST, "OB-400-CVRP-102150"),),
    ),
    _LegacyCaseBlueprint(
        id_suffix="consent-delete-after-delete",
        name="Delete already deleted domestic VRP consent",
        method="DELETE",
        path=f"{_VRP_V40_CONSENTS_PATH}/{{consentId}}",
        dependencies=("consent-delete",),
        runtime_input_requirements=(_RESOURCE_BASE_URL, _ACCESS_TOKEN, _CONSENT_ID),
        consent_case_suffix="consent-create-awaiting-authorisation-v4",
        assertion_one_of_ids=("OB3GLOAssertOn400", "OB3GLOAssertOn204"),
        legacy_vrp_sources=((_VRP_V31_MANIFEST, "OB-301-VRP-102200"), (_VRP_V40_MANIFEST, "OB-400-VRP-102200")),
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


def _build_one_of_status_assertion(assertion_ids: tuple[str, ...]) -> CatalogueAssertion | None:
    """Build a one-of HTTP status assertion from legacy assertion ids.

    Args:
        assertion_ids: Legacy ``asserts_one_of`` assertion ids.

    Returns:
        Catalogue assertion accepting any listed HTTP status, or ``None`` when
        no legacy one-of assertions were declared.

    Raises:
        ValueError: If a legacy one-of assertion id does not map to a status
            code.
    """
    if not assertion_ids:
        return None
    expected_statuses: list[int] = []
    for assertion_id in assertion_ids:
        expected_status = _LEGACY_STATUS_ASSERTIONS.get(assertion_id)
        if expected_status is None:
            raise ValueError(f"Unsupported legacy one-of status assertion id: {assertion_id}")
        expected_statuses.append(expected_status)
    expected_status_values: list[JsonValue] = list(expected_statuses)
    legacy_assertion_id_values: list[JsonValue] = list(assertion_ids)
    return CatalogueAssertion(
        assertion_id="legacy-one-of-status",
        kind="http_status",
        description=f"Expected HTTP {' or '.join(str(status) for status in expected_statuses)}.",
        rule={
            "expectedOneOf": expected_status_values,
            "legacyAssertionIds": legacy_assertion_id_values,
            "legacyAssertionSource": "manifests/assertions.json",
        },
    )


def _build_blueprint_assertions(blueprint: _LegacyCaseBlueprint) -> tuple[CatalogueAssertion, ...]:
    """Build all catalogue assertions for one VRP blueprint.

    Args:
        blueprint: Legacy coverage blueprint being converted.

    Returns:
        Catalogue assertions from ordered legacy ``asserts`` and optional
        ``asserts_one_of`` metadata.
    """
    one_of_assertion = _build_one_of_status_assertion(blueprint.assertion_one_of_ids)
    if one_of_assertion is None:
        assertions = _build_assertions(blueprint.assertion_ids)
    else:
        assertions = (*_build_assertions(blueprint.assertion_ids), one_of_assertion)
    return (*assertions, *_build_vrp_schema_assertions(blueprint=blueprint, assertions=assertions))


def _build_vrp_schema_assertions(
    *,
    blueprint: _LegacyCaseBlueprint,
    assertions: tuple[CatalogueAssertion, ...],
) -> tuple[CatalogueAssertion, ...]:
    """Build response-schema assertions required by legacy v4 VRP schemaCheck.

    Args:
        blueprint: Legacy coverage blueprint being converted.
        assertions: Existing assertions used to infer response statuses.

    Returns:
        Response-schema assertions for matching legacy v4 schema checks.
    """
    v40_script_ids = tuple(
        script_id for manifest_path, script_id in blueprint.legacy_vrp_sources if manifest_path == _VRP_V40_MANIFEST
    )
    if not _VRP_V40_SCHEMA_CHECK_SCRIPT_IDS.intersection(v40_script_ids):
        return ()
    schema_assertions: list[CatalogueAssertion] = []
    for expected_status in _expected_statuses_from_assertions(assertions):
        schema_ref = _VRP_V40_RESPONSE_SCHEMA_REFS.get(
            (blueprint.method, _normalise_vrp_schema_path(blueprint.path), expected_status)
        )
        if schema_ref is None:
            continue
        schema_assertions.append(
            CatalogueAssertion(
                assertion_id=f"legacy-response-schema-{expected_status}",
                kind="response_schema",
                description=f"Response body satisfies the legacy v4 VRP {expected_status} schema check.",
                rule={
                    "source": "bundled_openapi",
                    "document": "ob-read-write-v4.0-vrp-openapi",
                    "schemaRef": schema_ref,
                    "legacyAssertionIds": ["legacy-schema-check"],
                },
            )
        )
    return tuple(schema_assertions)


def _expected_statuses_from_assertions(assertions: tuple[CatalogueAssertion, ...]) -> tuple[int, ...]:
    """Return HTTP statuses enforced by catalogue status assertions.

    Args:
        assertions: Catalogue assertions attached to a case.

    Returns:
        Ordered HTTP statuses from exact and one-of status assertions.
    """
    statuses: list[int] = []
    for assertion in assertions:
        if assertion.kind != "http_status":
            continue
        expected = assertion.rule.get("expected")
        if isinstance(expected, int):
            statuses.append(expected)
        expected_one_of = assertion.rule.get("expectedOneOf")
        if isinstance(expected_one_of, list):
            statuses.extend(status for status in expected_one_of if isinstance(status, int))
    return tuple(dict.fromkeys(statuses))


def _normalise_vrp_schema_path(path: str) -> str:
    """Normalise VRP placeholders to bundled OpenAPI schema paths.

    Args:
        path: Catalogue blueprint path.

    Returns:
        OpenAPI path with canonical placeholder names.
    """
    return (
        path.removeprefix("/open-banking/v4.0/pisp")
        .replace("{consentId}", "{ConsentId}")
        .replace("{vrpId}", "{DomesticVRPId}")
    )


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
    endpoint_refs = tuple(
        _endpoint_ref_for_blueprint(blueprint)
        for blueprint in _BLUEPRINTS
        if _blueprint_applies_to_family(blueprint, family)
    )
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
                EndpointRef(
                    method="POST",
                    path=f"{_VRP_V40_CONSENTS_PATH}/{{consentId}}/funds-confirmation",
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
    request_path = _path_with_captured_vrp_values(family, blueprint)
    body_template = _family_template(family, blueprint.body_template)
    required_token_id = _required_token_id_for_blueprint(family, blueprint)
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
            endpoint_refs=(_endpoint_ref_for_blueprint(blueprint),),
            required_capability_ids=tuple(required_capability_ids),
            specification_versions=blueprint.specification_versions,
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
                body_template=body_template,
                generated_values=blueprint.generated_values or {},
                required_token_id=required_token_id,
            ),
        ),
        assertions=_build_blueprint_assertions(blueprint),
        response_signature_required=any(
            script_id in _RESPONSE_SIGNATURE_SCRIPT_IDS
            for _manifest_path, script_id in (
                blueprint.legacy_vrp_sources if family == "vrp" else blueprint.legacy_cvrp_sources
            )
        ),
    )


def _required_token_id_for_blueprint(family: _CatalogueFamily, blueprint: _LegacyCaseBlueprint) -> str | None:
    """Return the semantic token id required by a concrete VRP request.

    Args:
        family: Catalogue family name.
        blueprint: Legacy coverage blueprint being converted.

    Returns:
        Token id required by the request, or ``None`` when no access token is
        required.
    """
    if _ACCESS_TOKEN not in blueprint.runtime_input_requirements:
        return None
    if blueprint.required_token_id != _VRP_PSU_AUTH_TOKEN_ID:
        return blueprint.required_token_id
    if blueprint.consent_case_suffix is None:
        return blueprint.required_token_id
    return f"{family}-{blueprint.consent_case_suffix}-psu-payment-access"


def _endpoint_ref_for_blueprint(blueprint: _LegacyCaseBlueprint) -> EndpointRef:
    """Return the selectable endpoint ref for one VRP blueprint.

    Args:
        blueprint: Legacy coverage blueprint whose executable path may be
            versioned.

    Returns:
        Legacy short-path endpoint ref accepted by saved plans and builder
        selections.
    """
    return EndpointRef(method=blueprint.method, path=_short_vrp_path_alias(blueprint.path) or blueprint.path)


def _short_vrp_path_alias(path: str) -> str | None:
    """Return the legacy short VRP endpoint path for a versioned path.

    Args:
        path: Executable catalogue path.

    Returns:
        Short path without the versioned Open Banking prefix, or ``None`` when
        the path is already unversioned.
    """
    for prefix in _VRP_OPEN_BANKING_PATH_PREFIXES:
        if path == prefix:
            return "/"
        if path.startswith(f"{prefix}/"):
            return path.removeprefix(prefix)
    return None


def _path_with_captured_vrp_values(family: _CatalogueFamily, blueprint: _LegacyCaseBlueprint) -> str:
    """Return a VRP path with captured resource ids bound to this family case.

    Args:
        family: Catalogue family name.
        blueprint: Case blueprint whose path may contain OpenAPI path variables.

    Returns:
        Request path containing execution-context placeholders for captured ids.
    """
    resolved_path = blueprint.path
    if blueprint.consent_case_suffix is not None:
        resolved_path = resolved_path.replace(
            "{consentId}",
            _VRP_CAPTURED_CONSENT_ID % f"{family}-{blueprint.consent_case_suffix}",
        )
    if blueprint.initial_payment_case_suffix is not None:
        resolved_path = resolved_path.replace(
            "{vrpId}",
            _VRP_CAPTURED_PAYMENT_ID % f"{family}-{blueprint.initial_payment_case_suffix}",
        )
        resolved_path = resolved_path.replace(
            "{initialPaymentId}",
            _VRP_CAPTURED_PAYMENT_ID % f"{family}-{blueprint.initial_payment_case_suffix}",
        )
    if blueprint.repeated_payment_case_suffix is not None:
        resolved_path = resolved_path.replace(
            "{vrpId}",
            _VRP_CAPTURED_PAYMENT_ID % f"{family}-{blueprint.repeated_payment_case_suffix}",
        )
        resolved_path = resolved_path.replace(
            "{repeatedPaymentId}",
            _VRP_CAPTURED_PAYMENT_ID % f"{family}-{blueprint.repeated_payment_case_suffix}",
        )
    return resolved_path


def _family_template(family: _CatalogueFamily, value: JsonValue | None) -> JsonValue | None:
    """Return a request template with family-specific captured placeholders.

    Args:
        family: Catalogue family name.
        value: JSON template to adjust.

    Returns:
        Template with ``steps.vrp-`` placeholders rebound to the selected family,
        or ``None`` when no template is declared.
    """
    if value is None:
        return None
    if isinstance(value, str):
        return value.replace("steps.vrp-", f"steps.{family}-")
    if isinstance(value, list):
        return [_family_template(family, item) for item in value]
    if isinstance(value, dict):
        return {str(key): _family_template(family, item) for key, item in value.items()}
    return value


def _blueprint_applies_to_family(blueprint: _LegacyCaseBlueprint, family: _CatalogueFamily) -> bool:
    """Return whether a blueprint contributes a case for ``family``.

    Args:
        blueprint: Legacy case blueprint to inspect.
        family: Catalogue family name.

    Returns:
        True when the blueprint has legacy provenance for the requested family.
    """
    return bool(blueprint.legacy_vrp_sources if family == "vrp" else blueprint.legacy_cvrp_sources)


def _build_family_cases(family: _CatalogueFamily) -> tuple[CatalogueTestCase, ...]:
    """Build the ordered test-case set for a VRP catalogue family.

    Args:
        family: Catalogue family name.

    Returns:
        Ordered catalogue test cases for the requested family.
    """
    return tuple(
        _build_family_case(family, blueprint)
        for blueprint in _BLUEPRINTS
        if _blueprint_applies_to_family(blueprint, family)
    )


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
