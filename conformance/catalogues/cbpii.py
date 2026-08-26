"""CBPII funds-confirmation catalogue imported from legacy FCS manifests."""

from __future__ import annotations

from conformance.catalogue import (
    CatalogueAssertion,
    CatalogueKey,
    CatalogueRequestStep,
    CatalogueTestCase,
    EndpointCapability,
    EndpointRef,
    RuntimeInputRequirement,
    SecurityProfileApplicability,
    TestCaseApplicability,
    TestCatalogue,
)
from conformance.catalogues.common import with_open_banking_request_metadata
from conformance.json_types import JsonObject

CBPII_CATALOGUE_KEY = CatalogueKey(standard="open-banking", version="v4.0", api="cbpii")
"""Canonical key for the Open Banking v4.0 CBPII catalogue."""

CBPII_CATALOGUE_VERSION = "2026.7.23"
"""Version of the imported CBPII legacy-FCS catalogue content."""

_CBPII_CONSENT_CREATE = EndpointRef(method="POST", path="/open-banking/v4.0/cbpii/funds-confirmation-consents")
_CBPII_CONSENT_GET = EndpointRef(
    method="GET",
    path="/open-banking/v4.0/cbpii/funds-confirmation-consents/{consentId}",
)
_CBPII_FUNDS_CONFIRMATION_CREATE = EndpointRef(method="POST", path="/open-banking/v4.0/cbpii/funds-confirmations")
_CBPII_CONSENT_DELETE = EndpointRef(
    method="DELETE",
    path="/open-banking/v4.0/cbpii/funds-confirmation-consents/{consentId}",
)

_CBPII_CONSENT_CREATE_CAPABILITY = EndpointCapability(
    capability_id="cbpii.funds-confirmation-consents.create",
    label="Create funds confirmation consent",
    description="Baseline support for creating CBPII funds-confirmation consents.",
    required=True,
    endpoint_refs=(_CBPII_CONSENT_CREATE,),
)
"""Baseline capability for the CBPII consent-creation endpoint."""

_CBPII_CONSENT_EXPIRATION_FORMATS_CAPABILITY = EndpointCapability(
    capability_id="cbpii.funds-confirmation-consents.expiration-date-time-formats",
    label="Support funds confirmation consent expirationDateTime formats",
    description="Optional support for alternate expirationDateTime formats on consent creation.",
    required=False,
    endpoint_refs=(_CBPII_CONSENT_CREATE,),
)
"""Optional capability for consent-expiration format variants on creation."""

_CBPII_CONSENT_GET_CAPABILITY = EndpointCapability(
    capability_id="cbpii.funds-confirmation-consents.read",
    label="Get funds confirmation consent",
    description="Baseline support for retrieving CBPII funds-confirmation consents.",
    required=True,
    endpoint_refs=(_CBPII_CONSENT_GET,),
)
"""Baseline capability for the CBPII consent-retrieval endpoint."""

_CBPII_FUNDS_CONFIRMATION_CREATE_CAPABILITY = EndpointCapability(
    capability_id="cbpii.funds-confirmations.create",
    label="Create funds confirmation",
    description="Baseline support for creating CBPII funds confirmations.",
    required=True,
    endpoint_refs=(_CBPII_FUNDS_CONFIRMATION_CREATE,),
)
"""Baseline capability for the CBPII funds-confirmation creation endpoint."""

_CBPII_CONSENT_DELETE_CAPABILITY = EndpointCapability(
    capability_id="cbpii.funds-confirmation-consents.delete",
    label="Delete funds confirmation consent",
    description="Baseline support for deleting CBPII funds-confirmation consents.",
    required=True,
    endpoint_refs=(_CBPII_CONSENT_DELETE,),
)
"""Baseline capability for the CBPII consent-deletion endpoint."""

_ALL_PROFILES = SecurityProfileApplicability(profiles=("all",))

