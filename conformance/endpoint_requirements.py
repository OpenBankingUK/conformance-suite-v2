"""Spec-derived Open Banking Read/Write endpoint implementation requirements.

These requirements are independent of mandatory tests for an implemented
endpoint. Evidence and unresolved source discrepancies are recorded in
``docs/READ_WRITE_ENDPOINT_REQUIREMENTS.md``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import cache
from typing import Literal

from conformance.catalogue import EndpointRef, HttpMethod

RequirementKind = Literal["M", "C", "O", "MP", "MD", "CN"]
"""Published endpoint-table classifications, preserving conditional mandatory rules."""

SOURCE_COMMIT = "0fbe63706f2d3c4b7cc4a5a789b9a2eb5764be7a"  # pragma: allowlist secret - public upstream git SHA
"""Reviewed upstream documentation revision."""

_VERSION_FOLDERS = {"4.0.1": "v4.0.1", "4.0.0": "v4.0", "3.1.11": "v3.1.11"}
_LABELS: dict[RequirementKind, str] = {
    "M": "Mandatory",
    "C": "Conditional",
    "O": "Optional",
    "MP": "Mandatory if POST implemented",
    "MD": "Mandatory if immediate debit supported",
    "CN": "Conditional (event-management rule)",
}
type _EndpointRow = tuple[HttpMethod, str, RequirementKind]

# Individual resource endpoint tables, not the occasionally conflicting summaries.
_RESOURCE_TABLES: dict[str, tuple[_EndpointRow, ...]] = {
    "aisp/account-access-consents.md": (
        ("POST", "/account-access-consents", "M"),
        ("GET", "/account-access-consents/{ConsentId}", "M"),
        ("DELETE", "/account-access-consents/{ConsentId}", "M"),
    ),
    "aisp/Accounts.md": (
        ("GET", "/accounts", "M"),
        ("GET", "/accounts/{AccountId}", "M"),
    ),
    "aisp/Balances.md": (
        ("GET", "/accounts/{AccountId}/balances", "M"),
        ("GET", "/balances", "O"),
    ),
    "aisp/Transactions.md": (
        ("GET", "/accounts/{AccountId}/transactions", "M"),
        ("GET", "/transactions", "O"),
    ),
    "aisp/Beneficiaries.md": (
        ("GET", "/accounts/{AccountId}/beneficiaries", "C"),
        ("GET", "/beneficiaries", "O"),
    ),
    "aisp/direct-debits.md": (
        ("GET", "/accounts/{AccountId}/direct-debits", "C"),
        ("GET", "/direct-debits", "O"),
    ),
    "aisp/standing-orders.md": (
        ("GET", "/accounts/{AccountId}/standing-orders", "C"),
        ("GET", "/standing-orders", "O"),
    ),
    "aisp/Products.md": (
        ("GET", "/accounts/{AccountId}/product", "C"),
        ("GET", "/products", "O"),
    ),
    "aisp/Offers.md": (
        ("GET", "/accounts/{AccountId}/offers", "C"),
        ("GET", "/offers", "O"),
    ),
    "aisp/Parties.md": (
        ("GET", "/accounts/{AccountId}/parties", "C"),
        ("GET", "/accounts/{AccountId}/party", "C"),
        ("GET", "/party", "C"),
    ),
    "aisp/scheduled-payments.md": (
        ("GET", "/accounts/{AccountId}/scheduled-payments", "C"),
        ("GET", "/scheduled-payments", "O"),
    ),
    "aisp/Statements.md": (
        ("GET", "/accounts/{AccountId}/statements", "C"),
        ("GET", "/accounts/{AccountId}/statements/{StatementId}", "C"),
        ("GET", "/accounts/{AccountId}/statements/{StatementId}/file", "O"),
        ("GET", "/accounts/{AccountId}/statements/{StatementId}/transactions", "C"),
        ("GET", "/statements", "O"),
    ),
    "pisp/domestic-payment-consents.md": (
        ("POST", "/domestic-payment-consents", "M"),
        ("GET", "/domestic-payment-consents/{ConsentId}", "M"),
        ("GET", "/domestic-payment-consents/{ConsentId}/funds-confirmation", "M"),
    ),
    "pisp/domestic-payments.md": (
        ("POST", "/domestic-payments", "M"),
        ("GET", "/domestic-payments/{DomesticPaymentId}", "M"),
        ("GET", "/domestic-payments/{DomesticPaymentId}/payment-details", "O"),
    ),
    "pisp/domestic-scheduled-payment-consents.md": (
        ("POST", "/domestic-scheduled-payment-consents", "C"),
        ("GET", "/domestic-scheduled-payment-consents/{ConsentId}", "MP"),
    ),
    "pisp/domestic-scheduled-payments.md": (
        ("POST", "/domestic-scheduled-payments", "C"),
        ("GET", "/domestic-scheduled-payments/{DomesticScheduledPaymentId}", "MP"),
        ("GET", "/domestic-scheduled-payments/{DomesticScheduledPaymentId}/payment-details", "O"),
    ),
    "pisp/domestic-standing-order-consents.md": (
        ("POST", "/domestic-standing-order-consents", "C"),
        ("GET", "/domestic-standing-order-consents/{ConsentId}", "MP"),
    ),
    "pisp/domestic-standing-orders.md": (
        ("POST", "/domestic-standing-orders", "C"),
        ("GET", "/domestic-standing-orders/{DomesticStandingOrderId}", "MP"),
        ("GET", "/domestic-standing-orders/{DomesticStandingOrderId}/payment-details", "O"),
    ),
    "pisp/international-payment-consents.md": (
        ("POST", "/international-payment-consents", "C"),
        ("GET", "/international-payment-consents/{ConsentId}", "MP"),
        ("GET", "/international-payment-consents/{ConsentId}/funds-confirmation", "MP"),
    ),
    "pisp/international-payments.md": (
        ("POST", "/international-payments", "C"),
        ("GET", "/international-payments/{InternationalPaymentId}", "MP"),
        ("GET", "/international-payments/{InternationalPaymentId}/payment-details", "O"),
    ),
    "pisp/international-scheduled-payment-consents.md": (
        ("POST", "/international-scheduled-payment-consents", "C"),
        ("GET", "/international-scheduled-payment-consents/{ConsentId}", "MP"),
        ("GET", "/international-scheduled-payment-consents/{ConsentId}/funds-confirmation", "MD"),
    ),
    "pisp/international-scheduled-payments.md": (
        ("POST", "/international-scheduled-payments", "C"),
        ("GET", "/international-scheduled-payments/{InternationalScheduledPaymentId}", "MP"),
        ("GET", "/international-scheduled-payments/{InternationalScheduledPaymentId}/payment-details", "O"),
    ),
    "pisp/international-standing-order-consents.md": (
        ("POST", "/international-standing-order-consents", "C"),
        ("GET", "/international-standing-order-consents/{ConsentId}", "MP"),
    ),
    "pisp/international-standing-orders.md": (
        ("POST", "/international-standing-orders", "C"),
        ("GET", "/international-standing-orders/{InternationalStandingOrderPaymentId}", "MP"),
        ("GET", "/international-standing-orders/{InternationalStandingOrderPaymentId}/payment-details", "O"),
    ),
    "pisp/file-payment-consents.md": (
        ("POST", "/file-payment-consents", "C"),
        ("POST", "/file-payment-consents/{ConsentId}/file", "C"),
        ("GET", "/file-payment-consents/{ConsentId}", "MP"),
        ("GET", "/file-payment-consents/{ConsentId}/file", "C"),
    ),
    "pisp/file-payments.md": (
        ("POST", "/file-payments", "C"),
        ("GET", "/file-payments/{FilePaymentId}", "MP"),
        ("GET", "/file-payments/{FilePaymentId}/report-file", "C"),
        ("GET", "/file-payments/{FilePaymentId}/payment-details", "O"),
    ),
    "cbpii/funds-confirmation-consent.md": (
        ("POST", "/funds-confirmation-consents", "M"),
        ("GET", "/funds-confirmation-consents/{ConsentId}", "M"),
        ("DELETE", "/funds-confirmation-consents/{ConsentId}", "M"),
    ),
    "cbpii/funds-confirmation.md": (("POST", "/funds-confirmations", "M"),),
    "vrp/domestic-vrp-consents.md": (
        ("POST", "/domestic-vrp-consents", "M"),
        ("GET", "/domestic-vrp-consents/{ConsentId}", "M"),
        ("DELETE", "/domestic-vrp-consents/{ConsentId}", "M"),
        ("POST", "/domestic-vrp-consents/{ConsentId}/funds-confirmation", "M"),
        ("PUT", "/domestic-vrp-consents/{ConsentId}", "O"),
        ("PATCH", "/domestic-vrp-consents/{ConsentId}", "O"),
    ),
    "vrp/domestic-vrps.md": (
        ("POST", "/domestic-vrps", "C"),
        ("GET", "/domestic-vrps/{DomesticVRPId}", "C"),
        ("GET", "/domestic-vrps/{DomesticVRPId}/payment-details", "O"),
    ),
    "event-notifications/event-subscription.md": (
        ("POST", "/event-subscriptions", "O"),
        ("GET", "/event-subscriptions", "MP"),
        ("PUT", "/event-subscriptions/{EventSubscriptionId}", "CN"),
        ("DELETE", "/event-subscriptions/{EventSubscriptionId}", "CN"),
    ),
    "event-notifications/callback-url.md": (
        ("POST", "/callback-urls", "O"),
        ("GET", "/callback-urls", "MP"),
        ("PUT", "/callback-urls/{CallbackUrlId}", "CN"),
        ("DELETE", "/callback-urls/{CallbackUrlId}", "CN"),
    ),
    "event-notifications/event-notifications.md": (("POST", "/event-notifications", "O"),),
    "event-notifications/events.md": (("POST", "/events", "O"),),
}

_DISPUTED_ENDPOINTS = frozenset(
    {
        ("GET", "/file-payment-consents/{ConsentId}"),
        ("POST", "/file-payment-consents/{ConsentId}/file"),
        ("GET", "/international-scheduled-payment-consents/{ConsentId}/funds-confirmation"),
        ("PUT", "/callback-urls/{CallbackUrlId}"),
        ("DELETE", "/callback-urls/{CallbackUrlId}"),
        ("PUT", "/event-subscriptions/{EventSubscriptionId}"),
        ("DELETE", "/event-subscriptions/{EventSubscriptionId}"),
        ("PUT", "/domestic-vrp-consents/{ConsentId}"),
        ("PATCH", "/domestic-vrp-consents/{ConsentId}"),
        ("POST", "/events"),
        ("GET", "/accounts/{AccountId}/direct-debits"),
    }
)


@dataclass(frozen=True)
class EndpointRequirement:
    """Versioned implementation requirement with traceable specification evidence."""

    endpoint: EndpointRef
    kind: RequirementKind
    source_url: str
    disputed: bool = False
    host: Literal["ASPSP", "TPP"] = "ASPSP"

    @property
    def label(self) -> str:
        """Return the participant-facing specification classification."""
        return _LABELS[self.kind]

    @property
    def prerequisite(self) -> EndpointRef | None:
        """Return the exact POST dependency, only for the published MP rule."""
        if self.kind != "MP":
            return None
        resource_path = "/" + self.endpoint.path.split("/")[1]
        return EndpointRef(method="POST", path=resource_path)

    @property
    def condition(self) -> str:
        """Explain conditions without treating regulatory applicability as a toggle."""
        match self.kind:
            case "C":
                return "Required when applicable to the ASPSP's regulatory obligations or online-channel offering."
            case "MP":
                return f"Required when POST /{self.endpoint.path.split('/')[1]} is implemented."
            case "MD":
                return "Required when international scheduled payments support immediate debit."
            case "CN":
                return (
                    "Optional for aggregated polling only with a single event type; otherwise required "
                    "when the corresponding resource POST is implemented."
                )
            case _:
                return ""


@cache
def read_write_endpoint_requirements(version: str) -> tuple[EndpointRequirement, ...]:
    """Return reviewed requirements for one exact supported Read/Write version.

    Raises:
        ValueError: If the documentation version has not been reviewed.
    """
    if version not in _VERSION_FOLDERS:
        raise ValueError(f"No reviewed endpoint requirements for Read/Write {version}")
    folder = _VERSION_FOLDERS[version]
    requirements: list[EndpointRequirement] = []
    for page, rows in _RESOURCE_TABLES.items():
        for method, path, kind in rows:
            if version == "3.1.11" and path == "/domestic-vrp-consents/{ConsentId}" and method in {"PUT", "PATCH"}:
                continue
            requirements.append(
                EndpointRequirement(
                    endpoint=EndpointRef(method=method, path=path),
                    kind=kind,
                    source_url=(
                        "https://github.com/OpenBankingUK/read-write-api-docs-pub/blob/"
                        f"{SOURCE_COMMIT}/docs/{folder}/resources-and-data-models/{page}#endpoints"
                    ),
                    disputed=(method, path) in _DISPUTED_ENDPOINTS,
                    host="TPP" if path == "/event-notifications" else "ASPSP",
                )
            )
    return tuple(requirements)


def endpoint_requirement_key(endpoint: EndpointRef) -> tuple[HttpMethod, str]:
    """Match catalogue path-parameter aliases without changing static path casing."""
    path = re.sub(r"^/open-banking/v(?:3\.1|4\.0)/(?:aisp|pisp|cbpii|vrp)/", "/", endpoint.path)
    return endpoint.method, re.sub(r"\{[^{}]+\}", "{}", path)


def read_write_endpoint_requirement(version: str, endpoint: EndpointRef) -> EndpointRequirement | None:
    """Look up a reviewed endpoint, explicitly returning None for test-only paths.

    Unknown paths are not guessed to be Optional or Mandatory from test coverage.
    Unsupported documentation versions raise ValueError.
    """
    key = endpoint_requirement_key(endpoint)
    return next(
        (
            requirement
            for requirement in read_write_endpoint_requirements(version)
            if endpoint_requirement_key(requirement.endpoint) == key
        ),
        None,
    )
