"""Legacy FCS-derived Open Banking UK payment (PIS) test catalogue."""

from __future__ import annotations

from conformance.catalogue import (
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
from conformance.json_types import JsonObject, JsonValue

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

_PIS_DOMESTIC_PAYMENT_AUTH_ID = "pis-domestic-payment-access"
"""Semantic authorization id for PSU-authorised domestic payment requests."""

_PIS_DOMESTIC_SCHEDULED_PAYMENT_AUTH_ID = "pis-domestic-scheduled-payment-access"
"""Semantic authorization id for PSU-authorised domestic scheduled payment requests."""

_PIS_DOMESTIC_STANDING_ORDER_AUTH_ID = "pis-domestic-standing-order-access"
"""Semantic authorization id for PSU-authorised domestic standing-order requests."""

_PIS_INTERNATIONAL_PAYMENT_AUTH_ID = "pis-international-payment-access"
"""Semantic authorization id for PSU-authorised international payment requests."""

_PIS_INTERNATIONAL_SCHEDULED_PAYMENT_AUTH_ID = "pis-international-scheduled-payment-access"
"""Semantic authorization id for PSU-authorised international scheduled payment requests."""

_PIS_V31_SPECIFICATION_VERSIONS = ("3.1", "3.1.11")
"""User-facing Read/Write versions that can execute legacy v3.1-only PIS cases."""

_PIS_V40_SPECIFICATION_VERSIONS = ("4.0", "4.0.0", "4.0.1")
"""User-facing Read/Write versions that can execute legacy v4 PIS cases."""

_PIS_PSU_AUTH_TOKEN_IDS_BY_CASE_ID = {
    "pis-v4-domestic-payment-funds-confirmation": _PIS_DOMESTIC_PAYMENT_AUTH_ID,
    "pis-v4-domestic-payment-create": _PIS_DOMESTIC_PAYMENT_AUTH_ID,
    "pis-v4-domestic-scheduled-payment-create": _PIS_DOMESTIC_SCHEDULED_PAYMENT_AUTH_ID,
    "pis-v4-domestic-standing-order-create": _PIS_DOMESTIC_STANDING_ORDER_AUTH_ID,
    "pis-v4-domestic-standing-order-reject-invalid-frequency": _PIS_DOMESTIC_STANDING_ORDER_AUTH_ID,
    "pis-v4-international-payment-create": _PIS_INTERNATIONAL_PAYMENT_AUTH_ID,
    "pis-v4-international-scheduled-payment-create": _PIS_INTERNATIONAL_SCHEDULED_PAYMENT_AUTH_ID,
}
"""Per-flow semantic token ids for PIS submit/funds requests that need PSU authorisation."""

_PIS_CREDITOR_ACCOUNT_SCHEME_NAME = RuntimeInputRequirement(
    input_id="pisCreditorAccountSchemeName",
    input_type="string",
    label="Domestic creditor account scheme name",
)
"""Domestic PIS creditor-account scheme name used in payment initiation bodies."""

_PIS_CREDITOR_ACCOUNT_IDENTIFICATION = RuntimeInputRequirement(
    input_id="pisCreditorAccountIdentification",
    input_type="string",
    label="Domestic creditor account identification",
)
"""Domestic PIS creditor-account identifier used in payment initiation bodies."""

_PIS_CREDITOR_ACCOUNT_NAME = RuntimeInputRequirement(
    input_id="pisCreditorAccountName",
    input_type="string",
    label="Domestic creditor account name",
)
"""Domestic PIS creditor-account display name used in payment initiation bodies."""

_PIS_INTERNATIONAL_CREDITOR_ACCOUNT_SCHEME_NAME = RuntimeInputRequirement(
    input_id="pisInternationalCreditorAccountSchemeName",
    input_type="string",
    label="International creditor account scheme name",
)
"""International PIS creditor-account scheme name used in payment initiation bodies."""

_PIS_INTERNATIONAL_CREDITOR_ACCOUNT_IDENTIFICATION = RuntimeInputRequirement(
    input_id="pisInternationalCreditorAccountIdentification",
    input_type="string",
    label="International creditor account identification",
)
"""International PIS creditor-account identifier used in payment initiation bodies."""

_PIS_INTERNATIONAL_CREDITOR_ACCOUNT_NAME = RuntimeInputRequirement(
    input_id="pisInternationalCreditorAccountName",
    input_type="string",
    label="International creditor account name",
)
"""International PIS creditor-account display name used in payment initiation bodies."""

_PIS_INSTRUCTED_AMOUNT_AMOUNT = RuntimeInputRequirement(
    input_id="pisInstructedAmountAmount",
    input_type="string",
    label="PIS instructed amount",
)
"""Payment amount used in PIS payment initiation bodies."""

_PIS_INSTRUCTED_AMOUNT_CURRENCY = RuntimeInputRequirement(
    input_id="pisInstructedAmountCurrency",
    input_type="string",
    label="PIS instructed amount currency",
)
"""Currency for the instructed amount in PIS payment initiation bodies."""

_PIS_CURRENCY_OF_TRANSFER = RuntimeInputRequirement(
    input_id="pisCurrencyOfTransfer",
    input_type="string",
    label="PIS currency of transfer",
)
"""Currency of transfer used in international PIS payment initiation bodies."""

_PIS_REQUESTED_EXECUTION_DATE_TIME = RuntimeInputRequirement(
    input_id="pisRequestedExecutionDateTime",
    input_type="string",
    label="PIS requested execution date/time",
)
"""Requested execution date/time used in scheduled PIS payment bodies."""

_PIS_FIRST_PAYMENT_DATE_TIME = RuntimeInputRequirement(
    input_id="pisFirstPaymentDateTime",
    input_type="string",
    label="PIS first payment date/time",
)
"""First payment date/time used in domestic standing-order bodies."""

_PIS_STANDING_ORDER_FREQUENCY_TYPE = RuntimeInputRequirement(
    input_id="pisStandingOrderFrequencyType",
    input_type="string",
    label="PIS standing-order frequency type",
)
"""Standing-order frequency type used in domestic standing-order bodies."""

_PIS_STANDING_ORDER_FREQUENCY_POINT_IN_TIME = RuntimeInputRequirement(
    input_id="pisStandingOrderFrequencyPointInTime",
    input_type="string",
    label="PIS standing-order frequency point in time",
)
"""Standing-order frequency point-in-time used in domestic standing-order bodies."""

_PIS_DOMESTIC_PAYMENT_INPUTS = (
    _RESOURCE_BASE_URL,
    _ACCESS_TOKEN_REF,
    _PIS_CREDITOR_ACCOUNT_SCHEME_NAME,
    _PIS_CREDITOR_ACCOUNT_IDENTIFICATION,
    _PIS_CREDITOR_ACCOUNT_NAME,
    _PIS_INSTRUCTED_AMOUNT_AMOUNT,
    _PIS_INSTRUCTED_AMOUNT_CURRENCY,
)
"""Runtime inputs required to build domestic payment initiation payloads."""

_PIS_DOMESTIC_SCHEDULED_PAYMENT_INPUTS = (
    *_PIS_DOMESTIC_PAYMENT_INPUTS,
    _PIS_REQUESTED_EXECUTION_DATE_TIME,
)
"""Runtime inputs required to build domestic scheduled payment payloads."""

_PIS_DOMESTIC_STANDING_ORDER_INPUTS = (
    _RESOURCE_BASE_URL,
    _ACCESS_TOKEN_REF,
    _PIS_CREDITOR_ACCOUNT_SCHEME_NAME,
    _PIS_CREDITOR_ACCOUNT_IDENTIFICATION,
    _PIS_CREDITOR_ACCOUNT_NAME,
    _PIS_INSTRUCTED_AMOUNT_AMOUNT,
    _PIS_INSTRUCTED_AMOUNT_CURRENCY,
    _PIS_FIRST_PAYMENT_DATE_TIME,
    _PIS_STANDING_ORDER_FREQUENCY_TYPE,
    _PIS_STANDING_ORDER_FREQUENCY_POINT_IN_TIME,
)
"""Runtime inputs required to build domestic standing-order payloads."""

_PIS_INTERNATIONAL_PAYMENT_INPUTS = (
    _RESOURCE_BASE_URL,
    _ACCESS_TOKEN_REF,
    _PIS_INTERNATIONAL_CREDITOR_ACCOUNT_SCHEME_NAME,
    _PIS_INTERNATIONAL_CREDITOR_ACCOUNT_IDENTIFICATION,
    _PIS_INTERNATIONAL_CREDITOR_ACCOUNT_NAME,
    _PIS_INSTRUCTED_AMOUNT_AMOUNT,
    _PIS_INSTRUCTED_AMOUNT_CURRENCY,
    _PIS_CURRENCY_OF_TRANSFER,
)
"""Runtime inputs required to build international payment initiation payloads."""

_PIS_INTERNATIONAL_SCHEDULED_PAYMENT_INPUTS = (
    *_PIS_INTERNATIONAL_PAYMENT_INPUTS,
    _PIS_REQUESTED_EXECUTION_DATE_TIME,
)
"""Runtime inputs required to build international scheduled payment payloads."""

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

_PIS_DOMESTIC_CREDITOR_ACCOUNT_TEMPLATE: JsonObject = {
    "SchemeName": "${runtime.pisCreditorAccountSchemeName}",
    "Identification": "${runtime.pisCreditorAccountIdentification}",
    "Name": "${runtime.pisCreditorAccountName}",
}
"""Domestic creditor-account object template for PIS initiation payloads."""

_PIS_INTERNATIONAL_CREDITOR_ACCOUNT_TEMPLATE: JsonObject = {
    "SchemeName": "${runtime.pisInternationalCreditorAccountSchemeName}",
    "Identification": "${runtime.pisInternationalCreditorAccountIdentification}",
    "Name": "${runtime.pisInternationalCreditorAccountName}",
}
"""International creditor-account object template for PIS initiation payloads."""

_PIS_INSTRUCTED_AMOUNT_TEMPLATE: JsonObject = {
    "Amount": "${runtime.pisInstructedAmountAmount}",
    "Currency": "${runtime.pisInstructedAmountCurrency}",
}
"""Instructed-amount object template for PIS initiation payloads."""

_PIS_STANDING_ORDER_FREQUENCY_TEMPLATE: JsonObject = {
    "Type": "${runtime.pisStandingOrderFrequencyType}",
    "PointInTime": "${runtime.pisStandingOrderFrequencyPointInTime}",
}
"""Standing-order frequency object template for valid PIS standing orders."""

_PIS_INVALID_STANDING_ORDER_FREQUENCY_TEMPLATE: JsonObject = {
    "Type": "${runtime.pisStandingOrderFrequencyType}",
    "PointInTime": "${runtime.pisStandingOrderFrequencyPointInTime}",
    "CountPerPeriod": 1,
}
"""Standing-order frequency template with a deliberately invalid field combination."""

_PIS_DOMESTIC_PAYMENT_INITIATION_TEMPLATE: JsonObject = {
    "InstructionIdentification": "${generated.instructionIdentification}",
    "EndToEndIdentification": "${generated.endToEndIdentification}",
    "InstructedAmount": _PIS_INSTRUCTED_AMOUNT_TEMPLATE,
    "CreditorAccount": _PIS_DOMESTIC_CREDITOR_ACCOUNT_TEMPLATE,
}
"""Domestic payment initiation body used when creating consent requests."""

_PIS_DOMESTIC_SCHEDULED_PAYMENT_INITIATION_TEMPLATE: JsonObject = {
    "InstructionIdentification": "${generated.instructionIdentification}",
    "EndToEndIdentification": "${generated.endToEndIdentification}",
    "RequestedExecutionDateTime": "${runtime.pisRequestedExecutionDateTime}",
    "InstructedAmount": _PIS_INSTRUCTED_AMOUNT_TEMPLATE,
    "CreditorAccount": _PIS_DOMESTIC_CREDITOR_ACCOUNT_TEMPLATE,
}
"""Domestic scheduled payment initiation body used when creating consent requests."""

_PIS_DOMESTIC_STANDING_ORDER_INITIATION_TEMPLATE: JsonObject = {
    "FirstPaymentAmount": _PIS_INSTRUCTED_AMOUNT_TEMPLATE,
    "CreditorAccount": _PIS_DOMESTIC_CREDITOR_ACCOUNT_TEMPLATE,
    "MandateRelatedInformation": {
        "FirstPaymentDateTime": "${runtime.pisFirstPaymentDateTime}",
        "Frequency": _PIS_STANDING_ORDER_FREQUENCY_TEMPLATE,
    },
}
"""Domestic standing-order initiation body shared by consent and submission requests."""

_PIS_INVALID_FREQUENCY_STANDING_ORDER_INITIATION_TEMPLATE: JsonObject = {
    "FirstPaymentAmount": _PIS_INSTRUCTED_AMOUNT_TEMPLATE,
    "CreditorAccount": _PIS_DOMESTIC_CREDITOR_ACCOUNT_TEMPLATE,
    "MandateRelatedInformation": {
        "FirstPaymentDateTime": "${runtime.pisFirstPaymentDateTime}",
        "Frequency": _PIS_INVALID_STANDING_ORDER_FREQUENCY_TEMPLATE,
    },
}
"""Domestic standing-order initiation body with an invalid frequency combination."""

_PIS_INTERNATIONAL_PAYMENT_INITIATION_TEMPLATE: JsonObject = {
    "InstructionIdentification": "${generated.instructionIdentification}",
    "EndToEndIdentification": "${generated.endToEndIdentification}",
    "CurrencyOfTransfer": "${runtime.pisCurrencyOfTransfer}",
    "InstructedAmount": _PIS_INSTRUCTED_AMOUNT_TEMPLATE,
    "CreditorAccount": _PIS_INTERNATIONAL_CREDITOR_ACCOUNT_TEMPLATE,
}
"""International payment initiation body used when creating consent requests."""

_PIS_INTERNATIONAL_SCHEDULED_PAYMENT_INITIATION_TEMPLATE: JsonObject = {
    "InstructionIdentification": "${generated.instructionIdentification}",
    "EndToEndIdentification": "${generated.endToEndIdentification}",
    "RequestedExecutionDateTime": "${runtime.pisRequestedExecutionDateTime}",
    "CurrencyOfTransfer": "${runtime.pisCurrencyOfTransfer}",
    "InstructedAmount": _PIS_INSTRUCTED_AMOUNT_TEMPLATE,
    "CreditorAccount": _PIS_INTERNATIONAL_CREDITOR_ACCOUNT_TEMPLATE,
}
"""International scheduled payment initiation body used when creating consent requests."""

_PIS_GENERATED_INSTRUCTION_IDS: dict[str, GeneratedRuntimeValue] = {
    "instructionIdentification": "uuid4-hex",
    "endToEndIdentification": "uuid4-hex",
}
"""Generated identifiers required to keep repeatable PIS plans rerunnable."""

_PIS_GENERATED_OFFSET_DATETIME: dict[str, GeneratedRuntimeValue] = {
    **_PIS_GENERATED_INSTRUCTION_IDS,
    "requestedExecutionDateTime": "next-day-date-time-offset",
}
"""Generated identifiers and offset datetime for legacy scheduled-payment variants."""

_PIS_GENERATED_UTC_DATETIME: dict[str, GeneratedRuntimeValue] = {
    **_PIS_GENERATED_INSTRUCTION_IDS,
    "requestedExecutionDateTime": "next-day-date-time-utc",
}
"""Generated identifiers and UTC datetime for legacy scheduled-payment variants."""


def _consent_initiation_value(consent_step_id: str, field_path: str) -> str:
    """Return a placeholder for a field from a PIS consent initiation response.

    Args:
        consent_step_id: Request step id for the payment-consent creation call.
        field_path: Dot-path under ``Data.Initiation`` to copy into the
            matching payment-submission request.

    Returns:
        Execution placeholder that resolves to the consent initiation field.
    """
    return f"${{steps.{consent_step_id}.response.body.Data.Initiation.{field_path}}}"


def _domestic_scheduled_payment_initiation_template(requested_execution_date_time: str) -> JsonObject:
    """Build a domestic scheduled-payment initiation template.

    Args:
        requested_execution_date_time: Runtime or generated placeholder for the
            requested execution datetime.

    Returns:
        Domestic scheduled-payment initiation body template.
    """
    return {
        "InstructionIdentification": "${generated.instructionIdentification}",
        "EndToEndIdentification": "${generated.endToEndIdentification}",
        "RequestedExecutionDateTime": requested_execution_date_time,
        "InstructedAmount": _PIS_INSTRUCTED_AMOUNT_TEMPLATE,
        "CreditorAccount": _PIS_DOMESTIC_CREDITOR_ACCOUNT_TEMPLATE,
    }


_PIS_DOMESTIC_PAYMENT_CONSENT_INITIATION_TEMPLATE: JsonObject = {
    "InstructionIdentification": _consent_initiation_value(
        "pis-v4-domestic-payment-consent-create-request", "InstructionIdentification"
    ),
    "EndToEndIdentification": _consent_initiation_value(
        "pis-v4-domestic-payment-consent-create-request", "EndToEndIdentification"
    ),
    "InstructedAmount": {
        "Amount": _consent_initiation_value(
            "pis-v4-domestic-payment-consent-create-request", "InstructedAmount.Amount"
        ),
        "Currency": _consent_initiation_value(
            "pis-v4-domestic-payment-consent-create-request", "InstructedAmount.Currency"
        ),
    },
    "CreditorAccount": {
        "SchemeName": _consent_initiation_value(
            "pis-v4-domestic-payment-consent-create-request", "CreditorAccount.SchemeName"
        ),
        "Identification": _consent_initiation_value(
            "pis-v4-domestic-payment-consent-create-request", "CreditorAccount.Identification"
        ),
        "Name": _consent_initiation_value("pis-v4-domestic-payment-consent-create-request", "CreditorAccount.Name"),
    },
}
"""Domestic payment submission initiation copied from the authorised consent response."""

_PIS_DOMESTIC_SCHEDULED_PAYMENT_CONSENT_INITIATION_TEMPLATE: JsonObject = {
    "InstructionIdentification": _consent_initiation_value(
        "pis-v4-domestic-scheduled-payment-consent-create-request", "InstructionIdentification"
    ),
    "EndToEndIdentification": _consent_initiation_value(
        "pis-v4-domestic-scheduled-payment-consent-create-request", "EndToEndIdentification"
    ),
    "RequestedExecutionDateTime": _consent_initiation_value(
        "pis-v4-domestic-scheduled-payment-consent-create-request", "RequestedExecutionDateTime"
    ),
    "InstructedAmount": {
        "Amount": _consent_initiation_value(
            "pis-v4-domestic-scheduled-payment-consent-create-request", "InstructedAmount.Amount"
        ),
        "Currency": _consent_initiation_value(
            "pis-v4-domestic-scheduled-payment-consent-create-request", "InstructedAmount.Currency"
        ),
    },
    "CreditorAccount": {
        "SchemeName": _consent_initiation_value(
            "pis-v4-domestic-scheduled-payment-consent-create-request", "CreditorAccount.SchemeName"
        ),
        "Identification": _consent_initiation_value(
            "pis-v4-domestic-scheduled-payment-consent-create-request", "CreditorAccount.Identification"
        ),
        "Name": _consent_initiation_value(
            "pis-v4-domestic-scheduled-payment-consent-create-request", "CreditorAccount.Name"
        ),
    },
}
"""Domestic scheduled-payment submission initiation copied from the consent response."""

_PIS_INTERNATIONAL_PAYMENT_CONSENT_INITIATION_TEMPLATE: JsonObject = {
    "InstructionIdentification": _consent_initiation_value(
        "pis-v4-international-payment-consent-create-request", "InstructionIdentification"
    ),
    "EndToEndIdentification": _consent_initiation_value(
        "pis-v4-international-payment-consent-create-request", "EndToEndIdentification"
    ),
    "CurrencyOfTransfer": _consent_initiation_value(
        "pis-v4-international-payment-consent-create-request", "CurrencyOfTransfer"
    ),
    "InstructedAmount": {
        "Amount": _consent_initiation_value(
            "pis-v4-international-payment-consent-create-request", "InstructedAmount.Amount"
        ),
        "Currency": _consent_initiation_value(
            "pis-v4-international-payment-consent-create-request", "InstructedAmount.Currency"
        ),
    },
    "CreditorAccount": {
        "SchemeName": _consent_initiation_value(
            "pis-v4-international-payment-consent-create-request", "CreditorAccount.SchemeName"
        ),
        "Identification": _consent_initiation_value(
            "pis-v4-international-payment-consent-create-request", "CreditorAccount.Identification"
        ),
        "Name": _consent_initiation_value(
            "pis-v4-international-payment-consent-create-request", "CreditorAccount.Name"
        ),
    },
}
"""International payment submission initiation copied from the authorised consent response."""

_PIS_INTERNATIONAL_SCHEDULED_PAYMENT_CONSENT_INITIATION_TEMPLATE: JsonObject = {
    "InstructionIdentification": _consent_initiation_value(
        "pis-v4-international-scheduled-payment-consent-create-request", "InstructionIdentification"
    ),
    "EndToEndIdentification": _consent_initiation_value(
        "pis-v4-international-scheduled-payment-consent-create-request", "EndToEndIdentification"
    ),
    "RequestedExecutionDateTime": _consent_initiation_value(
        "pis-v4-international-scheduled-payment-consent-create-request", "RequestedExecutionDateTime"
    ),
    "CurrencyOfTransfer": _consent_initiation_value(
        "pis-v4-international-scheduled-payment-consent-create-request", "CurrencyOfTransfer"
    ),
    "InstructedAmount": {
        "Amount": _consent_initiation_value(
            "pis-v4-international-scheduled-payment-consent-create-request", "InstructedAmount.Amount"
        ),
        "Currency": _consent_initiation_value(
            "pis-v4-international-scheduled-payment-consent-create-request", "InstructedAmount.Currency"
        ),
    },
    "CreditorAccount": {
        "SchemeName": _consent_initiation_value(
            "pis-v4-international-scheduled-payment-consent-create-request", "CreditorAccount.SchemeName"
        ),
        "Identification": _consent_initiation_value(
            "pis-v4-international-scheduled-payment-consent-create-request", "CreditorAccount.Identification"
        ),
        "Name": _consent_initiation_value(
            "pis-v4-international-scheduled-payment-consent-create-request", "CreditorAccount.Name"
        ),
    },
}
"""International scheduled-payment submission initiation copied from the consent response."""


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
        "OB-301-DOP-1015003",
        "OB-301-DOP-101700",
        "OB-301-DOP-101900",
        "OB-301-DOP-102100",
        "OB-301-DOP-102200",
        "OB-301-DOP-102300",
        "OB-313-DOP-100100",
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

_PIS_V40_SCHEMA_CHECK_SCRIPT_IDS = frozenset(
    {
        "OB-400-DOP-100100",
        "OB-400-DOP-100300",
        "OB-316-DOP-100310",
        "OB-400-DOP-100400",
        "OB-400-DOP-100500",
        "OB-400-DOP-100600",
        "OB-400-DOP-100700",
        "OB-400-DOP-100800",
        "OB-400-DOP-100810",
        "OB-400-DOP-100820",
        "OB-400-DOP-100900",
        "OB-400-DOP-101000",
        "OB-400-DOP-101100",
        "OB-400-DOP-101101",
        "OB-400-DOP-101200",
        "OB-400-DOP-101300",
        "OB-400-DOP-101400",
        "OB-400-DOP-101401",
        "OB-400-DOP-101500",
        "OB-400-DOP-101503",
        "OB-400-DOP-101600",
        "OB-400-DOP-101700",
        "OB-400-DOP-101800",
        "OB-400-DOP-101900",
        "OB-400-DOP-102000",
        "OB-400-DOP-102100",
        "OB-400-DOP-102200",
        "OB-400-DOP-102300",
    }
)
"""Legacy v4 PIS scripts that enabled response schema checks."""

_PIS_V40_RESPONSE_SCHEMA_REFS = {
    ("POST", "/open-banking/v4.0/pisp/domestic-payment-consents", 201): (
        "#/components/schemas/OBWriteDomesticConsentResponse5"
    ),
    ("POST", "/open-banking/v4.0/pisp/domestic-payment-consents", 400): ("#/components/schemas/OBErrorResponse1"),
    ("GET", "/open-banking/v4.0/pisp/domestic-payment-consents/{domesticPaymentConsentId}", 200): (
        "#/components/schemas/OBWriteDomesticConsentResponse5"
    ),
    (
        "GET",
        "/open-banking/v4.0/pisp/domestic-payment-consents/{domesticPaymentConsentId}/funds-confirmation",
        200,
    ): "#/components/schemas/OBWriteFundsConfirmationResponse1",
    ("POST", "/open-banking/v4.0/pisp/domestic-payments", 201): ("#/components/schemas/OBWriteDomesticResponse5"),
    ("GET", "/open-banking/v4.0/pisp/domestic-payments/{domesticPaymentId}", 200): (
        "#/components/schemas/OBWriteDomesticResponse5"
    ),
    ("POST", "/open-banking/v4.0/pisp/domestic-scheduled-payment-consents", 201): (
        "#/components/schemas/OBWriteDomesticScheduledConsentResponse5"
    ),
    (
        "GET",
        "/open-banking/v4.0/pisp/domestic-scheduled-payment-consents/{domesticScheduledPaymentConsentId}",
        200,
    ): "#/components/schemas/OBWriteDomesticScheduledConsentResponse5",
    ("POST", "/open-banking/v4.0/pisp/domestic-scheduled-payments", 201): (
        "#/components/schemas/OBWriteDomesticScheduledResponse5"
    ),
    ("GET", "/open-banking/v4.0/pisp/domestic-scheduled-payments/{domesticScheduledPaymentId}", 200): (
        "#/components/schemas/OBWriteDomesticScheduledResponse5"
    ),
    ("POST", "/open-banking/v4.0/pisp/domestic-standing-order-consents", 201): (
        "#/components/schemas/OBWriteDomesticStandingOrderConsentResponse6"
    ),
    ("POST", "/open-banking/v4.0/pisp/domestic-standing-order-consents", 400): (
        "#/components/schemas/OBErrorResponse1"
    ),
    (
        "GET",
        "/open-banking/v4.0/pisp/domestic-standing-order-consents/{domesticStandingOrderConsentId}",
        200,
    ): "#/components/schemas/OBWriteDomesticStandingOrderConsentResponse6",
    ("POST", "/open-banking/v4.0/pisp/domestic-standing-orders", 201): (
        "#/components/schemas/OBWriteDomesticStandingOrderResponse6"
    ),
    ("POST", "/open-banking/v4.0/pisp/domestic-standing-orders", 400): "#/components/schemas/OBErrorResponse1",
    ("GET", "/open-banking/v4.0/pisp/domestic-standing-orders/{domesticStandingOrderId}", 200): (
        "#/components/schemas/OBWriteDomesticStandingOrderResponse6"
    ),
    ("POST", "/open-banking/v4.0/pisp/international-payment-consents", 201): (
        "#/components/schemas/OBWriteInternationalConsentResponse6"
    ),
    ("GET", "/open-banking/v4.0/pisp/international-payment-consents/{internationalPaymentConsentId}", 200): (
        "#/components/schemas/OBWriteInternationalConsentResponse6"
    ),
    ("POST", "/open-banking/v4.0/pisp/international-payments", 201): (
        "#/components/schemas/OBWriteInternationalResponse5"
    ),
    ("GET", "/open-banking/v4.0/pisp/international-payments/{internationalPaymentId}", 200): (
        "#/components/schemas/OBWriteInternationalResponse5"
    ),
    ("POST", "/open-banking/v4.0/pisp/international-scheduled-payment-consents", 201): (
        "#/components/schemas/OBWriteInternationalScheduledConsentResponse6"
    ),
    (
        "GET",
        "/open-banking/v4.0/pisp/international-scheduled-payment-consents/{internationalScheduledPaymentConsentId}",
        200,
    ): "#/components/schemas/OBWriteInternationalScheduledConsentResponse6",
    ("POST", "/open-banking/v4.0/pisp/international-scheduled-payments", 201): (
        "#/components/schemas/OBWriteInternationalScheduledResponse6"
    ),
    (
        "GET",
        "/open-banking/v4.0/pisp/international-scheduled-payments/{internationalScheduledPaymentId}",
        200,
    ): "#/components/schemas/OBWriteInternationalScheduledResponse6",
}
"""Bundled v4 Payment Initiation response schemas keyed by operation and status."""


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


def _schema_assertion(
    *,
    assertion_id: str,
    description: str,
    legacy_assertion_ids: tuple[str, ...],
    schema_ref: str,
) -> CatalogueAssertion:
    """Create a response-schema assertion traced to legacy schema-check cases.

    Args:
        assertion_id: Stable assertion id unique within a test case.
        description: Human-readable assertion description.
        legacy_assertion_ids: Legacy pseudo-assertion ids represented by this assertion.
        schema_ref: JSON pointer to the bundled Payment Initiation OpenAPI response schema.

    Returns:
        Catalogue assertion with response-schema provenance metadata.
    """
    return CatalogueAssertion(
        assertion_id=assertion_id,
        kind="response_schema",
        description=description,
        rule={
            "source": "bundled_openapi",
            "document": "ob-read-write-v4.0-payment-initiation-openapi",
            "schemaRef": schema_ref,
            "legacyAssertionIds": list(legacy_assertion_ids),
        },
    )


def _case_schema_assertions(
    *,
    method: HttpMethod,
    path: str,
    scripts_40: tuple[str, ...],
    assertions: tuple[CatalogueAssertion, ...],
) -> tuple[CatalogueAssertion, ...]:
    """Build schema assertions needed for legacy v4 PIS parity.

    Args:
        method: HTTP method for the executable request.
        path: Endpoint path for the executable request.
        scripts_40: Legacy v4 script ids represented by the case.
        assertions: Existing assertions used to infer the response status.

    Returns:
        Response-schema assertion tuple, or an empty tuple when no legacy v4
        schema check applies to the executable request.
    """
    if not _PIS_V40_SCHEMA_CHECK_SCRIPT_IDS.intersection(scripts_40):
        return ()
    expected_statuses = _expected_statuses_from_assertions(assertions)
    schema_assertions: list[CatalogueAssertion] = []
    for expected_status in expected_statuses:
        schema_ref = _PIS_V40_RESPONSE_SCHEMA_REFS.get((method, path, expected_status))
        if schema_ref is None:
            continue
        schema_assertions.append(
            _schema_assertion(
                assertion_id=f"response-schema-{expected_status}",
                description=f"Response body satisfies the legacy v4 PIS {expected_status} schema check.",
                legacy_assertion_ids=("legacy-schema-check",),
                schema_ref=schema_ref,
            )
        )
    return tuple(schema_assertions)


def _legacy_assertions_missing_from_case(
    *,
    legacy_assertion_ids: tuple[str, ...],
    assertions: tuple[CatalogueAssertion, ...],
) -> tuple[CatalogueAssertion, ...]:
    """Build executable assertions for legacy PIS assertions not hand-coded.

    Args:
        legacy_assertion_ids: Legacy assertion ids represented by the case.
        assertions: Existing catalogue assertions for the case.

    Returns:
        Additional catalogue assertions for supported legacy checks.
    """
    additional: list[CatalogueAssertion] = []
    if "OB3GLOFAPIHeader" in legacy_assertion_ids and not _has_assertion(assertions, "header", "x-fapi-interaction-id"):
        additional.append(
            _header_assertion(
                assertion_id="legacy-fapi-interaction-header",
                description="Response includes x-fapi-interaction-id.",
                header_name="x-fapi-interaction-id",
                legacy_assertion_ids=("OB3GLOFAPIHeader",),
            )
        )
    if "OB3GLOAAssertConsentId" in legacy_assertion_ids and not _has_json_path(assertions, "Data.ConsentId"):
        additional.append(
            _json_field_assertion(
                assertion_id="legacy-consent-id-present",
                description="Response includes a consent identifier.",
                field_path="Data.ConsentId",
                expected_value="present",
                legacy_assertion_ids=("OB3GLOAAssertConsentId",),
            )
        )
    if "OB3DOPAssertAwaitingAuthorisationV4" in legacy_assertion_ids and not _has_json_path(assertions, "Data.Status"):
        additional.append(
            _json_field_assertion(
                assertion_id="legacy-status-awaiting-authorisation",
                description="Response status is awaiting authorisation.",
                field_path="Data.Status",
                expected_value="AWAU",
                legacy_assertion_ids=("OB3DOPAssertAwaitingAuthorisationV4",),
            )
        )
    if "OB3DOPAssertAuthorisedV4" in legacy_assertion_ids and not _has_json_path(assertions, "Data.Status"):
        additional.append(
            _json_field_assertion(
                assertion_id="legacy-status-authorised",
                description="Response status is authorised.",
                field_path="Data.Status",
                expected_value="AUTH",
                legacy_assertion_ids=("OB3DOPAssertAuthorisedV4",),
            )
        )
    return tuple(additional)


def _has_assertion(assertions: tuple[CatalogueAssertion, ...], kind: str, marker: str) -> bool:
    """Return whether assertions already contain a kind/marker combination.

    Args:
        assertions: Catalogue assertions to inspect.
        kind: Assertion kind to match.
        marker: Header name or rule value to find.

    Returns:
        True when a matching assertion already exists.
    """
    normalized = marker.lower()
    return any(
        assertion.kind == kind
        and normalized
        in str(assertion.rule.get("header", assertion.rule.get("name", assertion.rule.get("path", "")))).lower()
        for assertion in assertions
    )


def _has_json_path(assertions: tuple[CatalogueAssertion, ...], path: str) -> bool:
    """Return whether assertions already include a JSON path check.

    Args:
        assertions: Catalogue assertions to inspect.
        path: JSON path to match.

    Returns:
        True when a JSON-field assertion already targets ``path``.
    """
    return any(assertion.kind == "json_field" and assertion.rule.get("path") == path for assertion in assertions)


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


def _specification_versions_for_scripts(scripts_31: tuple[str, ...], scripts_40: tuple[str, ...]) -> tuple[str, ...]:
    """Return user-facing versions that can execute the represented scripts.

    Args:
        scripts_31: Legacy v3.1 script ids represented by the case.
        scripts_40: Legacy v4 script ids represented by the case.

    Returns:
        Specification-version applicability for cases that are only valid for a
        single legacy major version, otherwise an empty tuple for shared cases.
    """
    if scripts_40 and not scripts_31:
        return _PIS_V40_SPECIFICATION_VERSIONS
    if scripts_31 and not scripts_40:
        return _PIS_V31_SPECIFICATION_VERSIONS
    return ()


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
    body_template: JsonValue | None = None,
    generated_values: dict[str, GeneratedRuntimeValue] | None = None,
    detached_jws_omit_claims: tuple[str, ...] = (),
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
        body_template: Optional JSON body template for write requests.
        generated_values: Optional per-step generated values referenced by the
            body template.
        detached_jws_omit_claims: Open Banking detached-JWS protected-header
            aliases to omit when compiling this request.

    Returns:
        Fully-defined catalogue test case with endpoint/profile applicability.
    """
    request_path = _path_with_captured_pis_values(path)
    runtime_input_refs = tuple(requirement.input_id for requirement in runtime_inputs if requirement.source == "plan")
    required_token_id = _pis_required_token_id(test_case_id=test_case_id, runtime_inputs=runtime_inputs)
    legacy_assertions = _legacy_assertions_missing_from_case(
        legacy_assertion_ids=legacy_assertion_ids,
        assertions=assertions,
    )
    executable_assertions = (
        *assertions,
        *legacy_assertions,
        *_case_schema_assertions(method=method, path=path, scripts_40=scripts_40, assertions=assertions),
    )
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
            specification_versions=_specification_versions_for_scripts(scripts_31, scripts_40),
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
                body_template=body_template,
                generated_values=generated_values or {},
                required_token_id=required_token_id,
                detached_jws_omit_claims=detached_jws_omit_claims,
            ),
        ),
        assertions=executable_assertions,
        response_signature_required=bool(_RESPONSE_SIGNATURE_SCRIPT_IDS.intersection((*scripts_31, *scripts_40))),
    )


def _pis_required_token_id(
    *,
    test_case_id: str,
    runtime_inputs: tuple[RuntimeInputRequirement, ...],
) -> str | None:
    """Return the semantic bearer token required by a PIS test case.

    Args:
        test_case_id: Stable PIS catalogue test-case id.
        runtime_inputs: Runtime inputs required by the catalogue test case.

    Returns:
        The per-flow PSU-authorised token id for downstream requests, the
        client-credentials token id for consent creation, or ``None`` when the
        request does not require bearer-token authorisation.
    """
    if _ACCESS_TOKEN_REF not in runtime_inputs:
        return None
    return _PIS_PSU_AUTH_TOKEN_IDS_BY_CASE_ID.get(test_case_id, _PIS_RESOURCE_AUTH_ID)


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
            runtime_inputs=_PIS_DOMESTIC_PAYMENT_INPUTS,
            body_template={"Data": {"Initiation": _PIS_DOMESTIC_PAYMENT_INITIATION_TEMPLATE}, "Risk": {}},
            generated_values=_PIS_GENERATED_INSTRUCTION_IDS,
            scripts_31=("OB-301-DOP-100100", "OB-301-DOP-100300"),
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
                    description="Consent status is awaiting authorisation.",
                    field_path="Data.Status",
                    expected_value="AWAU",
                    legacy_assertion_ids=("OB3DOPAssertAwaitingAuthorisation", "OB3DOPAssertAwaitingAuthorisationV4"),
                ),
            ),
        ),
        _build_case(
            test_case_id="pis-v4-domestic-payment-consent-create-without-financial-id",
            name="Create domestic payment consent without x-fapi-financial-id",
            role="consent",
            method="POST",
            path="/open-banking/v4.0/pisp/domestic-payment-consents",
            runtime_inputs=_PIS_DOMESTIC_PAYMENT_INPUTS,
            body_template={"Data": {"Initiation": _PIS_DOMESTIC_PAYMENT_INITIATION_TEMPLATE}, "Risk": {}},
            generated_values=_PIS_GENERATED_INSTRUCTION_IDS,
            scripts_31=("OB-313-DOP-100100",),
            scripts_40=(),
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
                    description="Consent creation without x-fapi-financial-id returns HTTP 201.",
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
                    description="Consent status is awaiting authorisation.",
                    field_path="Data.Status",
                    expected_value="AWAU",
                    legacy_assertion_ids=("OB3DOPAssertAwaitingAuthorisation", "OB3DOPAssertAwaitingAuthorisationV4"),
                ),
            ),
        ),
        _build_case(
            test_case_id="pis-v4-domestic-payment-consent-reject-missing-signature-claim",
            name="Reject domestic payment consent with missing detached-JWS iss claim",
            role="security",
            method="POST",
            path="/open-banking/v4.0/pisp/domestic-payment-consents",
            runtime_inputs=_PIS_DOMESTIC_PAYMENT_INPUTS,
            body_template={"Data": {"Initiation": _PIS_DOMESTIC_PAYMENT_INITIATION_TEMPLATE}, "Risk": {}},
            generated_values=_PIS_GENERATED_INSTRUCTION_IDS,
            scripts_31=("OB-301-DOP-100110",),
            scripts_40=("OB-400-DOP-100110",),
            legacy_assertion_ids=(
                "OB3GLOAssertOn400",
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
                    description="Detached JWS missing the Open Banking iss claim returns HTTP 400.",
                    expected_status=400,
                    legacy_assertion_ids=("OB3GLOAssertOn400",),
                ),
            ),
            required_capability_ids=("pis.domestic-payment-consent.reject-invalid-detached-jws",),
            mandatory=False,
            detached_jws_omit_claims=("iss",),
        ),
        _build_case(
            test_case_id="pis-v4-domestic-payment-consent-reject-invalid-signature",
            name="Reject domestic payment consent with missing detached JWS",
            role="security",
            method="POST",
            path="/open-banking/v4.0/pisp/domestic-payment-consents",
            runtime_inputs=_PIS_DOMESTIC_PAYMENT_INPUTS,
            body_template={"Data": {"Initiation": _PIS_DOMESTIC_PAYMENT_INITIATION_TEMPLATE}, "Risk": {}},
            generated_values=_PIS_GENERATED_INSTRUCTION_IDS,
            scripts_31=("OB-316-DOP-100310",),
            scripts_40=("OB-316-DOP-100310",),
            legacy_assertion_ids=(
                "OB3GLOAssertOn400",
                "OB3DOPAssertSignatureMissingOBErrorCode",
                "OB3DOPAssertSignatureMissingOBErrorCodeV4",
            ),
            assertions=(
                _status_assertion(
                    assertion_id="status-400",
                    description="Missing signature returns HTTP 400.",
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
                    description="Consent status is authorised.",
                    field_path="Data.Status",
                    expected_value="AUTH",
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
                *_PIS_DOMESTIC_PAYMENT_INPUTS,
                _DOMESTIC_PAYMENT_CONSENT_ID,
            ),
            body_template={
                "Data": {
                    "ConsentId": _PIS_CAPTURED_PATH_VALUES["{domesticPaymentConsentId}"],
                    "Initiation": _PIS_DOMESTIC_PAYMENT_CONSENT_INITIATION_TEMPLATE,
                },
                "Risk": {},
            },
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
            runtime_inputs=_PIS_DOMESTIC_SCHEDULED_PAYMENT_INPUTS,
            body_template={
                "Data": {
                    "Permission": "Create",
                    "Initiation": _PIS_DOMESTIC_SCHEDULED_PAYMENT_INITIATION_TEMPLATE,
                },
                "Risk": {},
            },
            generated_values=_PIS_GENERATED_INSTRUCTION_IDS,
            scripts_31=("OB-301-DOP-100800",),
            scripts_40=("OB-400-DOP-100800", "OB-400-DOP-101000"),
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
            test_case_id="pis-v4-domestic-scheduled-payment-consent-create-with-offset-datetime",
            name="Create domestic scheduled payment consent with offset datetime",
            role="consent",
            method="POST",
            path="/open-banking/v4.0/pisp/domestic-scheduled-payment-consents",
            runtime_inputs=_PIS_DOMESTIC_PAYMENT_INPUTS,
            body_template={
                "Data": {
                    "Permission": "Create",
                    "Initiation": _domestic_scheduled_payment_initiation_template(
                        "${generated.requestedExecutionDateTime}"
                    ),
                },
                "Risk": {},
            },
            generated_values=_PIS_GENERATED_OFFSET_DATETIME,
            scripts_31=("OB-301-DOP-100810",),
            scripts_40=("OB-400-DOP-100810",),
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
                    description="Scheduled-consent creation with offset datetime returns HTTP 201.",
                    expected_status=201,
                    legacy_assertion_ids=("OB3GLOAssertOn201",),
                ),
                _header_assertion(
                    assertion_id="fapi-interaction-header",
                    description="Response includes x-fapi-interaction-id.",
                    header_name="x-fapi-interaction-id",
                    legacy_assertion_ids=("OB3GLOFAPIHeader",),
                ),
            ),
        ),
        _build_case(
            test_case_id="pis-v4-domestic-scheduled-payment-consent-create-with-utc-datetime",
            name="Create domestic scheduled payment consent with UTC datetime",
            role="consent",
            method="POST",
            path="/open-banking/v4.0/pisp/domestic-scheduled-payment-consents",
            runtime_inputs=_PIS_DOMESTIC_PAYMENT_INPUTS,
            body_template={
                "Data": {
                    "Permission": "Create",
                    "Initiation": _domestic_scheduled_payment_initiation_template(
                        "${generated.requestedExecutionDateTime}"
                    ),
                },
                "Risk": {},
            },
            generated_values=_PIS_GENERATED_UTC_DATETIME,
            scripts_31=("OB-301-DOP-100820",),
            scripts_40=("OB-400-DOP-100820",),
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
                    description="Scheduled-consent creation with UTC datetime returns HTTP 201.",
                    expected_status=201,
                    legacy_assertion_ids=("OB3GLOAssertOn201",),
                ),
                _header_assertion(
                    assertion_id="fapi-interaction-header",
                    description="Response includes x-fapi-interaction-id.",
                    header_name="x-fapi-interaction-id",
                    legacy_assertion_ids=("OB3GLOFAPIHeader",),
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
                *_PIS_DOMESTIC_SCHEDULED_PAYMENT_INPUTS,
                _DOMESTIC_SCHEDULED_PAYMENT_CONSENT_ID,
            ),
            body_template={
                "Data": {
                    "ConsentId": _PIS_CAPTURED_PATH_VALUES["{domesticScheduledPaymentConsentId}"],
                    "Initiation": _PIS_DOMESTIC_SCHEDULED_PAYMENT_CONSENT_INITIATION_TEMPLATE,
                },
                "Risk": {},
            },
            scripts_31=("OB-301-DOP-101000", "OB-301-DOP-101101"),
            scripts_40=("OB-400-DOP-101101",),
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
            runtime_inputs=_PIS_DOMESTIC_STANDING_ORDER_INPUTS,
            body_template={
                "Data": {
                    "Permission": "Create",
                    "Initiation": _PIS_DOMESTIC_STANDING_ORDER_INITIATION_TEMPLATE,
                },
                "Risk": {},
            },
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
                *_PIS_DOMESTIC_STANDING_ORDER_INPUTS,
                _DOMESTIC_STANDING_ORDER_CONSENT_ID,
            ),
            body_template={
                "Data": {
                    "ConsentId": _PIS_CAPTURED_PATH_VALUES["{domesticStandingOrderConsentId}"],
                    "Initiation": _PIS_DOMESTIC_STANDING_ORDER_INITIATION_TEMPLATE,
                },
                "Risk": {},
            },
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
            test_case_id="pis-v4-domestic-standing-order-read-with-number-and-final-date",
            name="Read domestic standing order created with number-of-payments and final-payment date",
            role="resource",
            method="GET",
            path="/open-banking/v4.0/pisp/domestic-standing-orders/{domesticStandingOrderId}",
            runtime_inputs=(_RESOURCE_BASE_URL, _ACCESS_TOKEN_REF, _DOMESTIC_STANDING_ORDER_ID),
            scripts_31=("OB-301-DOP-1015001",),
            scripts_40=(),
            legacy_assertion_ids=("legacy-schema-check",),
            assertions=(
                _schema_assertion(
                    assertion_id="response-schema",
                    description="Standing-order variant retrieval passes the legacy response schema check.",
                    legacy_assertion_ids=("legacy-schema-check",),
                    schema_ref="#/components/schemas/OBWriteDomesticStandingOrderResponse6",
                ),
            ),
            dependencies=("pis-v4-domestic-standing-order-create",),
        ),
        _build_case(
            test_case_id="pis-v4-domestic-standing-order-read-with-final-amount-only",
            name="Read domestic standing order created with final-payment amount only",
            role="resource",
            method="GET",
            path="/open-banking/v4.0/pisp/domestic-standing-orders/{domesticStandingOrderId}",
            runtime_inputs=(_RESOURCE_BASE_URL, _ACCESS_TOKEN_REF, _DOMESTIC_STANDING_ORDER_ID),
            scripts_31=("OB-301-DOP-1015002",),
            scripts_40=(),
            legacy_assertion_ids=("legacy-schema-check",),
            assertions=(
                _schema_assertion(
                    assertion_id="response-schema",
                    description="Standing-order final-amount-only retrieval passes the legacy response schema check.",
                    legacy_assertion_ids=("legacy-schema-check",),
                    schema_ref="#/components/schemas/OBWriteDomesticStandingOrderResponse6",
                ),
            ),
            dependencies=("pis-v4-domestic-standing-order-create",),
        ),
        _build_case(
            test_case_id="pis-v4-domestic-standing-order-consent-reject-invalid-frequency",
            name="Reject domestic standing-order consent with invalid frequency combination",
            role="consent",
            method="POST",
            path="/open-banking/v4.0/pisp/domestic-standing-order-consents",
            runtime_inputs=_PIS_DOMESTIC_STANDING_ORDER_INPUTS,
            body_template={
                "Data": {
                    "Permission": "Create",
                    "Initiation": _PIS_INVALID_FREQUENCY_STANDING_ORDER_INITIATION_TEMPLATE,
                },
                "Risk": {},
            },
            scripts_31=(),
            scripts_40=("OB-400-DOP-101503",),
            legacy_assertion_ids=("OB3GLOAssertOn400",),
            assertions=(
                _status_assertion(
                    assertion_id="status-400",
                    description="Invalid standing-order consent frequency input returns HTTP 400.",
                    expected_status=400,
                    legacy_assertion_ids=("OB3GLOAssertOn400",),
                ),
            ),
            mandatory=False,
        ),
        _build_case(
            test_case_id="pis-v4-domestic-standing-order-reject-invalid-frequency",
            name="Reject domestic standing order with invalid frequency combination",
            role="resource",
            method="POST",
            path="/open-banking/v4.0/pisp/domestic-standing-orders",
            runtime_inputs=(
                *_PIS_DOMESTIC_STANDING_ORDER_INPUTS,
                _DOMESTIC_STANDING_ORDER_CONSENT_ID,
            ),
            body_template={
                "Data": {
                    "ConsentId": _PIS_CAPTURED_PATH_VALUES["{domesticStandingOrderConsentId}"],
                    "Initiation": _PIS_INVALID_FREQUENCY_STANDING_ORDER_INITIATION_TEMPLATE,
                },
                "Risk": {},
            },
            scripts_31=("OB-301-DOP-101400", "OB-301-DOP-1015003"),
            scripts_40=("OB-400-DOP-101400",),
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
            runtime_inputs=_PIS_INTERNATIONAL_PAYMENT_INPUTS,
            body_template={"Data": {"Initiation": _PIS_INTERNATIONAL_PAYMENT_INITIATION_TEMPLATE}, "Risk": {}},
            generated_values=_PIS_GENERATED_INSTRUCTION_IDS,
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
                *_PIS_INTERNATIONAL_PAYMENT_INPUTS,
                _INTERNATIONAL_PAYMENT_CONSENT_ID,
            ),
            body_template={
                "Data": {
                    "ConsentId": _PIS_CAPTURED_PATH_VALUES["{internationalPaymentConsentId}"],
                    "Initiation": _PIS_INTERNATIONAL_PAYMENT_CONSENT_INITIATION_TEMPLATE,
                },
                "Risk": {},
            },
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
            runtime_inputs=_PIS_INTERNATIONAL_SCHEDULED_PAYMENT_INPUTS,
            body_template={
                "Data": {
                    "Permission": "Create",
                    "Initiation": _PIS_INTERNATIONAL_SCHEDULED_PAYMENT_INITIATION_TEMPLATE,
                },
                "Risk": {},
            },
            generated_values=_PIS_GENERATED_INSTRUCTION_IDS,
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
                *_PIS_INTERNATIONAL_SCHEDULED_PAYMENT_INPUTS,
                _INTERNATIONAL_SCHEDULED_PAYMENT_CONSENT_ID,
            ),
            body_template={
                "Data": {
                    "ConsentId": _PIS_CAPTURED_PATH_VALUES["{internationalScheduledPaymentConsentId}"],
                    "Initiation": _PIS_INTERNATIONAL_SCHEDULED_PAYMENT_CONSENT_INITIATION_TEMPLATE,
                },
                "Risk": {},
            },
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