_RESOURCE_BASE_URL = RuntimeInputRequirement(
    input_id="resourceBaseUrl",
    input_type="url",
    label="Resource server base URL",
)
_ACCESS_TOKEN = RuntimeInputRequirement(
    input_id="accessToken",
    input_type="string",
    label="Access token with CBPII permissions",
    sensitive=True,
    source="token",
)
_DEBTOR_ACCOUNT_SCHEME_NAME = RuntimeInputRequirement(
    input_id="debtorAccountSchemeName",
    input_type="string",
    label="Debtor account scheme name",
    description="Scheme name for the participant/model-bank account used in CBPII consent creation.",
)
_DEBTOR_ACCOUNT_IDENTIFICATION = RuntimeInputRequirement(
    input_id="debtorAccountIdentification",
    input_type="string",
    label="Debtor account identification",
    sensitive=True,
    description="Account identifier for the participant/model-bank account used in CBPII consent creation.",
)
_DEBTOR_ACCOUNT_NAME = RuntimeInputRequirement(
    input_id="debtorAccountName",
    input_type="string",
    label="Debtor account name",
    description="Account name for the participant/model-bank account used in CBPII consent creation.",
)
_CONSENT_REQUEST_REFERENCE = RuntimeInputRequirement(
    input_id="fundsConfirmationConsentRequestRef",
    input_type="file_reference",
    label="Runtime reference to the OB funds-confirmation-consent request payload",
    source="fixture",
)
_FUNDS_CONFIRMATION_REQUEST_REFERENCE = RuntimeInputRequirement(
    input_id="fundsConfirmationRequestRef",
    input_type="file_reference",
    label="Runtime reference to the OB funds-confirmation request payload",
    source="fixture",
)
_CONSENT_ID = RuntimeInputRequirement(
    input_id="fundsConfirmationConsentId",
    input_type="string",
    label="Funds-confirmation consent id (generated by setup or injected at runtime)",
    required=False,
    source="captured",
)
_INVALID_CONSENT_ID = RuntimeInputRequirement(
    input_id="invalidFundsConfirmationConsentId",
    input_type="string",
    label="Invalid consent identifier used for negative delete coverage",
    source="generated",
)
_UNIQUE_CBPII_REFERENCE = RuntimeInputRequirement(
    input_id="uniqueCbpiiReference",
    input_type="string",
    label="Unique CBPII reference for a funds confirmation request",
    source="generated",
)

_CBPII_CLIENT_CREDENTIALS_AUTH_ID = "cbpii-client-credentials"
"""Semantic authorization id for CBPII client-credentials API requests."""

_CBPII_FUNDS_CONFIRMATION_AUTH_ID = "cbpii-funds-confirmation"
"""Semantic authorization id for CBPII authorised funds-confirmation requests."""

_CAPTURED_FUNDS_CONFIRMATION_CONSENT_ID = "${steps.cbpii-consent-create-core-request.response.body.Data.ConsentId}"
"""Execution-context placeholder for the consent id returned by consent creation."""

_CBPII_CONSENT_REQUEST_TEMPLATE: JsonObject = {
    "Data": {
        "DebtorAccount": {
            "SchemeName": "${runtime.debtorAccountSchemeName}",
            "Identification": "${runtime.debtorAccountIdentification}",
            "Name": "${runtime.debtorAccountName}",
        },
        "ExpirationDateTime": "2026-12-31T23:59:59+00:00",
    }
}
"""CBPII funds-confirmation-consent template parameterised by debtor account config."""

_CBPII_INVALID_ACCOUNT_CONSENT_REQUEST_TEMPLATE: JsonObject = {
    "Data": {
        "DebtorAccount": {
            "SchemeName": "UK.OBIE.InvalidAccountScheme",
            "Identification": "00000000000",
            "Name": "Invalid account",
        }
    }
}
"""Stable negative CBPII consent request fixture with invalid debtor-account data."""

_CBPII_FUNDS_CONFIRMATION_REQUEST_TEMPLATE: JsonObject = {
    "Data": {
        "ConsentId": _CAPTURED_FUNDS_CONFIRMATION_CONSENT_ID,
        "Reference": "${generated.uniqueCbpiiReference}",
        "InstructedAmount": {
            "Amount": "10.00",
            "Currency": "GBP",
        },
    }
}
"""Stable default CBPII funds-confirmation request fixture."""

