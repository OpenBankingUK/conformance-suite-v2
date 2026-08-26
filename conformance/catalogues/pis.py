"""Legacy FCS-derived Open Banking UK payment (PIS) test catalogue."""

from __future__ import annotations

from conformance.catalogue import (
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

PIS_PAYMENT_CATALOGUE_KEY = CatalogueKey(standard="open-banking", version="v4.0", api="pis")
"""Catalogue boundary for PIS payment coverage imported from legacy FCS manifests."""

PIS_PAYMENT_CATALOGUE_VERSION = "2026.07.legacy-fcs-pis.1"
"""Catalogue content version for the first legacy-payment import into v2."""

_RESOURCE_BASE_URL = RuntimeInputRequirement(
    input_id="resourceBaseUrl",
    input_type="url",
    label="Resource server base URL",
)
"""HTTPS base URL for payment-resource API calls."""

_ACCESS_TOKEN_REF = RuntimeInputRequirement(
    input_id="accessTokenRef",
    input_type="file_reference",
    label="Access token runtime reference",
    sensitive=True,
    source="token",
)
"""Reference to a runtime token artifact used for authorization headers."""

_DOMESTIC_PAYMENT_CONSENT_ID = RuntimeInputRequirement(
    input_id="domesticPaymentConsentId",
    input_type="string",
    label="Domestic payment consent identifier",
    source="captured",
)
"""Consent identifier returned by domestic-payment consent creation."""

_DOMESTIC_PAYMENT_ID = RuntimeInputRequirement(
    input_id="domesticPaymentId",
    input_type="string",
    label="Domestic payment identifier",
    source="captured",
)
"""Payment identifier returned by domestic-payment submission."""

_DOMESTIC_SCHEDULED_PAYMENT_CONSENT_ID = RuntimeInputRequirement(
    input_id="domesticScheduledPaymentConsentId",
    input_type="string",
    label="Domestic scheduled payment consent identifier",
    source="captured",
)
"""Consent identifier returned by domestic-scheduled consent creation."""

_DOMESTIC_SCHEDULED_PAYMENT_ID = RuntimeInputRequirement(
    input_id="domesticScheduledPaymentId",
    input_type="string",
    label="Domestic scheduled payment identifier",
    source="captured",
)
"""Payment identifier returned by domestic-scheduled submission."""

_DOMESTIC_STANDING_ORDER_CONSENT_ID = RuntimeInputRequirement(
    input_id="domesticStandingOrderConsentId",
    input_type="string",
    label="Domestic standing-order consent identifier",
    source="captured",
)
"""Consent identifier returned by domestic-standing-order consent creation."""

_DOMESTIC_STANDING_ORDER_ID = RuntimeInputRequirement(
    input_id="domesticStandingOrderId",
    input_type="string",
    label="Domestic standing-order identifier",
    source="captured",
)
"""Standing-order identifier returned by domestic-standing-order submission."""

_INTERNATIONAL_PAYMENT_CONSENT_ID = RuntimeInputRequirement(
    input_id="internationalPaymentConsentId",
    input_type="string",
    label="International payment consent identifier",
    source="captured",
)
"""Consent identifier returned by international-payment consent creation."""

_INTERNATIONAL_PAYMENT_ID = RuntimeInputRequirement(
    input_id="internationalPaymentId",
    input_type="string",
    label="International payment identifier",
    source="captured",
)
"""Payment identifier returned by international-payment submission."""

_INTERNATIONAL_SCHEDULED_PAYMENT_CONSENT_ID = RuntimeInputRequirement(
    input_id="internationalScheduledPaymentConsentId",
    input_type="string",
    label="International scheduled payment consent identifier",
    source="captured",
)
"""Consent identifier returned by international-scheduled consent creation."""

_INTERNATIONAL_SCHEDULED_PAYMENT_ID = RuntimeInputRequirement(
    input_id="internationalScheduledPaymentId",
    input_type="string",
    label="International scheduled payment identifier",
    source="captured",
)

_PIS_RESOURCE_AUTH_ID = "pis-payment-access"
"""Semantic authorization id for PIS resource API requests."""

_PIS_CAPTURED_PATH_VALUES = {
    "{domesticPaymentConsentId}": (
        "${steps.pis-v4-domestic-payment-consent-create-request.response.body.Data.ConsentId}"
    ),
    "{domesticPaymentId}": "${steps.pis-v4-domestic-payment-create-request.response.body.Data.DomesticPaymentId}",
    "{domesticScheduledPaymentConsentId}": (
        "${steps.pis-v4-domestic-scheduled-payment-consent-create-request.response.body.Data.ConsentId}"
    ),
    "{domesticScheduledPaymentId}": (
        "${steps.pis-v4-domestic-scheduled-payment-create-request.response.body.Data.DomesticScheduledPaymentId}"
    ),
    "{domesticStandingOrderConsentId}": (
        "${steps.pis-v4-domestic-standing-order-consent-create-request.response.body.Data.ConsentId}"
    ),
    "{domesticStandingOrderId}": (
        "${steps.pis-v4-domestic-standing-order-create-request.response.body.Data.DomesticStandingOrderId}"
    ),
    "{internationalPaymentConsentId}": (
        "${steps.pis-v4-international-payment-consent-create-request.response.body.Data.ConsentId}"
    ),
    "{internationalPaymentId}": (
        "${steps.pis-v4-international-payment-create-request.response.body.Data.InternationalPaymentId}"
    ),
    "{internationalScheduledPaymentConsentId}": (
        "${steps.pis-v4-international-scheduled-payment-consent-create-request.response.body.Data.ConsentId}"
    ),
    "{internationalScheduledPaymentId}": (
        "${steps.pis-v4-international-scheduled-payment-create-request.response.body.Data.InternationalScheduledPaymentId}"
    ),
}
"""Captured response-field placeholders for PIS path parameters."""
"""Payment identifier returned by international-scheduled submission."""


PIS_PAYMENT_CAPABILITIES = (
    EndpointCapability(
        capability_id="pis.domestic-payment-submission",
        label="Domestic payment submission",
        description="Baseline support for submitting domestic payments.",
        required=True,
        endpoint_refs=(EndpointRef(method="POST", path="/open-banking/v4.0/pisp/domestic-payments"),),
    ),
    EndpointCapability(
        capability_id="pis.domestic-scheduled-payment-submission",
        label="Domestic scheduled payment submission",
        description="Baseline support for submitting domestic scheduled payments.",
        required=True,
        endpoint_refs=(EndpointRef(method="POST", path="/open-banking/v4.0/pisp/domestic-scheduled-payments"),),
    ),
    EndpointCapability(
        capability_id="pis.domestic-standing-order-submission",
        label="Domestic standing order submission",
        description="Baseline support for submitting domestic standing orders.",
        required=True,
        endpoint_refs=(EndpointRef(method="POST", path="/open-banking/v4.0/pisp/domestic-standing-orders"),),
    ),
    EndpointCapability(
        capability_id="pis.international-payment-submission",
        label="International payment submission",
        description="Baseline support for submitting international payments.",
        required=True,
        endpoint_refs=(EndpointRef(method="POST", path="/open-banking/v4.0/pisp/international-payments"),),
    ),
    EndpointCapability(
        capability_id="pis.international-scheduled-payment-submission",
        label="International scheduled payment submission",
        description="Baseline support for submitting international scheduled payments.",
        required=True,
        endpoint_refs=(EndpointRef(method="POST", path="/open-banking/v4.0/pisp/international-scheduled-payments"),),
    ),
    EndpointCapability(
        capability_id="pis.domestic-payment-consent.reject-invalid-detached-jws",
        label="Domestic payment consent invalid detached JWS rejection",
        description="Optional support for rejecting invalid detached JWS on domestic payment consent creation.",
        required=False,
        endpoint_refs=(EndpointRef(method="POST", path="/open-banking/v4.0/pisp/domestic-payment-consents"),),
    ),
    EndpointCapability(
        capability_id="pis.domestic-standing-order.reject-invalid-frequency-combination",
        label="Domestic standing order invalid frequency rejection",
        description="Optional support for rejecting invalid domestic standing order frequency combinations.",
        required=False,
        endpoint_refs=(EndpointRef(method="POST", path="/open-banking/v4.0/pisp/domestic-standing-orders"),),
    ),
)
"""Catalogue-owned implementation features represented by the legacy PIS catalogue."""

_RESPONSE_SIGNATURE_SCRIPT_IDS = frozenset(
    {
        "OB-301-DOP-100100",
        "OB-301-DOP-100300",
        "OB-301-DOP-100400",
        "OB-301-DOP-100500",
        "OB-301-DOP-100600",
        "OB-301-DOP-100700",
        "OB-301-DOP-100900",
        "OB-301-DOP-101100",
        "OB-301-DOP-101101",
        "OB-301-DOP-101200",
        "OB-301-DOP-101300",
        "OB-301-DOP-101400",
        "OB-301-DOP-101401",
        "OB-301-DOP-101500",
        "OB-301-DOP-1015001",
        "OB-301-DOP-1015002",
        "OB-301-DOP-1015003",
        "OB-301-DOP-101700",
        "OB-301-DOP-101900",
        "OB-301-DOP-102100",
        "OB-301-DOP-102200",
        "OB-301-DOP-102300",
        "OB-400-DOP-100100",
        "OB-400-DOP-100300",
        "OB-400-DOP-100400",
        "OB-400-DOP-100500",
        "OB-400-DOP-100600",
        "OB-400-DOP-100700",
        "OB-400-DOP-100900",
        "OB-400-DOP-101100",
        "OB-400-DOP-101101",
        "OB-400-DOP-101200",
        "OB-400-DOP-101300",
        "OB-400-DOP-101400",
        "OB-400-DOP-101401",
        "OB-400-DOP-101500",
        "OB-400-DOP-101503",
        "OB-400-DOP-101700",
        "OB-400-DOP-101900",
        "OB-400-DOP-102100",
        "OB-400-DOP-102200",
        "OB-400-DOP-102300",
    }
)
"""Legacy PIS script ids whose responses required JWS signature validation."""


def _legacy_compliance_scope(
    *,
    scripts_31: tuple[str, ...],
    scripts_40: tuple[str, ...],
    assertion_ids: tuple[str, ...],
) -> tuple[str, ...]:
    """Build compliance-scope metadata tracing a case to legacy FCS manifests.

    Args:
        scripts_31: Legacy script ids from ``ob_3.1_payment_fca.json``.
        scripts_40: Legacy script ids from ``ob_4.0_payment_fca.json``.
        assertion_ids: Legacy assertion ids represented by this case.

    Returns:
        Compliance-scope labels documenting source manifests, script ids,
        and assertion provenance.
    """
    scope: list[str] = [
        "legacy-fcs-source:OpenBankingUK/conformance-suite@develop/manifests/ob_3.1_payment_fca.json",
        "legacy-fcs-source:OpenBankingUK/conformance-suite@develop/manifests/ob_4.0_payment_fca.json",
    ]
    scope.extend(f"legacy-fcs-script:ob_3.1_payment_fca.json#{script_id}" for script_id in scripts_31)
    scope.extend(f"legacy-fcs-script:ob_4.0_payment_fca.json#{script_id}" for script_id in scripts_40)
    scope.extend(f"legacy-fcs-assertion:{assertion_id}" for assertion_id in assertion_ids)
    return tuple(scope)


def _status_assertion(
    *,
    assertion_id: str,
    description: str,
    expected_status: int,
    legacy_assertion_ids: tuple[str, ...],
) -> CatalogueAssertion:
    """Create a status assertion traced to legacy FCS assertion identifiers.

    Args:
        assertion_id: Stable assertion id unique within a test case.
        description: Human-readable assertion description.
        expected_status: Expected HTTP status code.
        legacy_assertion_ids: Legacy assertion ids represented by this assertion.

    Returns:
        Catalogue assertion with status-code rule metadata.
    """
    return CatalogueAssertion(
        assertion_id=assertion_id,
        kind="http_status",
        description=description,
        rule={
            "expected": expected_status,
            "legacyAssertionIds": list(legacy_assertion_ids),
        },
    )


def _json_field_assertion(
    *,
    assertion_id: str,
    description: str,
    field_path: str,
    expected_value: str,
    legacy_assertion_ids: tuple[str, ...],
) -> CatalogueAssertion:
    """Create a JSON-field assertion traced to legacy FCS assertion identifiers.

    Args:
        assertion_id: Stable assertion id unique within a test case.
        description: Human-readable assertion description.
        field_path: Dotted JSON path checked by the assertion.
        expected_value: Expected string field value.
        legacy_assertion_ids: Legacy assertion ids represented by this assertion.

    Returns:
        Catalogue assertion with JSON field rule metadata.
    """
    return CatalogueAssertion(
        assertion_id=assertion_id,
        kind="json_field",
        description=description,
        rule={
            "path": field_path,
            "expected": expected_value,
            "legacyAssertionIds": list(legacy_assertion_ids),
        },
    )


def _header_assertion(
    *,
    assertion_id: str,
    description: str,
    header_name: str,
    legacy_assertion_ids: tuple[str, ...],
) -> CatalogueAssertion:
    """Create a header assertion traced to legacy FCS assertion identifiers.

    Args:
        assertion_id: Stable assertion id unique within a test case.
        description: Human-readable assertion description.
        header_name: Header name that must be present.
        legacy_assertion_ids: Legacy assertion ids represented by this assertion.

    Returns:
        Catalogue assertion with header rule metadata.
    """
    return CatalogueAssertion(
        assertion_id=assertion_id,
        kind="header",
        description=description,
        rule={
            "header": header_name,
            "required": True,
            "legacyAssertionIds": list(legacy_assertion_ids),
        },
    )


def _build_case(
    *,
    test_case_id: str,
    name: str,
    role: TestCaseRole,
    method: HttpMethod,
    path: str,
    runtime_inputs: tuple[RuntimeInputRequirement, ...],
    scripts_31: tuple[str, ...],
    scripts_40: tuple[str, ...],
    legacy_assertion_ids: tuple[str, ...],
    assertions: tuple[CatalogueAssertion, ...],
    required_capability_ids: tuple[str, ...] = (),
    dependencies: tuple[str, ...] = (),
    mandatory: bool = True,
) -> CatalogueTestCase:
    """Construct one PIS payment catalogue test case.

    Args:
        test_case_id: Stable case identifier.
        name: Human-readable case name.
        role: Test-case role for scheduling.
        method: HTTP method for endpoint applicability.
        path: Endpoint path for endpoint applicability.
        runtime_inputs: Runtime input requirements used by this case.
        scripts_31: Legacy script ids from ``ob_3.1_payment_fca.json``.
        scripts_40: Legacy script ids from ``ob_4.0_payment_fca.json``.
        legacy_assertion_ids: Legacy assertion ids represented by this case.
        assertions: Catalogue assertions locked for this case.
        required_capability_ids: Catalogue capability ids required for this case
            to apply directly.
        dependencies: Other test-case ids required before this case.
        mandatory: Whether deselection is blocked for applicable plans.

    Returns:
        Fully-defined catalogue test case with endpoint/profile applicability.
    """
    request_path = _path_with_captured_pis_values(path)
    runtime_input_refs = tuple(requirement.input_id for requirement in runtime_inputs if requirement.source == "plan")
    required_token_id = _PIS_RESOURCE_AUTH_ID if _ACCESS_TOKEN_REF in runtime_inputs else None
    return CatalogueTestCase(
        test_case_id=test_case_id,
        name=name,
        role=role,
        compliance_scope=_legacy_compliance_scope(
            scripts_31=scripts_31,
            scripts_40=scripts_40,
            assertion_ids=legacy_assertion_ids,
        ),
        applicability=TestCaseApplicability(
            security_profiles=SecurityProfileApplicability(profiles=("all",)),
            endpoint_refs=(EndpointRef(method=method, path=path),),
            required_capability_ids=required_capability_ids,
        ),
        mandatory=mandatory,
        dependencies=dependencies,
        runtime_input_requirements=runtime_inputs,
        request_steps=(
            CatalogueRequestStep(
                step_id=f"{test_case_id}-request",
                name=name,
                method=method,
                path=request_path,
                runtime_input_refs=runtime_input_refs,
                headers=open_banking_request_headers_for(require_idempotency=method in {"POST", "PUT", "PATCH"}),
                required_token_id=required_token_id,
            ),
        ),
        assertions=assertions,
        response_signature_required=bool(_RESPONSE_SIGNATURE_SCRIPT_IDS.intersection((*scripts_31, *scripts_40))),
    )


def _path_with_captured_pis_values(path: str) -> str:
    """Return ``path`` with PIS resource ids resolved from captured responses.

    Args:
        path: Standards path template that may contain OpenAPI path variables.

    Returns:
        Request path containing execution-context placeholders for captured ids.
    """
    resolved_path = path
    for variable, placeholder in _PIS_CAPTURED_PATH_VALUES.items():
        resolved_path = resolved_path.replace(variable, placeholder)
    return resolved_path


PIS_PAYMENT_CATALOGUE = TestCatalogue(
    key=PIS_PAYMENT_CATALOGUE_KEY,
    catalogue_version=PIS_PAYMENT_CATALOGUE_VERSION,
    capabilities=PIS_PAYMENT_CAPABILITIES,
    test_cases=(
        _build_case(
            test_case_id="pis-v4-domestic-payment-consent-create",
            name="Create domestic payment consent",
            role="consent",
            method="POST",
            path="/open-banking/v4.0/pisp/domestic-payment-consents",
            runtime_inputs=(_RESOURCE_BASE_URL, _ACCESS_TOKEN_REF),
            scripts_31=("OB-301-DOP-100100", "OB-301-DOP-100300", "OB-313-DOP-100100"),
            scripts_40=("OB-400-DOP-100100", "OB-400-DOP-100300"),
            legacy_assertion_ids=(
                "OB3GLOAssertOn201",
                "OB3GLOFAPIHeader",
                "OB3DOPAssertAwaitingAuthorisation",
                "OB3DOPAssertAwaitingAuthorisationV4",
                "OB3GLOAAssertConsentId",
            ),
            assertions=(
                _status_assertion(
                    assertion_id="status-201",
                    description="Consent creation returns HTTP 201.",
                    expected_status=201,
                    legacy_assertion_ids=("OB3GLOAssertOn201",),
                ),
                _header_assertion(
                    assertion_id="fapi-interaction-header",
                    description="Response includes x-fapi-interaction-id.",
                    header_name="x-fapi-interaction-id",
                    legacy_assertion_ids=("OB3GLOFAPIHeader",),
                ),
                _json_field_assertion(
                    assertion_id="consent-status-awaiting-authorisation",
                    description="Consent status is AwaitingAuthorisation.",
                    field_path="data.status",
                    expected_value="AwaitingAuthorisation",
                    legacy_assertion_ids=("OB3DOPAssertAwaitingAuthorisation", "OB3DOPAssertAwaitingAuthorisationV4"),
                ),
            ),
        ),
        _build_case(
            test_case_id="pis-v4-domestic-payment-consent-reject-invalid-signature",
            name="Reject domestic payment consent with invalid detached JWS",
            role="security",
            method="POST",
            path="/open-banking/v4.0/pisp/domestic-payment-consents",
            runtime_inputs=(_RESOURCE_BASE_URL, _ACCESS_TOKEN_REF),
            scripts_31=("OB-301-DOP-100110", "OB-316-DOP-100310"),
            scripts_40=("OB-400-DOP-100110", "OB-316-DOP-100310"),
            legacy_assertion_ids=(
                "OB3GLOAssertOn400",
                "OB3DOPAssertSignatureMissingOBErrorCode",
                "OB3DOPAssertSignatureMissingOBErrorCodeV4",
                "OB3GLOAssertSignatureInvalidClaimErrorCode",
                "OB3GLOAssertSignatureMissingClaimErrorCode",
                "OB3GLOAssertSignatureMalformedErrorCode",
                "OB3GLOAssertSignatureInvalidClaimErrorCodeV4",
                "OB3GLOAssertSignatureMissingClaimErrorCodeV4",
                "OB3GLOAssertSignatureMalformedErrorCodeV4",
            ),
            assertions=(
                _status_assertion(
                    assertion_id="status-400",
                    description="Invalid or missing signature returns HTTP 400.",
                    expected_status=400,
                    legacy_assertion_ids=("OB3GLOAssertOn400",),
                ),
            ),
            required_capability_ids=("pis.domestic-payment-consent.reject-invalid-detached-jws",),
            mandatory=False,
        ),
        _build_case(
            test_case_id="pis-v4-domestic-payment-consent-read-authorised",
            name="Read domestic payment consent status",
            role="consent",
            method="GET",
            path="/open-banking/v4.0/pisp/domestic-payment-consents/{domesticPaymentConsentId}",
            runtime_inputs=(_RESOURCE_BASE_URL, _ACCESS_TOKEN_REF, _DOMESTIC_PAYMENT_CONSENT_ID),
            scripts_31=("OB-301-DOP-100400",),
            scripts_40=("OB-400-DOP-100400",),
            legacy_assertion_ids=(
                "OB3GLOAssertOn200",
                "OB3GLOFAPIHeader",
                "OB3DOPAssertAuthorised",
                "OB3DOPAssertAuthorisedV4",
            ),
            assertions=(
                _status_assertion(
                    assertion_id="status-200",
                    description="Consent retrieval returns HTTP 200.",
                    expected_status=200,
                    legacy_assertion_ids=("OB3GLOAssertOn200",),
                ),
                _json_field_assertion(
                    assertion_id="consent-status-authorised",
                    description="Consent status is Authorised.",
                    field_path="data.status",
                    expected_value="Authorised",
                    legacy_assertion_ids=("OB3DOPAssertAuthorised", "OB3DOPAssertAuthorisedV4"),
                ),
            ),
            dependencies=("pis-v4-domestic-payment-consent-create",),
        ),
        _build_case(
            test_case_id="pis-v4-domestic-payment-funds-confirmation",
            name="Confirm domestic payment consent funds availability",
            role="resource",
            method="GET",
            path="/open-banking/v4.0/pisp/domestic-payment-consents/{domesticPaymentConsentId}/funds-confirmation",
            runtime_inputs=(_RESOURCE_BASE_URL, _ACCESS_TOKEN_REF, _DOMESTIC_PAYMENT_CONSENT_ID),
            scripts_31=("OB-301-DOP-100500",),
            scripts_40=("OB-400-DOP-100500",),
            legacy_assertion_ids=("OB3GLOAssertOn200", "OB3DOPFundsAvailable"),
            assertions=(
                _status_assertion(
                    assertion_id="status-200",
                    description="Funds-confirmation returns HTTP 200.",
                    expected_status=200,
                    legacy_assertion_ids=("OB3GLOAssertOn200",),
                ),
            ),
            dependencies=("pis-v4-domestic-payment-consent-read-authorised",),
        ),
        _build_case(
            test_case_id="pis-v4-domestic-payment-create",
            name="Submit domestic payment",
            role="resource",
            method="POST",
            path="/open-banking/v4.0/pisp/domestic-payments",
            runtime_inputs=(
                _RESOURCE_BASE_URL,
                _ACCESS_TOKEN_REF,
                _DOMESTIC_PAYMENT_CONSENT_ID,
            ),
            scripts_31=("OB-301-DOP-100600",),
            scripts_40=("OB-400-DOP-100600",),
            legacy_assertion_ids=("OB3GLOAssertOn201",),
            required_capability_ids=("pis.domestic-payment-submission",),
            assertions=(
                _status_assertion(
                    assertion_id="status-201",
                    description="Domestic payment submission returns HTTP 201.",
                    expected_status=201,
                    legacy_assertion_ids=("OB3GLOAssertOn201",),
                ),
            ),
            dependencies=("pis-v4-domestic-payment-consent-read-authorised",),
        ),
        _build_case(
            test_case_id="pis-v4-domestic-payment-read",
            name="Read domestic payment status",
            role="resource",
            method="GET",
            path="/open-banking/v4.0/pisp/domestic-payments/{domesticPaymentId}",
            runtime_inputs=(_RESOURCE_BASE_URL, _ACCESS_TOKEN_REF, _DOMESTIC_PAYMENT_ID),
            scripts_31=("OB-301-DOP-100700",),
            scripts_40=("OB-400-DOP-100700",),
            legacy_assertion_ids=("OB3GLOAssertOn200",),
            assertions=(
                _status_assertion(
                    assertion_id="status-200",
                    description="Domestic payment status retrieval returns HTTP 200.",
                    expected_status=200,
                    legacy_assertion_ids=("OB3GLOAssertOn200",),
                ),
            ),
            dependencies=("pis-v4-domestic-payment-create",),
        ),
        _build_case(
            test_case_id="pis-v4-domestic-scheduled-payment-consent-create",
            name="Create domestic scheduled payment consent",
            role="consent",
            method="POST",
            path="/open-banking/v4.0/pisp/domestic-scheduled-payment-consents",
            runtime_inputs=(_RESOURCE_BASE_URL, _ACCESS_TOKEN_REF),
            scripts_31=("OB-301-DOP-100800", "OB-301-DOP-100810", "OB-301-DOP-100820"),
            scripts_40=("OB-400-DOP-100800", "OB-400-DOP-100810", "OB-400-DOP-100820"),
            legacy_assertion_ids=(
                "OB3GLOAssertOn201",
                "OB3GLOFAPIHeader",
                "OB3DOPAssertAwaitingAuthorisation",
                "OB3DOPAssertAwaitingAuthorisationV4",
                "OB3GLOAAssertConsentId",
            ),
            assertions=(
                _status_assertion(
                    assertion_id="status-201",
                    description="Scheduled-consent creation returns HTTP 201.",
                    expected_status=201,
                    legacy_assertion_ids=("OB3GLOAssertOn201",),
                ),
            ),
        ),
        _build_case(
            test_case_id="pis-v4-domestic-scheduled-payment-consent-read",
            name="Read domestic scheduled payment consent status",
            role="consent",
            method="GET",
            path="/open-banking/v4.0/pisp/domestic-scheduled-payment-consents/{domesticScheduledPaymentConsentId}",
            runtime_inputs=(
                _RESOURCE_BASE_URL,
                _ACCESS_TOKEN_REF,
                _DOMESTIC_SCHEDULED_PAYMENT_CONSENT_ID,
            ),
            scripts_31=("OB-301-DOP-100900",),
            scripts_40=("OB-400-DOP-100900",),
            legacy_assertion_ids=("OB3GLOAssertOn200", "OB3DOPAssertAuthorised", "OB3DOPAssertAuthorisedV4"),
            assertions=(
                _status_assertion(
                    assertion_id="status-200",
                    description="Scheduled-consent retrieval returns HTTP 200.",
                    expected_status=200,
                    legacy_assertion_ids=("OB3GLOAssertOn200",),
                ),
            ),
            dependencies=("pis-v4-domestic-scheduled-payment-consent-create",),
        ),
        _build_case(
            test_case_id="pis-v4-domestic-scheduled-payment-create",
            name="Submit domestic scheduled payment",
            role="resource",
            method="POST",
            path="/open-banking/v4.0/pisp/domestic-scheduled-payments",
            runtime_inputs=(
                _RESOURCE_BASE_URL,
                _ACCESS_TOKEN_REF,
                _DOMESTIC_SCHEDULED_PAYMENT_CONSENT_ID,
            ),
            scripts_31=("OB-301-DOP-101000", "OB-301-DOP-101101"),
            scripts_40=("OB-400-DOP-101000", "OB-400-DOP-101101"),
            legacy_assertion_ids=("OB3GLOAssertOn201",),
            required_capability_ids=("pis.domestic-scheduled-payment-submission",),
            assertions=(
                _status_assertion(
                    assertion_id="status-201",
                    description="Domestic scheduled payment submission returns HTTP 201.",
                    expected_status=201,
                    legacy_assertion_ids=("OB3GLOAssertOn201",),
                ),
            ),
            dependencies=("pis-v4-domestic-scheduled-payment-consent-read",),
        ),
        _build_case(
            test_case_id="pis-v4-domestic-scheduled-payment-read",
            name="Read domestic scheduled payment status",
            role="resource",
            method="GET",
            path="/open-banking/v4.0/pisp/domestic-scheduled-payments/{domesticScheduledPaymentId}",
            runtime_inputs=(_RESOURCE_BASE_URL, _ACCESS_TOKEN_REF, _DOMESTIC_SCHEDULED_PAYMENT_ID),
            scripts_31=("OB-301-DOP-101100",),
            scripts_40=("OB-400-DOP-101100",),
            legacy_assertion_ids=("OB3GLOAssertOn200",),
            assertions=(
                _status_assertion(
                    assertion_id="status-200",
                    description="Domestic scheduled payment status retrieval returns HTTP 200.",
                    expected_status=200,
                    legacy_assertion_ids=("OB3GLOAssertOn200",),
                ),
            ),
            dependencies=("pis-v4-domestic-scheduled-payment-create",),
        ),
        _build_case(
            test_case_id="pis-v4-domestic-standing-order-consent-create",
            name="Create domestic standing-order consent",
            role="consent",
            method="POST",
            path="/open-banking/v4.0/pisp/domestic-standing-order-consents",
            runtime_inputs=(_RESOURCE_BASE_URL, _ACCESS_TOKEN_REF),
            scripts_31=("OB-301-DOP-101200",),
            scripts_40=("OB-400-DOP-101200",),
            legacy_assertion_ids=(
                "OB3GLOAssertOn201",
                "OB3GLOFAPIHeader",
                "OB3DOPAssertAwaitingAuthorisation",
                "OB3DOPAssertAwaitingAuthorisationV4",
                "OB3GLOAAssertConsentId",
            ),
            assertions=(
                _status_assertion(
                    assertion_id="status-201",
                    description="Standing-order consent creation returns HTTP 201.",
                    expected_status=201,
                    legacy_assertion_ids=("OB3GLOAssertOn201",),
                ),
            ),
        ),
        _build_case(
            test_case_id="pis-v4-domestic-standing-order-consent-read",
            name="Read domestic standing-order consent status",
            role="consent",
            method="GET",
            path="/open-banking/v4.0/pisp/domestic-standing-order-consents/{domesticStandingOrderConsentId}",
            runtime_inputs=(_RESOURCE_BASE_URL, _ACCESS_TOKEN_REF, _DOMESTIC_STANDING_ORDER_CONSENT_ID),
            scripts_31=("OB-301-DOP-101300",),
            scripts_40=("OB-400-DOP-101300",),
            legacy_assertion_ids=("OB3GLOAssertOn200", "OB3DOPAssertAuthorised", "OB3DOPAssertAuthorisedV4"),
            assertions=(
                _status_assertion(
                    assertion_id="status-200",
                    description="Standing-order consent retrieval returns HTTP 200.",
                    expected_status=200,
                    legacy_assertion_ids=("OB3GLOAssertOn200",),
                ),
            ),
            dependencies=("pis-v4-domestic-standing-order-consent-create",),
        ),
        _build_case(
            test_case_id="pis-v4-domestic-standing-order-create",
            name="Submit domestic standing order",
            role="resource",
            method="POST",
            path="/open-banking/v4.0/pisp/domestic-standing-orders",
            runtime_inputs=(
                _RESOURCE_BASE_URL,
                _ACCESS_TOKEN_REF,
                _DOMESTIC_STANDING_ORDER_CONSENT_ID,
            ),
            scripts_31=("OB-301-DOP-101401",),
            scripts_40=("OB-400-DOP-101401",),
            legacy_assertion_ids=("OB3GLOAssertOn201",),
            required_capability_ids=("pis.domestic-standing-order-submission",),
            assertions=(
                _status_assertion(
                    assertion_id="status-201",
                    description="Domestic standing-order submission returns HTTP 201.",
                    expected_status=201,
                    legacy_assertion_ids=("OB3GLOAssertOn201",),
                ),
            ),
            dependencies=("pis-v4-domestic-standing-order-consent-read",),
        ),
        _build_case(
            test_case_id="pis-v4-domestic-standing-order-read",
            name="Read domestic standing order status",
            role="resource",
            method="GET",
            path="/open-banking/v4.0/pisp/domestic-standing-orders/{domesticStandingOrderId}",
            runtime_inputs=(_RESOURCE_BASE_URL, _ACCESS_TOKEN_REF, _DOMESTIC_STANDING_ORDER_ID),
            scripts_31=("OB-301-DOP-101500",),
            scripts_40=("OB-400-DOP-101500",),
            legacy_assertion_ids=("OB3GLOAssertOn200",),
            assertions=(
                _status_assertion(
                    assertion_id="status-200",
                    description="Domestic standing-order status retrieval returns HTTP 200.",
                    expected_status=200,
                    legacy_assertion_ids=("OB3GLOAssertOn200",),
                ),
            ),
            dependencies=("pis-v4-domestic-standing-order-create",),
        ),
        _build_case(
            test_case_id="pis-v4-domestic-standing-order-reject-invalid-frequency",
            name="Reject domestic standing order with invalid frequency combination",
            role="resource",
            method="POST",
            path="/open-banking/v4.0/pisp/domestic-standing-orders",
            runtime_inputs=(
                _RESOURCE_BASE_URL,
                _ACCESS_TOKEN_REF,
                _DOMESTIC_STANDING_ORDER_CONSENT_ID,
            ),
            scripts_31=("OB-301-DOP-101400", "OB-301-DOP-1015003"),
            scripts_40=("OB-400-DOP-101400", "OB-400-DOP-101503"),
            legacy_assertion_ids=("OB3GLOAssertOn400",),
            required_capability_ids=("pis.domestic-standing-order.reject-invalid-frequency-combination",),
            assertions=(
                _status_assertion(
                    assertion_id="status-400",
                    description="Invalid standing-order frequency input returns HTTP 400.",
                    expected_status=400,
                    legacy_assertion_ids=("OB3GLOAssertOn400",),
                ),
            ),
            dependencies=("pis-v4-domestic-standing-order-consent-read",),
            mandatory=False,
        ),
        _build_case(
            test_case_id="pis-v4-international-payment-consent-create",
            name="Create international payment consent",
            role="consent",
            method="POST",
            path="/open-banking/v4.0/pisp/international-payment-consents",
            runtime_inputs=(_RESOURCE_BASE_URL, _ACCESS_TOKEN_REF),
            scripts_31=("OB-301-DOP-101600",),
            scripts_40=("OB-400-DOP-101600",),
            legacy_assertion_ids=(
                "OB3GLOAssertOn201",
                "OB3GLOFAPIHeader",
                "OB3DOPAssertAwaitingAuthorisation",
                "OB3DOPAssertAwaitingAuthorisationV4",
                "OB3GLOAAssertConsentId",
            ),
            assertions=(
                _status_assertion(
                    assertion_id="status-201",
                    description="International-payment consent creation returns HTTP 201.",
                    expected_status=201,
                    legacy_assertion_ids=("OB3GLOAssertOn201",),
                ),
            ),
        ),
        _build_case(
            test_case_id="pis-v4-international-payment-consent-read",
            name="Read international payment consent status",
            role="consent",
            method="GET",
            path="/open-banking/v4.0/pisp/international-payment-consents/{internationalPaymentConsentId}",
            runtime_inputs=(_RESOURCE_BASE_URL, _ACCESS_TOKEN_REF, _INTERNATIONAL_PAYMENT_CONSENT_ID),
            scripts_31=("OB-301-DOP-101700",),
            scripts_40=("OB-400-DOP-101700",),
            legacy_assertion_ids=("OB3GLOAssertOn200", "OB3DOPAssertAuthorised", "OB3DOPAssertAuthorisedV4"),
            assertions=(
                _status_assertion(
                    assertion_id="status-200",
                    description="International-payment consent retrieval returns HTTP 200.",
                    expected_status=200,
                    legacy_assertion_ids=("OB3GLOAssertOn200",),
                ),
            ),
            dependencies=("pis-v4-international-payment-consent-create",),
        ),
        _build_case(
            test_case_id="pis-v4-international-payment-create",
            name="Submit international payment",
            role="resource",
            method="POST",
            path="/open-banking/v4.0/pisp/international-payments",
            runtime_inputs=(
                _RESOURCE_BASE_URL,
                _ACCESS_TOKEN_REF,
                _INTERNATIONAL_PAYMENT_CONSENT_ID,
            ),
            scripts_31=("OB-301-DOP-101800",),
            scripts_40=("OB-400-DOP-101800",),
            legacy_assertion_ids=("OB3GLOAssertOn201", "OB3IPAssertInternationalPaymentId"),
            required_capability_ids=("pis.international-payment-submission",),
            assertions=(
                _status_assertion(
                    assertion_id="status-201",
                    description="International payment submission returns HTTP 201.",
                    expected_status=201,
                    legacy_assertion_ids=("OB3GLOAssertOn201",),
                ),
            ),
            dependencies=("pis-v4-international-payment-consent-read",),
        ),
        _build_case(
            test_case_id="pis-v4-international-payment-read",
            name="Read international payment status",
            role="resource",
            method="GET",
            path="/open-banking/v4.0/pisp/international-payments/{internationalPaymentId}",
            runtime_inputs=(_RESOURCE_BASE_URL, _ACCESS_TOKEN_REF, _INTERNATIONAL_PAYMENT_ID),
            scripts_31=("OB-301-DOP-101900",),
            scripts_40=("OB-400-DOP-101900",),
            legacy_assertion_ids=("OB3GLOAssertOn200",),
            assertions=(
                _status_assertion(
                    assertion_id="status-200",
                    description="International payment status retrieval returns HTTP 200.",
                    expected_status=200,
                    legacy_assertion_ids=("OB3GLOAssertOn200",),
                ),
            ),
            dependencies=("pis-v4-international-payment-create",),
        ),
        _build_case(
            test_case_id="pis-v4-international-scheduled-payment-consent-create",
            name="Create international scheduled payment consent",
            role="consent",
            method="POST",
            path="/open-banking/v4.0/pisp/international-scheduled-payment-consents",
            runtime_inputs=(_RESOURCE_BASE_URL, _ACCESS_TOKEN_REF),
            scripts_31=("OB-301-DOP-102000",),
            scripts_40=("OB-400-DOP-102000",),
            legacy_assertion_ids=(
                "OB3GLOAssertOn201",
                "OB3GLOFAPIHeader",
                "OB3DOPAssertAwaitingAuthorisation",
                "OB3DOPAssertAwaitingAuthorisationV4",
                "OB3GLOAAssertConsentId",
            ),
            assertions=(
                _status_assertion(
                    assertion_id="status-201",
                    description="International scheduled-consent creation returns HTTP 201.",
                    expected_status=201,
                    legacy_assertion_ids=("OB3GLOAssertOn201",),
                ),
            ),
        ),
        _build_case(
            test_case_id="pis-v4-international-scheduled-payment-consent-read",
            name="Read international scheduled payment consent status",
            role="consent",
            method="GET",
            path="/open-banking/v4.0/pisp/international-scheduled-payment-consents/{internationalScheduledPaymentConsentId}",
            runtime_inputs=(
                _RESOURCE_BASE_URL,
                _ACCESS_TOKEN_REF,
                _INTERNATIONAL_SCHEDULED_PAYMENT_CONSENT_ID,
            ),
            scripts_31=("OB-301-DOP-102100",),
            scripts_40=("OB-400-DOP-102100",),
            legacy_assertion_ids=("OB3GLOAssertOn200", "OB3DOPAssertAuthorised", "OB3DOPAssertAuthorisedV4"),
            assertions=(
                _status_assertion(
                    assertion_id="status-200",
                    description="International scheduled-consent retrieval returns HTTP 200.",
                    expected_status=200,
                    legacy_assertion_ids=("OB3GLOAssertOn200",),
                ),
            ),
            dependencies=("pis-v4-international-scheduled-payment-consent-create",),
        ),
        _build_case(
            test_case_id="pis-v4-international-scheduled-payment-create",
            name="Submit international scheduled payment",
            role="resource",
            method="POST",
            path="/open-banking/v4.0/pisp/international-scheduled-payments",
            runtime_inputs=(
                _RESOURCE_BASE_URL,
                _ACCESS_TOKEN_REF,
                _INTERNATIONAL_SCHEDULED_PAYMENT_CONSENT_ID,
            ),
            scripts_31=("OB-301-DOP-102200",),
            scripts_40=("OB-400-DOP-102200",),
            legacy_assertion_ids=("OB3GLOAssertOn201", "OB3IPAssertInternationalScheduledPaymentId"),
            required_capability_ids=("pis.international-scheduled-payment-submission",),
            assertions=(
                _status_assertion(
                    assertion_id="status-201",
                    description="International scheduled payment submission returns HTTP 201.",
                    expected_status=201,
                    legacy_assertion_ids=("OB3GLOAssertOn201",),
                ),
            ),
            dependencies=("pis-v4-international-scheduled-payment-consent-read",),
        ),
        _build_case(
            test_case_id="pis-v4-international-scheduled-payment-read",
            name="Read international scheduled payment status",
            role="resource",
            method="GET",
            path="/open-banking/v4.0/pisp/international-scheduled-payments/{internationalScheduledPaymentId}",
            runtime_inputs=(
                _RESOURCE_BASE_URL,
                _ACCESS_TOKEN_REF,
                _INTERNATIONAL_SCHEDULED_PAYMENT_ID,
            ),
            scripts_31=("OB-301-DOP-102300",),
            scripts_40=("OB-400-DOP-102300",),
            legacy_assertion_ids=("OB3GLOAssertOn200",),
            assertions=(
                _status_assertion(
                    assertion_id="status-200",
                    description="International scheduled payment status retrieval returns HTTP 200.",
                    expected_status=200,
                    legacy_assertion_ids=("OB3GLOAssertOn200",),
                ),
            ),
            dependencies=("pis-v4-international-scheduled-payment-create",),
        ),
    ),
)
"""PIS payment catalogue expressing legacy FCS v3.1/v4.0 operation coverage."""


def get_pis_payment_catalogue() -> TestCatalogue:
    """Return the bundled PIS payment catalogue.

    Returns:
        Static catalogue for Open Banking UK v4.0 PIS payment coverage.
    """
    return PIS_PAYMENT_CATALOGUE