CBPII_FCS_CATALOGUE = TestCatalogue(
    key=CBPII_CATALOGUE_KEY,
    catalogue_version=CBPII_CATALOGUE_VERSION,
    capabilities=(
        _CBPII_CONSENT_CREATE_CAPABILITY,
        _CBPII_CONSENT_EXPIRATION_FORMATS_CAPABILITY,
        _CBPII_CONSENT_GET_CAPABILITY,
        _CBPII_FUNDS_CONFIRMATION_CREATE_CAPABILITY,
        _CBPII_CONSENT_DELETE_CAPABILITY,
    ),
    test_cases=tuple(
        with_open_banking_request_metadata(test_case)
        for test_case in (
            CatalogueTestCase(
                test_case_id="cbpii-consent-create-core",
                name="Create funds confirmation consent",
                role="consent",
                compliance_scope=(
                    "legacy_manifest:manifests/ob_3.1_cbpii_fca.json#OB-301-CBPII-000001",
                    "legacy_manifest:manifests/ob_3.1_cbpii_fca.json#OB-301-CBPII-000002",
                    "legacy_manifest:manifests/ob_3.1_cbpii_fca.json#OB-312-CBPII-000100",
                    "legacy_manifest:manifests/ob_4.0_cbpii_fca.json#OB-400-CBPII-000001",
                    "legacy_manifest:manifests/ob_4.0_cbpii_fca.json#OB-400-CBPII-000002",
                    "legacy_manifest:manifests/ob_4.0_cbpii_fca.json#OB-312-CBPII-000100",
                    "legacy_asserts:OB3GLOAssertOn201|OB3GLOFAPIHeader|OB3GLOAAssertConsentId|"
                    "OB3DOPAssertAwaitingAuthorisation|OB3DOPAssertAwaitingAuthorisationV4|OB3GLOAssertContentType",
                ),
                applicability=TestCaseApplicability(
                    security_profiles=_ALL_PROFILES,
                    endpoint_refs=(_CBPII_CONSENT_CREATE,),
                    required_capability_ids=("cbpii.funds-confirmation-consents.create",),
                ),
                mandatory=True,
                runtime_input_requirements=(
                    _RESOURCE_BASE_URL,
                    _ACCESS_TOKEN,
                    _DEBTOR_ACCOUNT_SCHEME_NAME,
                    _DEBTOR_ACCOUNT_IDENTIFICATION,
                    _DEBTOR_ACCOUNT_NAME,
                    _CONSENT_REQUEST_REFERENCE,
                ),
                request_steps=(
                    CatalogueRequestStep(
                        step_id="cbpii-consent-create-core-request",
                        name="POST funds confirmation consent",
                        method="POST",
                        path="/open-banking/v4.0/cbpii/funds-confirmation-consents",
                        runtime_input_refs=("resourceBaseUrl",),
                        body_template=_CBPII_CONSENT_REQUEST_TEMPLATE,
                        required_token_id=_CBPII_CLIENT_CREDENTIALS_AUTH_ID,
                    ),
                ),
                assertions=(
                    CatalogueAssertion(
                        assertion_id="status-201",
                        kind="http_status",
                        description="Consent creation responds with HTTP 201",
                        rule={"expected": 201},
                    ),
                    CatalogueAssertion(
                        assertion_id="header-fapi-interaction-id",
                        kind="header",
                        description="FAPI interaction header is present",
                        rule={"name": "x-fapi-interaction-id", "required": True},
                    ),
                    CatalogueAssertion(
                        assertion_id="data-consent-id-present",
                        kind="json_field",
                        description="Response contains Data.ConsentId",
                        rule={"path": "Data.ConsentId", "present": True},
                    ),
                    CatalogueAssertion(
                        assertion_id="data-status-awaiting-authorisation",
                        kind="json_field",
                        description="Response status is AWAU",
                        rule={"path": "Data.Status", "expected": "AWAU"},
                    ),
                    CatalogueAssertion(
                        assertion_id="header-content-type-json",
                        kind="header",
                        description="Content-Type header indicates JSON",
                        rule={"name": "Content-Type", "contains": "application/json"},
                    ),
                ),
            ),
            CatalogueTestCase(
                test_case_id="cbpii-consent-create-invalid-account-data",
                name="Reject consent creation with invalid debtor account values",
                role="consent",
                compliance_scope=(
                    "legacy_manifest:manifests/ob_3.1_cbpii_fca.json#OB-301-CBPII-000006",
                    "legacy_manifest:manifests/ob_3.1_cbpii_fca.json#OB-301-CBPII-000007",
                    "legacy_manifest:manifests/ob_3.1_cbpii_fca.json#OB-301-CBPII-000008",
                    "legacy_manifest:manifests/ob_4.0_cbpii_fca.json#OB-400-CBPII-000006",
                    "legacy_manifest:manifests/ob_4.0_cbpii_fca.json#OB-400-CBPII-000007",
                    "legacy_manifest:manifests/ob_4.0_cbpii_fca.json#OB-400-CBPII-000008",
                    "legacy_asserts:OB3GLOAssertOn400",
                ),
                applicability=TestCaseApplicability(
                    security_profiles=_ALL_PROFILES,
                    endpoint_refs=(_CBPII_CONSENT_CREATE,),
                    required_capability_ids=("cbpii.funds-confirmation-consents.create",),
                ),
                mandatory=True,
                runtime_input_requirements=(
                    _RESOURCE_BASE_URL,
                    _ACCESS_TOKEN,
                    _DEBTOR_ACCOUNT_SCHEME_NAME,
                    _DEBTOR_ACCOUNT_IDENTIFICATION,
                    _DEBTOR_ACCOUNT_NAME,
                    _CONSENT_REQUEST_REFERENCE,
                ),
                request_steps=(
                    CatalogueRequestStep(
                        step_id="cbpii-consent-create-invalid-account-data-request",
                        name="POST funds confirmation consent with invalid debtor account",
                        method="POST",
                        path="/open-banking/v4.0/cbpii/funds-confirmation-consents",
                        runtime_input_refs=("resourceBaseUrl",),
                        body_template=_CBPII_INVALID_ACCOUNT_CONSENT_REQUEST_TEMPLATE,
                        required_token_id=_CBPII_CLIENT_CREDENTIALS_AUTH_ID,
                    ),
                ),
                assertions=(
                    CatalogueAssertion(
                        assertion_id="status-400",
                        kind="http_status",
                        description="Invalid debtor account input is rejected",
                        rule={"expected": 400},
                    ),
                ),
            ),
            CatalogueTestCase(
                test_case_id="cbpii-consent-create-expiration-formats",
                name="Accept supported expirationDateTime formats",
                role="consent",
                compliance_scope=(
                    "legacy_manifest:manifests/ob_3.1_cbpii_fca.json#OB-301-CBPII-000009",
                    "legacy_manifest:manifests/ob_3.1_cbpii_fca.json#OB-301-CBPII-000010",
                    "legacy_manifest:manifests/ob_3.1_cbpii_fca.json#OB-301-CBPII-000011",
                    "legacy_manifest:manifests/ob_3.1_cbpii_fca.json#OB-301-CBPII-000012",
                    "legacy_manifest:manifests/ob_4.0_cbpii_fca.json#OB-400-CBPII-000009",
                    "legacy_manifest:manifests/ob_4.0_cbpii_fca.json#OB-400-CBPII-000010",
                    "legacy_manifest:manifests/ob_4.0_cbpii_fca.json#OB-400-CBPII-000011",
                    "legacy_manifest:manifests/ob_4.0_cbpii_fca.json#OB-400-CBPII-000012",
                    "legacy_asserts:OB3GLOAssertOn201",
                ),
                applicability=TestCaseApplicability(
                    security_profiles=_ALL_PROFILES,
                    endpoint_refs=(_CBPII_CONSENT_CREATE,),
                    required_capability_ids=("cbpii.funds-confirmation-consents.expiration-date-time-formats",),
                ),
                mandatory=True,
                runtime_input_requirements=(
                    _RESOURCE_BASE_URL,
                    _ACCESS_TOKEN,
                    _DEBTOR_ACCOUNT_SCHEME_NAME,
                    _DEBTOR_ACCOUNT_IDENTIFICATION,
                    _DEBTOR_ACCOUNT_NAME,
                    _CONSENT_REQUEST_REFERENCE,
                ),
                request_steps=(
                    CatalogueRequestStep(
                        step_id="cbpii-consent-create-expiration-formats-request",
                        name="POST funds confirmation consent with allowed expirationDateTime format",
                        method="POST",
                        path="/open-banking/v4.0/cbpii/funds-confirmation-consents",
                        runtime_input_refs=("resourceBaseUrl",),
                        body_template=_CBPII_CONSENT_REQUEST_TEMPLATE,
                        required_token_id=_CBPII_CLIENT_CREDENTIALS_AUTH_ID,
                    ),
                ),
                assertions=(
                    CatalogueAssertion(
                        assertion_id="status-201",
                        kind="http_status",
                        description="Supported expirationDateTime format is accepted",
                        rule={"expected": 201},
                    ),
                ),
            ),
            CatalogueTestCase(
                test_case_id="cbpii-consent-get-authorised",
                name="Get an authorised funds confirmation consent",
                role="resource",
                compliance_scope=(
                    "legacy_manifest:manifests/ob_3.1_cbpii_fca.json#OB-301-CBPII-000003",
                    "legacy_manifest:manifests/ob_4.0_cbpii_fca.json#OB-400-CBPII-000003",
                    "legacy_asserts:OB3GLOAssertOn200|OB3GLOFAPIHeader|OB3GLOAssertContentType|"
                    "OB3DOPAssertAuthorised|OB3DOPAssertAuthorisedV4",
                ),
                applicability=TestCaseApplicability(
                    security_profiles=_ALL_PROFILES,
                    endpoint_refs=(_CBPII_CONSENT_GET,),
                    required_capability_ids=("cbpii.funds-confirmation-consents.read",),
                ),
                mandatory=True,
                dependencies=("cbpii-consent-create-core",),
                runtime_input_requirements=(
                    _RESOURCE_BASE_URL,
                    _ACCESS_TOKEN,
                    _CONSENT_ID,
                ),
                request_steps=(
                    CatalogueRequestStep(
                        step_id="cbpii-consent-get-authorised-request",
                        name="GET funds confirmation consent",
                        method="GET",
                        path=(
                            "/open-banking/v4.0/cbpii/funds-confirmation-consents/"
                            f"{_CAPTURED_FUNDS_CONFIRMATION_CONSENT_ID}"
                        ),
                        runtime_input_refs=("resourceBaseUrl",),
                        required_token_id=_CBPII_CLIENT_CREDENTIALS_AUTH_ID,
                    ),
                ),
                assertions=(
                    CatalogueAssertion(
                        assertion_id="status-200",
                        kind="http_status",
                        description="Consent retrieval responds with HTTP 200",
                        rule={"expected": 200},
                    ),
                    CatalogueAssertion(
                        assertion_id="data-status-authorised",
                        kind="json_field",
                        description="Retrieved consent status is AUTH",
                        rule={"path": "Data.Status", "expected": "AUTH"},
                    ),
                ),
            ),
            CatalogueTestCase(
                test_case_id="cbpii-funds-confirmation-create",
                name="Create a funds confirmation",
                role="resource",
                compliance_scope=(
                    "legacy_manifest:manifests/ob_3.1_cbpii_fca.json#OB-301-CBPII-000004",
                    "legacy_manifest:manifests/ob_4.0_cbpii_fca.json#OB-400-CBPII-000004",
                    "legacy_asserts:OB3GLOAssertOn201|OB3GLOFAPIHeader|OB3GLOAssertContentType",
                ),
                applicability=TestCaseApplicability(
                    security_profiles=_ALL_PROFILES,
                    endpoint_refs=(_CBPII_FUNDS_CONFIRMATION_CREATE,),
                    required_capability_ids=("cbpii.funds-confirmations.create",),
                ),
                mandatory=True,
                dependencies=("cbpii-consent-create-core",),
                runtime_input_requirements=(
                    _RESOURCE_BASE_URL,
                    _ACCESS_TOKEN,
                    _CONSENT_ID,
                    _FUNDS_CONFIRMATION_REQUEST_REFERENCE,
                    _UNIQUE_CBPII_REFERENCE,
                ),
                request_steps=(
                    CatalogueRequestStep(
                        step_id="cbpii-funds-confirmation-create-request",
                        name="POST funds confirmation",
                        method="POST",
                        path="/open-banking/v4.0/cbpii/funds-confirmations",
                        runtime_input_refs=("resourceBaseUrl",),
                        body_template=_CBPII_FUNDS_CONFIRMATION_REQUEST_TEMPLATE,
                        generated_values={"uniqueCbpiiReference": "uuid4-hex"},
                        required_token_id=_CBPII_FUNDS_CONFIRMATION_AUTH_ID,
                    ),
                ),
                assertions=(
                    CatalogueAssertion(
                        assertion_id="status-201",
                        kind="http_status",
                        description="Funds confirmation creation responds with HTTP 201",
                        rule={"expected": 201},
                    ),
                ),
            ),
            CatalogueTestCase(
                test_case_id="cbpii-consent-delete",
                name="Delete a funds confirmation consent",
                role="resource",
                compliance_scope=(
                    "legacy_manifest:manifests/ob_3.1_cbpii_fca.json#OB-301-CBPII-000005",
                    "legacy_manifest:manifests/ob_4.0_cbpii_fca.json#OB-400-CBPII-000005",
                    "legacy_asserts:OB3GLOAssertOn204|OB3GLOFAPIHeader",
                ),
                applicability=TestCaseApplicability(
                    security_profiles=_ALL_PROFILES,
                    endpoint_refs=(_CBPII_CONSENT_DELETE,),
                    required_capability_ids=("cbpii.funds-confirmation-consents.delete",),
                ),
                mandatory=True,
                dependencies=("cbpii-consent-create-core",),
                runtime_input_requirements=(
                    _RESOURCE_BASE_URL,
                    _ACCESS_TOKEN,
                    _CONSENT_ID,
                ),
                request_steps=(
                    CatalogueRequestStep(
                        step_id="cbpii-consent-delete-request",
                        name="DELETE funds confirmation consent",
                        method="DELETE",
                        path=(
                            "/open-banking/v4.0/cbpii/funds-confirmation-consents/"
                            f"{_CAPTURED_FUNDS_CONFIRMATION_CONSENT_ID}"
                        ),
                        runtime_input_refs=("resourceBaseUrl",),
                        required_token_id=_CBPII_CLIENT_CREDENTIALS_AUTH_ID,
                    ),
                ),
                assertions=(
                    CatalogueAssertion(
                        assertion_id="status-204",
                        kind="http_status",
                        description="Consent deletion responds with HTTP 204",
                        rule={"expected": 204},
                    ),
                ),
            ),
            CatalogueTestCase(
                test_case_id="cbpii-consent-delete-invalid-id",
                name="Reject consent deletion with invalid consent id",
                role="resource",
                compliance_scope=(
                    "legacy_manifest:manifests/ob_3.1_cbpii_fca.json#OB-301-CBPII-000009(delete)",
                    "legacy_manifest:manifests/ob_4.0_cbpii_fca.json#OB-400-CBPII-000009(delete)",
                    "legacy_asserts:OB3GLOAssertOn400",
                ),
                applicability=TestCaseApplicability(
                    security_profiles=_ALL_PROFILES,
                    endpoint_refs=(_CBPII_CONSENT_DELETE,),
                    required_capability_ids=("cbpii.funds-confirmation-consents.delete",),
                ),
                mandatory=True,
                runtime_input_requirements=(
                    _RESOURCE_BASE_URL,
                    _ACCESS_TOKEN,
                    _INVALID_CONSENT_ID,
                ),
                request_steps=(
                    CatalogueRequestStep(
                        step_id="cbpii-consent-delete-invalid-id-request",
                        name="DELETE funds confirmation consent with invalid consent id",
                        method="DELETE",
                        path=(
                            "/open-banking/v4.0/cbpii/funds-confirmation-consents/"
                            "${generated.invalidFundsConfirmationConsentId}"
                        ),
                        runtime_input_refs=("resourceBaseUrl",),
                        generated_values={"invalidFundsConfirmationConsentId": "invalid-resource-id"},
                        required_token_id=_CBPII_CLIENT_CREDENTIALS_AUTH_ID,
                    ),
                ),
                assertions=(
                    CatalogueAssertion(
                        assertion_id="status-400",
                        kind="http_status",
                        description="Invalid consent id deletion request is rejected",
                        rule={"expected": 400},
                    ),
                ),
            ),
        )
    ),
)
"""CBPII FCS catalogue converted from legacy OB 3.1/4.0 manifests."""

__all__ = ["CBPII_CATALOGUE_KEY", "CBPII_CATALOGUE_VERSION", "CBPII_FCS_CATALOGUE"]
