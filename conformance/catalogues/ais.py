"""AIS catalogue entries derived from legacy FCS accounts-and-transactions coverage."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
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

AIS_ACCOUNTS_TRANSACTIONS_CATALOGUE_KEY = CatalogueKey(standard="open-banking", version="v4.0", api="ais")
"""Catalogue boundary for AIS accounts-and-transactions legacy FCS import."""

AIS_ACCOUNTS_TRANSACTIONS_CATALOGUE_VERSION = "2026.07.legacy-fcs-ais-at.1"
"""Content version for the imported AIS accounts-and-transactions catalogue."""

_AIS_BASE_PATH = "/open-banking/v4.0/aisp"
_FAPI1_ADVANCED_ONLY = SecurityProfileApplicability(profiles=("fapi1-advanced",))

_AIS_V31_SPECIFICATION_VERSIONS = ("3.1", "3.1.11")
"""User-facing Read/Write versions that can execute legacy v3.1-only AIS cases."""

_AIS_V40_SPECIFICATION_VERSIONS = ("4.0", "4.0.0", "4.0.1")
"""User-facing Read/Write versions that can execute legacy v4 AIS cases."""

_ACCOUNTS_ENDPOINT = EndpointRef(method="GET", path=f"{_AIS_BASE_PATH}/accounts")
_ACCOUNT_BY_ID_ENDPOINT = EndpointRef(method="GET", path=f"{_AIS_BASE_PATH}/accounts/{{AccountId}}")
_ACCOUNT_BALANCES_ENDPOINT = EndpointRef(method="GET", path=f"{_AIS_BASE_PATH}/accounts/{{AccountId}}/balances")
_ACCOUNT_TRANSACTIONS_ENDPOINT = EndpointRef(method="GET", path=f"{_AIS_BASE_PATH}/accounts/{{AccountId}}/transactions")
_TRANSACTIONS_ENDPOINT = EndpointRef(method="GET", path=f"{_AIS_BASE_PATH}/transactions")

_RESOURCE_BASE_URL = RuntimeInputRequirement(
    input_id="resourceBaseUrl",
    input_type="url",
    label="AIS resource server base URL",
)
_ACCESS_TOKEN = RuntimeInputRequirement(
    input_id="accessToken",
    input_type="string",
    label="AIS access token",
    sensitive=True,
    source="token",
)
_CONSENTED_ACCOUNT_ID = RuntimeInputRequirement(
    input_id="consentedAccountId",
    input_type="string",
    label="Consented account identifier",
    source="plan",
)
_INVALID_ACCESS_TOKEN = RuntimeInputRequirement(
    input_id="invalidAccessToken",
    input_type="string",
    label="Invalid AIS access token for unauthorized checks",
    required=False,
    sensitive=True,
    source="generated",
)
_FROM_BOOKING_DATE_TIME = RuntimeInputRequirement(
    input_id="fromBookingDateTime",
    input_type="string",
    label="Optional transaction from-booking-date-time filter",
    required=False,
    source="plan",
)
_TO_BOOKING_DATE_TIME = RuntimeInputRequirement(
    input_id="toBookingDateTime",
    input_type="string",
    label="Optional transaction to-booking-date-time filter",
    required=False,
    source="plan",
)

_COMMON_RESOURCE_RUNTIME_REQUIREMENTS = (_RESOURCE_BASE_URL, _ACCESS_TOKEN)
_ACCOUNT_RESOURCE_RUNTIME_REQUIREMENTS = (_RESOURCE_BASE_URL, _ACCESS_TOKEN, _CONSENTED_ACCOUNT_ID)
type _AisPermissionProfile = Literal["basic", "detail"]
"""Legacy AIS permission profile labels used for consent/token binding."""

_AIS_BASIC_RESOURCE_AUTH_ID = "ais-account-access-basic"
"""Semantic authorization id for AIS resource requests using basic read permissions."""

_AIS_DETAIL_RESOURCE_AUTH_ID = "ais-account-access-detail"
"""Semantic authorization id for AIS resource requests using detail read permissions."""

_AIS_RESOURCE_AUTH_IDS: Mapping[_AisPermissionProfile, str] = {
    "basic": _AIS_BASIC_RESOURCE_AUTH_ID,
    "detail": _AIS_DETAIL_RESOURCE_AUTH_ID,
}
"""Semantic authorization ids keyed by legacy AIS permission profile."""

_AIS_CLIENT_CREDENTIALS_AUTH_ID = "ais-client-credentials"
"""Semantic authorization id for AIS client-credential setup requests."""

_AIS_DETAIL_PERMISSION_LEGACY_IDS = frozenset(
    {
        "OB-301-ACC-100200",
        "OB-301-ACC-100400",
        "OB-301-ACC-100700",
        "OB-301-ACC-100800",
        "OB-301-SCP-103500",
        "OB-301-SCP-103600",
        "OB-301-SCP-103700",
        "OB-301-SCP-103701",
        "OB-301-SCP-103703",
        "OB-301-SCP-103704",
        "OB-301-STO-103800",
        "OB-301-STO-103900",
        "OB-301-STO-103901",
        "OB-301-STO-104000",
        "OB-301-STO-104100",
        "OB-301-STO-104102",
        "OB-301-STO-104103",
        "OB-301-TRA-105100",
        "OB-301-TRA-105110",
        "OB-301-TRA-105120",
        "OB-301-TRA-105700",
        "OB-400-ACC-100200",
        "OB-400-ACC-100400",
        "OB-400-ACC-100700",
        "OB-400-ACC-100800",
        "OB-400-SCP-103500",
        "OB-400-SCP-103600",
        "OB-400-SCP-103700",
        "OB-400-SCP-103701",
        "OB-400-SCP-103703",
        "OB-400-SCP-103704",
        "OB-400-STO-103800",
        "OB-400-STO-103900",
        "OB-400-STO-103901",
        "OB-400-STO-104000",
        "OB-400-STO-104100",
        "OB-400-STO-104102",
        "OB-400-STO-104103",
        "OB-400-TRA-105100",
        "OB-400-TRA-105110",
        "OB-400-TRA-105120",
        "OB-400-TRA-105700",
    }
)
"""Legacy AIS script ids whose resource token must use the detail permission profile."""

type _LegacyExtraCaseData = tuple[str, str, HttpMethod, str, tuple[str, ...], tuple[str, ...], tuple[str, ...]]
"""Compact legacy AIS case data not covered by the original accounts/transactions slice."""

_AIS_LEGACY_EXTRA_CASE_DATA: tuple[_LegacyExtraCaseData, ...] = (
    (
        "ais-at-legacy-account-acc-100500",
        "Fails using incorrect permissions for a given Account.",
        "GET",
        "/open-banking/v4.0/aisp/accounts/{AccountId}",
        ("OB-301-ACC-100500",),
        (),
        ("OB3GLOAssertOn401", "OB3GLOAssertOn403"),
    ),
    (
        "ais-at-legacy-account-acc-100600",
        "Fails using incorrect permissions for Bulk Account.",
        "GET",
        "/open-banking/v4.0/aisp/accounts",
        ("OB-301-ACC-100600",),
        (),
        ("OB3GLOAssertOn401", "OB3GLOAssertOn403"),
    ),
    (
        "ais-at-legacy-account-acc-101000",
        "Fails using 401 unauthorized given no token for Accounts.",
        "GET",
        "/open-banking/v4.0/aisp/accounts/{AccountId}",
        ("OB-301-ACC-101000",),
        (),
        ("OB3GLOAssertOn401", "OB3GLOAssertOn403"),
    ),
    (
        "ais-at-legacy-account-acc-101100",
        "Fails using 401 unauthorized given no token for Bulk Accounts.",
        "GET",
        "/open-banking/v4.0/aisp/accounts",
        ("OB-301-ACC-101100",),
        (),
        ("OB3GLOAssertOn401", "OB3GLOAssertOn403"),
    ),
    (
        "ais-at-legacy-balance-bal-101300",
        "All data returned for Bulk Accounts with ReadBalances permission, status and headers.",
        "GET",
        "/open-banking/v4.0/aisp/balances",
        ("OB-301-BAL-101300",),
        ("OB-400-BAL-101300",),
        ("OB3GLOAssertOn200", "OB3GLOFAPIHeader"),
    ),
    (
        "ais-at-legacy-balance-bal-101400",
        "Fails on incorrect permissions are provided for balance.",
        "GET",
        "/open-banking/v4.0/aisp/accounts/{AccountId}/balances",
        ("OB-301-BAL-101400",),
        ("OB-400-BAL-101400",),
        ("OB3GLOAssertOn400", "OB3GLOAssertOn403"),
    ),
    (
        "ais-at-legacy-balance-bal-101500",
        "Fails on incorrect permissions for Bulk Balances.",
        "GET",
        "/open-banking/v4.0/aisp/balances",
        ("OB-301-BAL-101500",),
        ("OB-400-BAL-101500",),
        ("OB3GLOAssertOn401",),
    ),
    (
        "ais-at-legacy-balance-bal-101600",
        "Fails when account is invalid for Balances.",
        "GET",
        "/open-banking/v4.0/aisp/accounts/{AccountId}/balances",
        ("OB-301-BAL-101600",),
        ("OB-400-BAL-101600",),
        ("OB3GLOAssertOn400", "OB3GLOAssertOn403"),
    ),
    (
        "ais-at-legacy-balance-bal-101700",
        "Fails when token is from client grant.",
        "GET",
        "/open-banking/v4.0/aisp/balances",
        ("OB-301-BAL-101700",),
        ("OB-400-BAL-101700",),
        ("OB3GLOAssertOn401", "OB3GLOAssertOn403"),
    ),
    (
        "ais-at-legacy-balance-bal-101703",
        "The x-fapi-interaction-id is played-back for Balances.",
        "GET",
        "/open-banking/v4.0/aisp/balances",
        ("OB-301-BAL-101703",),
        ("OB-400-BAL-101703",),
        ("OB3GLOAssertFAPIPlayBack",),
    ),
    (
        "ais-at-legacy-beneficiary-ben-101800",
        "Minimal account Beneficiary data returned with ReadBeneficiariesBasic permission.",
        "GET",
        "/open-banking/v4.0/aisp/accounts/{AccountId}/beneficiaries",
        ("OB-301-BEN-101800",),
        ("OB-400-BEN-101800",),
        ("AssertAllBeneficiaryDetailsNotPresent", "OB3GLOAssertOn200", "OB3GLOFAPIHeader"),
    ),
    (
        "ais-at-legacy-beneficiary-ben-101900",
        "Minimal bulk Beneficiaries data returned with ReadBeneficiariesBasic permission.",
        "GET",
        "/open-banking/v4.0/aisp/beneficiaries",
        ("OB-301-BEN-101900",),
        ("OB-400-BEN-101900",),
        ("AssertAllBeneficiaryDetailsNotPresent", "OB3GLOAssertOn200", "OB3GLOFAPIHeader"),
    ),
    (
        "ais-at-legacy-beneficiary-ben-102000",
        "Fails when incorrect permissions are provided for Bulk Beneficiaries.",
        "GET",
        "/open-banking/v4.0/aisp/beneficiaries",
        ("OB-301-BEN-102000",),
        ("OB-400-BEN-102000",),
        ("OB3GLOAssertOn401",),
    ),
    (
        "ais-at-legacy-beneficiary-ben-102100",
        "Fails given incorrect permission is provided for a Beneficiary.",
        "GET",
        "/open-banking/v4.0/aisp/accounts/{AccountId}/beneficiaries",
        ("OB-301-BEN-102100",),
        ("OB-400-BEN-102100",),
        ("OB3GLOAssertOn401",),
    ),
    (
        "ais-at-legacy-beneficiary-ben-102200",
        "Fails when account is invalid for Beneficiary.",
        "GET",
        "/open-banking/v4.0/aisp/accounts/{AccountId}/beneficiaries",
        ("OB-301-BEN-102200",),
        ("OB-400-BEN-102200",),
        (
            "OB3GLOAssertOn403",
            "OB3IPAssertResourceFieldInvalidOBErrorCode400",
            "OB3IPAssertResourceFieldInvalidOBErrorCode400V4",
            "OB3IPAssertResourceNotFoundOBErrorCode400",
            "OB3IPAssertResourceNotFoundOBErrorCode400V4",
        ),
    ),
    (
        "ais-at-legacy-beneficiary-ben-102201",
        "Fails 404 on an invalid Beneficiary resource.",
        "GET",
        "/open-banking/v4.0/aisp/accounts/{AccountId}/beneficiaries/foobar",
        ("OB-301-BEN-102201",),
        ("OB-400-BEN-102201",),
        ("OB3GLOAssertOn404",),
    ),
    (
        "ais-at-legacy-beneficiary-ben-102203",
        "Fails using client grant token on Beneficiaries.",
        "GET",
        "/open-banking/v4.0/aisp/beneficiaries",
        ("OB-301-BEN-102203",),
        ("OB-400-BEN-102203",),
        ("OB3GLOAssertOn401", "OB3GLOAssertOn403"),
    ),
    (
        "ais-at-legacy-beneficiary-ben-102204",
        "The x-fapi-interaction-id is played-back for account Beneficiary.",
        "GET",
        "/open-banking/v4.0/aisp/accounts/{AccountId}/beneficiaries/",
        ("OB-301-BEN-102204",),
        ("OB-400-BEN-102204",),
        ("OB3GLOAssertFAPIPlayBack",),
    ),
    (
        "ais-at-legacy-beneficiary-ben-102205",
        "The x-fapi-interaction-id is played-back for Beneficiary.",
        "GET",
        "/open-banking/v4.0/aisp/beneficiaries",
        ("OB-301-BEN-102205",),
        ("OB-400-BEN-102205",),
        ("OB3GLOAssertFAPIPlayBack",),
    ),
    (
        "ais-at-legacy-direct-debit-dir-102300",
        "All data returned for a given account with ReadDirectDebits permission, status and headers.",
        "GET",
        "/open-banking/v4.0/aisp/accounts/{AccountId}/direct-debits",
        ("OB-301-DIR-102300",),
        ("OB-400-DIR-102300",),
        ("OB3GLOAssertOn200", "OB3GLOFAPIHeader"),
    ),
    (
        "ais-at-legacy-direct-debit-dir-102400",
        "All data returned for a given account with ReadDirectDebits permission, status and headers.",
        "GET",
        "/open-banking/v4.0/aisp/direct-debits",
        ("OB-301-DIR-102400",),
        ("OB-400-DIR-102400",),
        ("OB3GLOAssertOn200", "OB3GLOFAPIHeader"),
    ),
    (
        "ais-at-legacy-direct-debit-dir-102500",
        "Fails when account is invalid for Direct Debit.",
        "GET",
        "/open-banking/v4.0/aisp/accounts/{AccountId}/direct-debits",
        ("OB-301-DIR-102500",),
        ("OB-400-DIR-102500",),
        ("OB3GLOAssertOn400", "OB3GLOAssertOn403"),
    ),
    (
        "ais-at-legacy-direct-debit-dir-102501",
        "Fails 404 on an invalid Direct Debit resource.",
        "GET",
        "/open-banking/v4.0/aisp/accounts/{AccountId}/direct-debits/foobar",
        ("OB-301-DIR-102501",),
        ("OB-400-DIR-102501",),
        ("OB3GLOAssertOn404",),
    ),
    (
        "ais-at-legacy-direct-debit-dir-102502",
        "Fails when token using on Direct Debit is from client grant.",
        "GET",
        "/open-banking/v4.0/aisp/direct-debits",
        ("OB-301-DIR-102502",),
        ("OB-400-DIR-102502",),
        ("OB3GLOAssertOn401", "OB3GLOAssertOn403"),
    ),
    (
        "ais-at-legacy-direct-debit-dir-102503",
        "The x-fapi-interaction-id is played-back for account Direct Debit.",
        "GET",
        "/open-banking/v4.0/aisp/accounts/{AccountId}/direct-debits",
        ("OB-301-DIR-102503",),
        ("OB-400-DIR-102503",),
        ("OB3GLOAssertFAPIPlayBack",),
    ),
    (
        "ais-at-legacy-direct-debit-dir-102504",
        "The x-fapi-interaction-id is played-back for Direct Debit.",
        "GET",
        "/open-banking/v4.0/aisp/direct-debits",
        ("OB-301-DIR-102504",),
        ("OB-400-DIR-102504",),
        ("OB3GLOAssertFAPIPlayBack",),
    ),
    (
        "ais-at-legacy-offer-off-102600",
        "Account Offers data returned with ReadOffers permission.",
        "GET",
        "/open-banking/v4.0/aisp/accounts/{AccountId}/offers",
        ("OB-301-OFF-102600",),
        ("OB-400-OFF-102600",),
        ("OB3GLOAssertOn200", "OB3GLOFAPIHeader"),
    ),
    (
        "ais-at-legacy-offer-off-102700",
        "All data returned for Offers with ReadOffers permission with additional schema checks, status and headers.",
        "GET",
        "/open-banking/v4.0/aisp/offers",
        ("OB-301-OFF-102700",),
        ("OB-400-OFF-102700",),
        ("OB3GLOAssertOn200", "OB3GLOFAPIHeader"),
    ),
    (
        "ais-at-legacy-offer-off-102800",
        "Fails when account is invalid for Offer.",
        "GET",
        "/open-banking/v4.0/aisp/accounts/{AccountId}/offers",
        ("OB-301-OFF-102800",),
        ("OB-400-OFF-102800",),
        (
            "OB3GLOAssertOn403",
            "OB3IPAssertResourceFieldInvalidOBErrorCode400",
            "OB3IPAssertResourceFieldInvalidOBErrorCode400V4",
            "OB3IPAssertResourceNotFoundOBErrorCode400",
            "OB3IPAssertResourceNotFoundOBErrorCode400V4",
        ),
    ),
    (
        "ais-at-legacy-offer-off-102801",
        "Fails 404 on an invalid Offer resource.",
        "GET",
        "/open-banking/v4.0/aisp/accounts/{AccountId}/offers/foobar",
        ("OB-301-OFF-102801",),
        ("OB-400-OFF-102801",),
        ("OB3GLOAssertOn404",),
    ),
    (
        "ais-at-legacy-offer-off-102802",
        "Fails when token using on Offers is from client grant.",
        "GET",
        "/open-banking/v4.0/aisp/offers",
        ("OB-301-OFF-102802",),
        ("OB-400-OFF-102802",),
        ("OB3GLOAssertOn401", "OB3GLOAssertOn403"),
    ),
    (
        "ais-at-legacy-offer-off-102803",
        "The x-fapi-interaction-id is played-back for account Offer.",
        "GET",
        "/open-banking/v4.0/aisp/accounts/{AccountId}/offers",
        ("OB-301-OFF-102803",),
        ("OB-400-OFF-102803",),
        ("OB3GLOAssertFAPIPlayBack",),
    ),
    (
        "ais-at-legacy-offer-off-102804",
        "The x-fapi-interaction-id is played-back for Offer.",
        "GET",
        "/open-banking/v4.0/aisp/offers",
        ("OB-301-OFF-102804",),
        ("OB-400-OFF-102804",),
        ("OB3GLOAssertFAPIPlayBack",),
    ),
    (
        "ais-at-legacy-party-par-102900",
        "Account Party data returned with ReadParty permission.",
        "GET",
        "/open-banking/v4.0/aisp/accounts/{AccountId}/party",
        ("OB-301-PAR-102900",),
        ("OB-400-PAR-102900",),
        ("OB3GLOAssertOn200", "OB3GLOFAPIHeader"),
    ),
    (
        "ais-at-legacy-party-par-102901",
        "Data returned for a given Bulk Party using ReadPartyPSU permission, status and headers.",
        "GET",
        "/open-banking/v4.0/aisp/party",
        ("OB-301-PAR-102901",),
        ("OB-400-PAR-102901",),
        ("OB3GLOAssertOn200", "OB3GLOFAPIHeader"),
    ),
    (
        "ais-at-legacy-party-par-102902",
        "Account Parties data returned with ReadParty permission.",
        "GET",
        "/open-banking/v4.0/aisp/accounts/{AccountId}/parties",
        ("OB-301-PAR-102902",),
        ("OB-400-PAR-102902",),
        ("OB3GLOAssertOn200", "OB3GLOFAPIHeader"),
    ),
    (
        "ais-at-legacy-party-par-103000",
        "All data returned for ReadParty permission with additional schema checks, status and headers.",
        "GET",
        "/open-banking/v4.0/aisp/accounts/{AccountId}/party",
        ("OB-301-PAR-103000",),
        ("OB-400-PAR-103000",),
        ("OB3GLOAssertOn200", "OB3GLOFAPIHeader"),
    ),
    (
        "ais-at-legacy-party-par-103100",
        "Fails when account is invalid for a Party.",
        "GET",
        "/open-banking/v4.0/aisp/accounts/{AccountId}/party",
        ("OB-301-PAR-103100",),
        ("OB-400-PAR-103100",),
        ("OB3GLOAssertOn400", "OB3GLOAssertOn403"),
    ),
    (
        "ais-at-legacy-party-par-103101",
        "Fails 404 on an invalid Party resource.",
        "GET",
        "/open-banking/v4.0/aisp/accounts/{AccountId}/party/foobar",
        ("OB-301-PAR-103101",),
        ("OB-400-PAR-103101",),
        ("OB3GLOAssertOn404",),
    ),
    (
        "ais-at-legacy-party-par-103102",
        "Fails when token using on Party is from client grant.",
        "GET",
        "/open-banking/v4.0/aisp/party",
        ("OB-301-PAR-103102",),
        ("OB-400-PAR-103102",),
        ("OB3GLOAssertOn401", "OB3GLOAssertOn403"),
    ),
    (
        "ais-at-legacy-party-par-103103",
        "Fails when incorrect permissions are provided for Bulk Party.",
        "GET",
        "/open-banking/v4.0/aisp/beneficiaries",
        ("OB-301-PAR-103103",),
        ("OB-400-PAR-103103",),
        ("OB3GLOAssertOn401",),
    ),
    (
        "ais-at-legacy-party-par-103104",
        "The x-fapi-interaction-id is played-back for account Party.",
        "GET",
        "/open-banking/v4.0/aisp/accounts/{AccountId}/party",
        ("OB-301-PAR-103104",),
        ("OB-400-PAR-103104",),
        ("OB3GLOAssertFAPIPlayBack",),
    ),
    (
        "ais-at-legacy-party-par-103105",
        "The x-fapi-interaction-id is played-back for Party.",
        "GET",
        "/open-banking/v4.0/aisp/party",
        ("OB-301-PAR-103105",),
        ("OB-400-PAR-103105",),
        ("OB3GLOAssertFAPIPlayBack",),
    ),
    (
        "ais-at-legacy-party-par-103106",
        "Fails when account is invalid for Parties.",
        "GET",
        "/open-banking/v4.0/aisp/accounts/{AccountId}/parties",
        ("OB-301-PAR-103106",),
        ("OB-400-PAR-103106",),
        ("OB3GLOAssertOn400", "OB3GLOAssertOn403"),
    ),
    (
        "ais-at-legacy-party-par-103107",
        "Fails 404 on an invalid Party resource.",
        "GET",
        "/open-banking/v4.0/aisp/accounts/{AccountId}/parties/foobar",
        ("OB-301-PAR-103107",),
        ("OB-400-PAR-103107",),
        ("OB3GLOAssertOn404",),
    ),
    (
        "ais-at-legacy-party-par-103108",
        "The x-fapi-interaction-id is played-back for account Parties.",
        "GET",
        "/open-banking/v4.0/aisp/accounts/{AccountId}/parties",
        ("OB-301-PAR-103108",),
        ("OB-400-PAR-103108",),
        ("OB3GLOAssertFAPIPlayBack",),
    ),
    (
        "ais-at-legacy-product-pro-103200",
        "Account Product data returned with ReadProducts permission.",
        "GET",
        "/open-banking/v4.0/aisp/accounts/{AccountId}/product",
        ("OB-301-PRO-103200",),
        ("OB-400-PRO-103200",),
        ("OB3GLOAssertOn200", "OB3GLOFAPIHeader"),
    ),
    (
        "ais-at-legacy-product-pro-103300",
        "All data returned for ReadProducts permission with additional schema checks, status and headers.",
        "GET",
        "/open-banking/v4.0/aisp/products",
        ("OB-301-PRO-103300",),
        ("OB-400-PRO-103300",),
        ("OB3GLOAssertOn200", "OB3GLOFAPIHeader"),
    ),
    (
        "ais-at-legacy-product-pro-103400",
        "Fails when account is invalid for a Product.",
        "GET",
        "/open-banking/v4.0/aisp/accounts/{AccountId}/product",
        ("OB-301-PRO-103400",),
        ("OB-400-PRO-103400",),
        ("OB3GLOAssertOn400", "OB3GLOAssertOn403"),
    ),
    (
        "ais-at-legacy-product-pro-103401",
        "Fails 404 on an invalid Product resource.",
        "GET",
        "/open-banking/v4.0/aisp/accounts/{AccountId}/product/foobar",
        ("OB-301-PRO-103401",),
        ("OB-400-PRO-103401",),
        ("OB3GLOAssertOn404",),
    ),
    (
        "ais-at-legacy-product-pro-102802",
        "Fails when token using on Product is from client grant.",
        "GET",
        "/open-banking/v4.0/aisp/products",
        ("OB-301-PRO-102802",),
        ("OB-400-PRO-102802",),
        ("OB3GLOAssertOn401", "OB3GLOAssertOn403"),
    ),
    (
        "ais-at-legacy-product-pro-103402",
        "The x-fapi-interaction-id is played-back for account Product.",
        "GET",
        "/open-banking/v4.0/aisp/accounts/{AccountId}/product",
        ("OB-301-PRO-103402",),
        ("OB-400-PRO-103402",),
        ("OB3GLOAssertFAPIPlayBack",),
    ),
    (
        "ais-at-legacy-product-pro-103403",
        "The x-fapi-interaction-id is played-back for Product.",
        "GET",
        "/open-banking/v4.0/aisp/products",
        ("OB-301-PRO-103403",),
        ("OB-400-PRO-103403",),
        ("OB3GLOAssertFAPIPlayBack",),
    ),
    (
        "ais-at-legacy-scheduled-payment-scp-103500",
        "Detailed level data returned for a given account using the ReadScheduledPaymentsDetail permission.",
        "GET",
        "/open-banking/v4.0/aisp/accounts/{AccountId}/scheduled-payments",
        ("OB-301-SCP-103500",),
        ("OB-400-SCP-103500",),
        ("OB3GLOAssertOn200", "OB3GLOFAPIHeader"),
    ),
    (
        "ais-at-legacy-scheduled-payment-scp-103600",
        "Bulk scheduled payments returned with ReadScheduledPaymentsDetail permission.",
        "GET",
        "/open-banking/v4.0/aisp/scheduled-payments",
        ("OB-301-SCP-103600",),
        ("OB-400-SCP-103600",),
        ("OB3GLOAssertOn200", "OB3GLOFAPIHeader"),
    ),
    (
        "ais-at-legacy-scheduled-payment-scp-103700",
        "Fails when account is invalid for Scheduled Payment.",
        "GET",
        "/open-banking/v4.0/aisp/accounts/{AccountId}/scheduled-payments",
        ("OB-301-SCP-103700",),
        ("OB-400-SCP-103700",),
        (
            "OB3GLOAssertOn403",
            "OB3IPAssertResourceFieldInvalidOBErrorCode400",
            "OB3IPAssertResourceFieldInvalidOBErrorCode400V4",
            "OB3IPAssertResourceNotFoundOBErrorCode400",
            "OB3IPAssertResourceNotFoundOBErrorCode400V4",
        ),
    ),
    (
        "ais-at-legacy-scheduled-payment-scp-103701",
        "Fails 404 on an invalid Scheduled Payment resource.",
        "GET",
        "/open-banking/v4.0/aisp/accounts/{AccountId}/scheduled-payments/foobar",
        ("OB-301-SCP-103701",),
        ("OB-400-SCP-103701",),
        ("OB3GLOAssertOn404",),
    ),
    (
        "ais-at-legacy-scheduled-payment-scp-103702",
        "Fails when token is from client grant.",
        "GET",
        "/open-banking/v4.0/aisp/scheduled-payments",
        ("OB-301-SCP-103702",),
        ("OB-400-SCP-103702",),
        ("OB3GLOAssertOn401", "OB3GLOAssertOn403"),
    ),
    (
        "ais-at-legacy-scheduled-payment-scp-103703",
        "The x-fapi-interaction-id is played-back for account Scheduled Payments.",
        "GET",
        "/open-banking/v4.0/aisp/accounts/{AccountId}/scheduled-payments",
        ("OB-301-SCP-103703",),
        ("OB-400-SCP-103703",),
        ("OB3GLOAssertFAPIPlayBack",),
    ),
    (
        "ais-at-legacy-scheduled-payment-scp-103704",
        "The x-fapi-interaction-id is played-back for Scheduled Payments.",
        "GET",
        "/open-banking/v4.0/aisp/scheduled-payments",
        ("OB-301-SCP-103704",),
        ("OB-400-SCP-103704",),
        ("OB3GLOAssertFAPIPlayBack",),
    ),
    (
        "ais-at-legacy-standing-order-sto-103800",
        "Detailed level data returned for a given account using the ReadStandingOrdersDetail permission.",
        "GET",
        "/open-banking/v4.0/aisp/accounts/{AccountId}/standing-orders",
        ("OB-301-STO-103800",),
        ("OB-400-STO-103800",),
        ("OB3GLOAssertOn200", "OB3GLOFAPIHeader"),
    ),
    (
        "ais-at-legacy-standing-order-sto-103900",
        "All data returned for a given account using the ReadStandingOrdersDetail permission.",
        "GET",
        "/open-banking/v4.0/aisp/standing-orders",
        ("OB-301-STO-103900",),
        ("OB-400-STO-103900",),
        ("OB3GLOAssertOn200", "OB3GLOFAPIHeader"),
    ),
    (
        "ais-at-legacy-standing-order-sto-103901",
        "Bulk standing orders return HTTP 200 with FAPI headers.",
        "GET",
        "/open-banking/v4.0/aisp/standing-orders",
        ("OB-301-STO-103901",),
        ("OB-400-STO-103901",),
        ("OB3GLOAssertOn200", "OB3GLOFAPIHeader"),
    ),
    (
        "ais-at-legacy-standing-order-sto-104000",
        "Fails when account is invalid for a Standing Order.",
        "GET",
        "/open-banking/v4.0/aisp/accounts/{AccountId}/standing-orders",
        ("OB-301-STO-104000",),
        ("OB-400-STO-104000",),
        (
            "OB3GLOAssertOn403",
            "OB3IPAssertResourceFieldInvalidOBErrorCode400",
            "OB3IPAssertResourceFieldInvalidOBErrorCode400V4",
            "OB3IPAssertResourceNotFoundOBErrorCode400",
            "OB3IPAssertResourceNotFoundOBErrorCode400V4",
        ),
    ),
    (
        "ais-at-legacy-standing-order-sto-104100",
        "Fails 404 on an invalid Standing Order resource.",
        "GET",
        "/open-banking/v4.0/aisp/accounts/{AccountId}/standing-orders/foobar",
        ("OB-301-STO-104100",),
        ("OB-400-STO-104100",),
        ("OB3GLOAssertOn404",),
    ),
    (
        "ais-at-legacy-standing-order-sto-104101",
        "Fails when token is from client grant.",
        "GET",
        "/open-banking/v4.0/aisp/standing-orders",
        ("OB-301-STO-104101",),
        ("OB-400-STO-104101",),
        ("OB3GLOAssertOn401", "OB3GLOAssertOn403"),
    ),
    (
        "ais-at-legacy-standing-order-sto-104102",
        "The x-fapi-interaction-id is played-back for account Standing Orders.",
        "GET",
        "/open-banking/v4.0/aisp/accounts/{AccountId}/standing-orders",
        ("OB-301-STO-104102",),
        ("OB-400-STO-104102",),
        ("OB3GLOAssertFAPIPlayBack",),
    ),
    (
        "ais-at-legacy-standing-order-sto-104103",
        "The x-fapi-interaction-id is played-back for Standing Orders.",
        "GET",
        "/open-banking/v4.0/aisp/standing-orders",
        ("OB-301-STO-104103",),
        ("OB-400-STO-104103",),
        ("OB3GLOAssertFAPIPlayBack",),
    ),
    (
        "ais-at-legacy-transaction-tra-105700",
        "Fails when account is invalid for a Transaction.",
        "GET",
        "/open-banking/v4.0/aisp/accounts/{AccountId}/transactions",
        ("OB-301-TRA-105700",),
        ("OB-400-TRA-105700",),
        ("OB3GLOAssertOn400", "OB3GLOAssertOn403"),
    ),
    (
        "ais-at-legacy-statement-sta-105900",
        "Succeeds when fromStatementDateTime is a valid ISO8601 formatted date variant 1.",
        "GET",
        "/open-banking/v4.0/aisp/statements",
        ("OB-301-STA-105900",),
        ("OB-400-STA-105900",),
        ("OB3GLOAssertFAPIPlayBack", "OB3GLOAssertOn200"),
    ),
    (
        "ais-at-legacy-statement-sta-106000",
        "Succeeds when fromStatementDateTime is a valid ISO8601 formatted date variant 2.",
        "GET",
        "/open-banking/v4.0/aisp/statements",
        ("OB-301-STA-106000",),
        ("OB-400-STA-106000",),
        ("OB3GLOAssertFAPIPlayBack", "OB3GLOAssertOn200"),
    ),
    (
        "ais-at-legacy-statement-sta-106100",
        "Succeeds when fromStatementDateTime is a valid ISO8601 formatted date variant 3.",
        "GET",
        "/open-banking/v4.0/aisp/statements",
        ("OB-301-STA-106100",),
        ("OB-400-STA-106100",),
        ("OB3GLOAssertFAPIPlayBack", "OB3GLOAssertOn200"),
    ),
    (
        "ais-at-legacy-statement-sta-106200",
        "Succeeds when fromStatementDateTime is a valid ISO8601 formatted date variant 4.",
        "GET",
        "/open-banking/v4.0/aisp/statements",
        ("OB-301-STA-106200",),
        ("OB-400-STA-106200",),
        ("OB3GLOAssertFAPIPlayBack", "OB3GLOAssertOn200"),
    ),
    (
        "ais-at-legacy-statement-sta-106300",
        "Succeeds when fromStatementDateTime is a valid ISO8601 formatted date variant 5.",
        "GET",
        "/open-banking/v4.0/aisp/statements",
        ("OB-301-STA-106300",),
        ("OB-400-STA-106300",),
        ("OB3GLOAssertFAPIPlayBack", "OB3GLOAssertOn200"),
    ),
)
"""Legacy AIS manifest cases outside the original accounts/transactions slice."""

_LEGACY_STATUS_ASSERTIONS = {
    "OB3GLOAssertOn200": 200,
    "OB3GLOAssertOn400": 400,
    "OB3GLOAssertOn401": 401,
    "OB3GLOAssertOn403": 403,
    "OB3GLOAssertOn404": 404,
}
"""Legacy assertion ids that map directly to a representative HTTP status."""

_AIS_V40_SCHEMA_CHECK_SCRIPT_IDS = frozenset(
    {
        "OB-400-ACC-100000",
        "OB-400-ACC-100200",
        "OB-400-ACC-100300",
        "OB-400-ACC-100400",
        "OB-400-BAL-101200",
        "OB-400-BAL-101300",
        "OB-400-BEN-101800",
        "OB-400-BEN-101900",
        "OB-400-DIR-102300",
        "OB-400-DIR-102400",
        "OB-400-OFF-102600",
        "OB-400-OFF-102700",
        "OB-400-PAR-102900",
        "OB-400-PAR-102901",
        "OB-400-PAR-102902",
        "OB-400-PAR-103000",
        "OB-400-PRO-103200",
        "OB-400-PRO-103300",
        "OB-400-SCP-103500",
        "OB-400-SCP-103600",
        "OB-400-STO-103800",
        "OB-400-STO-103900",
        "OB-400-STO-103901",
        "OB-400-TRA-105000",
        "OB-400-TRA-105100",
        "OB-400-TRA-105110",
        "OB-400-TRA-105120",
        "OB-400-TRA-105200",
        "OB-400-TRA-101200",
        "OB-400-STA-105900",
        "OB-400-STA-106000",
        "OB-400-STA-106100",
        "OB-400-STA-106200",
        "OB-400-STA-106300",
    }
)
"""Legacy v4 AIS scripts that enabled response schema checks."""

_AIS_V40_RESPONSE_SCHEMA_REFS = {
    ("GET", f"{_AIS_BASE_PATH}/accounts/{{AccountId}}", 200): "#/components/schemas/OBReadAccount6",
    ("GET", f"{_AIS_BASE_PATH}/accounts", 200): "#/components/schemas/OBReadAccount6",
    ("GET", f"{_AIS_BASE_PATH}/accounts/{{AccountId}}/balances", 200): "#/components/schemas/OBReadBalance1",
    ("GET", f"{_AIS_BASE_PATH}/balances", 200): "#/components/schemas/OBReadBalance1",
    ("GET", f"{_AIS_BASE_PATH}/accounts/{{AccountId}}/beneficiaries", 200): "#/components/schemas/OBReadBeneficiary5",
    ("GET", f"{_AIS_BASE_PATH}/beneficiaries", 200): "#/components/schemas/OBReadBeneficiary5",
    ("GET", f"{_AIS_BASE_PATH}/accounts/{{AccountId}}/direct-debits", 200): "#/components/schemas/OBReadDirectDebit2",
    ("GET", f"{_AIS_BASE_PATH}/direct-debits", 200): "#/components/schemas/OBReadDirectDebit2",
    ("GET", f"{_AIS_BASE_PATH}/accounts/{{AccountId}}/offers", 200): "#/components/schemas/OBReadOffer1",
    ("GET", f"{_AIS_BASE_PATH}/offers", 200): "#/components/schemas/OBReadOffer1",
    ("GET", f"{_AIS_BASE_PATH}/accounts/{{AccountId}}/party", 200): "#/components/schemas/OBReadParty2",
    ("GET", f"{_AIS_BASE_PATH}/party", 200): "#/components/schemas/OBReadParty2",
    ("GET", f"{_AIS_BASE_PATH}/accounts/{{AccountId}}/parties", 200): "#/components/schemas/OBReadParty3",
    ("GET", f"{_AIS_BASE_PATH}/accounts/{{AccountId}}/product", 200): "#/components/schemas/OBReadProduct2",
    ("GET", f"{_AIS_BASE_PATH}/products", 200): "#/components/schemas/OBReadProduct2",
    ("GET", f"{_AIS_BASE_PATH}/accounts/{{AccountId}}/scheduled-payments", 200): (
        "#/components/schemas/OBReadScheduledPayment3"
    ),
    ("GET", f"{_AIS_BASE_PATH}/scheduled-payments", 200): "#/components/schemas/OBReadScheduledPayment3",
    ("GET", f"{_AIS_BASE_PATH}/accounts/{{AccountId}}/standing-orders", 200): (
        "#/components/schemas/OBReadStandingOrder6"
    ),
    ("GET", f"{_AIS_BASE_PATH}/standing-orders", 200): "#/components/schemas/OBReadStandingOrder6",
    ("GET", f"{_AIS_BASE_PATH}/accounts/{{AccountId}}/transactions", 200): "#/components/schemas/OBReadTransaction6",
    ("GET", f"{_AIS_BASE_PATH}/transactions", 200): "#/components/schemas/OBReadTransaction6",
    ("GET", f"{_AIS_BASE_PATH}/statements", 200): "#/components/schemas/OBReadStatement2",
}
"""Bundled v4 Account and Transaction response schemas keyed by operation and status."""

_AIS_V40_ONE_OF_STATUS_SCRIPT_IDS = frozenset(
    {
        "OB-400-BAL-101400",
        "OB-400-BAL-101600",
        "OB-400-DIR-102500",
        "OB-400-PAR-103100",
        "OB-400-PAR-103106",
        "OB-400-PRO-103400",
        "OB-400-TRA-105700",
        "OB-400-BEN-102200",
        "OB-400-OFF-102800",
        "OB-400-SCP-103700",
        "OB-400-STO-104000",
    }
)
"""Legacy v4 AIS scripts whose status assertions were declared as asserts_one_of."""


def _applicability(
    *endpoint_refs: EndpointRef,
    required_capability_ids: tuple[str, ...] = (),
    specification_versions: tuple[str, ...] = (),
) -> TestCaseApplicability:
    """Build profile and endpoint applicability for imported AIS cases.

    Args:
        *endpoint_refs: Endpoints that must be implemented for the case to apply.
        required_capability_ids: Endpoint capability ids that must be selected
            before the case becomes directly applicable.
        specification_versions: User-facing specification versions this case
            applies to.

    Returns:
        Applicability constrained to legacy FCS FCA profile coverage.
    """
    return TestCaseApplicability(
        security_profiles=_FAPI1_ADVANCED_ONLY,
        endpoint_refs=endpoint_refs,
        required_capability_ids=required_capability_ids,
        specification_versions=specification_versions,
    )


def _legacy_scope(
    *,
    v31_ids: tuple[str, ...],
    v40_ids: tuple[str, ...],
    legacy_assertions: tuple[str, ...],
) -> tuple[str, ...]:
    """Build traceability scope metadata that records legacy provenance.

    Args:
        v31_ids: Legacy script ids from ``ob_3.1_accounts_transactions_fca.json``.
        v40_ids: Legacy script ids from ``ob_4.0_accounts_transactions_fca.json``.
        legacy_assertions: Legacy assertion identifiers represented by the case.

    Returns:
        Compliance-scope metadata strings suitable for catalogue traceability.
    """
    return (
        "legacy-fcs-source:OpenBankingUK/conformance-suite@develop/manifests/ob_3.1_accounts_transactions_fca.json",
        "legacy-fcs-source:OpenBankingUK/conformance-suite@develop/manifests/ob_4.0_accounts_transactions_fca.json",
        f"legacy-fcs-v3.1-ids:{','.join(v31_ids) if v31_ids else 'none'}",
        f"legacy-fcs-v4.0-ids:{','.join(v40_ids) if v40_ids else 'none'}",
        f"legacy-fcs-assertions:{','.join(legacy_assertions)}",
    )


def _assertion(
    assertion_id: str,
    kind: AssertionKind,
    description: str,
    rule: dict[str, JsonValue],
) -> CatalogueAssertion:
    """Build a catalogue assertion entry.

    Args:
        assertion_id: Stable assertion identifier unique within a test case.
        kind: Assertion family understood by the catalogue foundation.
        description: Human-readable assertion summary.
        rule: JSON-serialisable assertion rule payload.

    Returns:
        A locked catalogue assertion.
    """
    return CatalogueAssertion(
        assertion_id=assertion_id,
        kind=kind,
        description=description,
        rule=rule,
    )


def _case(
    test_case_id: str,
    *,
    name: str,
    role: TestCaseRole,
    compliance_scope: tuple[str, ...],
    endpoint_refs: tuple[EndpointRef, ...],
    required_capability_ids: tuple[str, ...] = (),
    dependencies: tuple[str, ...],
    mandatory: bool,
    request_method: HttpMethod,
    request_path: str,
    runtime_requirements: tuple[RuntimeInputRequirement, ...] = (),
    assertions: tuple[CatalogueAssertion, ...] = (),
    generated_values: Mapping[str, GeneratedRuntimeValue] | None = None,
    permission_profile: _AisPermissionProfile = "basic",
    specification_versions: tuple[str, ...] = (),
) -> CatalogueTestCase:
    """Build an imported AIS catalogue test case.

    Args:
        test_case_id: Stable catalogue test case id.
        name: Human-readable case name.
        role: Catalogue execution role for the case.
        compliance_scope: Traceability labels and legacy provenance metadata.
        endpoint_refs: Endpoints that make the case directly applicable.
        required_capability_ids: Endpoint capability ids that must be selected
            before this case becomes directly applicable.
        dependencies: Other test case ids that must execute first.
        mandatory: Whether direct applicability makes the case non-deselectable.
        request_method: HTTP method represented by this case.
        request_path: Standards path represented by this case.
        runtime_requirements: Runtime inputs needed to execute the request.
        assertions: Locked assertions represented by this case.
        generated_values: Additional generated runtime values scoped to the
            request step.
        permission_profile: Legacy AIS permission profile used for protected
            resource requests that consume an account-access token.
        specification_versions: User-facing specification versions this case
            applies to.

    Returns:
        A fully populated ``CatalogueTestCase`` ready for compilation.
    """
    is_open_banking_api_request = request_path.startswith(_AIS_BASE_PATH)
    runtime_input_refs = tuple(
        requirement.input_id for requirement in runtime_requirements if requirement.source == "plan"
    )
    request_headers = open_banking_request_headers_for() if is_open_banking_api_request else ()
    required_token_id = None
    if test_case_id == "ais-at-setup-consent":
        required_token_id = _AIS_CLIENT_CREDENTIALS_AUTH_ID
    elif _ACCESS_TOKEN in runtime_requirements and _INVALID_ACCESS_TOKEN not in runtime_requirements:
        required_token_id = _AIS_RESOURCE_AUTH_IDS[permission_profile]
    produced_token_id = None
    request_generated_values: dict[str, GeneratedRuntimeValue] = (
        {"invalidAccessToken": "invalid-access-token"} if _INVALID_ACCESS_TOKEN in runtime_requirements else {}
    )
    if generated_values is not None:
        request_generated_values.update(generated_values)
    return CatalogueTestCase(
        test_case_id=test_case_id,
        name=name,
        role=role,
        compliance_scope=compliance_scope,
        applicability=_applicability(
            *endpoint_refs,
            required_capability_ids=required_capability_ids,
            specification_versions=specification_versions,
        ),
        mandatory=mandatory,
        dependencies=dependencies,
        runtime_input_requirements=runtime_requirements,
        request_steps=(
            CatalogueRequestStep(
                step_id=f"{test_case_id}-request",
                name=name,
                method=request_method,
                path=request_path,
                runtime_input_refs=runtime_input_refs,
                headers=request_headers,
                generated_values=request_generated_values,
                required_token_id=required_token_id,
                produced_token_id=produced_token_id,
                authorization_profile=permission_profile if required_token_id is not None else None,
            ),
        ),
        assertions=assertions,
    )


def _legacy_extra_runtime_requirements(
    *,
    path: str,
    legacy_assertion_ids: tuple[str, ...],
) -> tuple[RuntimeInputRequirement, ...]:
    """Select runtime inputs for a generated legacy AIS resource case.

    Args:
        path: Standards path represented by the legacy case.
        legacy_assertion_ids: Legacy assertion ids attached to the case.

    Returns:
        Runtime input requirements needed to execute the request.
    """
    uses_account_id = "{AccountId}" in path
    uses_invalid_token = "OB3GLOAssertOn401" in legacy_assertion_ids and "OB3GLOAssertOn200" not in legacy_assertion_ids
    if uses_account_id and uses_invalid_token:
        return (_RESOURCE_BASE_URL, _CONSENTED_ACCOUNT_ID, _INVALID_ACCESS_TOKEN)
    if uses_account_id:
        return _ACCOUNT_RESOURCE_RUNTIME_REQUIREMENTS
    if uses_invalid_token:
        return (_RESOURCE_BASE_URL, _INVALID_ACCESS_TOKEN)
    return _COMMON_RESOURCE_RUNTIME_REQUIREMENTS


def _legacy_extra_assertions(
    *,
    legacy_assertion_ids: tuple[str, ...],
    case_name: str,
    method: HttpMethod,
    path: str,
    v40_ids: tuple[str, ...],
) -> tuple[CatalogueAssertion, ...]:
    """Build representative assertions for generated legacy AIS cases.

    Args:
        legacy_assertion_ids: Legacy assertion ids attached to the case.
        case_name: Human-readable case name used in generated descriptions.
        method: HTTP method for the executable request.
        path: Standards path represented by the legacy case.
        v40_ids: Legacy v4 AIS manifest script identifiers in the group.

    Returns:
        Catalogue assertions representing status and FAPI-header checks.
    """
    assertions: list[CatalogueAssertion] = []
    accepted_statuses = _legacy_one_of_statuses(legacy_assertion_ids=legacy_assertion_ids, v40_ids=v40_ids)
    if accepted_statuses:
        assertions.append(
            _assertion(
                "status",
                "http_status",
                f"{case_name} returns HTTP {' or '.join(str(status) for status in accepted_statuses)}",
                {
                    "expectedOneOf": list(accepted_statuses),
                    "legacyAssertionIds": [
                        legacy_assertion_id
                        for legacy_assertion_id, expected_status in _LEGACY_STATUS_ASSERTIONS.items()
                        if expected_status in accepted_statuses
                    ],
                },
            )
        )
    else:
        for legacy_assertion_id, expected_status in _LEGACY_STATUS_ASSERTIONS.items():
            if legacy_assertion_id not in legacy_assertion_ids:
                continue
            assertions.append(
                _assertion(
                    "status",
                    "http_status",
                    f"{case_name} returns HTTP {expected_status}",
                    {"expected": expected_status, "legacyAssertionId": legacy_assertion_id},
                )
            )
            break
    if "OB3GLOFAPIHeader" in legacy_assertion_ids:
        assertions.append(
            _assertion(
                "fapi-header",
                "header",
                f"{case_name} includes x-fapi-interaction-id",
                {"name": "x-fapi-interaction-id", "rule": "present"},
            )
        )
    if "OB3GLOAssertFAPIPlayBack" in legacy_assertion_ids:
        assertions.append(
            _assertion(
                "fapi-playback",
                "header",
                f"{case_name} replays x-fapi-interaction-id",
                {"name": "x-fapi-interaction-id", "rule": "playback"},
            )
        )
    if "AssertAllBeneficiaryDetailsNotPresent" in legacy_assertion_ids:
        assertions.append(
            _assertion(
                "beneficiary-permissions-filter",
                "json_field",
                f"{case_name} omits beneficiary detail fields under ReadBeneficiariesBasic",
                {
                    "fields": ["CreditorAgent", "CreditorAccount"],
                    "path": "Data.Beneficiary",
                    "rule": "all_items_absent_fields",
                },
            )
        )
    if "OBACCAssertBankTransactionCode" in legacy_assertion_ids:
        assertions.append(
            _assertion(
                "bank-transaction-code",
                "json_field",
                f"{case_name} includes bank transaction codes",
                {"path": "Data.Transaction", "rule": "all_items_have_field", "field": "BankTransactionCode"},
            )
        )
    if "OBACCAssertProprietaryBankTransactionCode" in legacy_assertion_ids:
        assertions.append(
            _assertion(
                "proprietary-bank-transaction-code",
                "json_field",
                f"{case_name} includes proprietary bank transaction code details",
                {
                    "path": "Data.Transaction",
                    "rule": "all_items_have_field",
                    "field": "ProprietaryBankTransactionCode",
                },
            )
        )
    return (
        *assertions,
        *_legacy_schema_assertions(method=method, path=path, v40_ids=v40_ids, assertions=tuple(assertions)),
    )


def _legacy_one_of_statuses(*, legacy_assertion_ids: tuple[str, ...], v40_ids: tuple[str, ...]) -> tuple[int, ...]:
    """Return accepted status codes for legacy AIS one-of status assertions.

    Args:
        legacy_assertion_ids: Legacy assertion ids attached to the generated
            AIS case.
        v40_ids: Legacy v4 AIS manifest script identifiers in the group.

    Returns:
        Accepted HTTP status codes when the legacy manifest intentionally allowed
        a choice of status outcomes, otherwise an empty tuple.
    """
    legacy_id_set = set(legacy_assertion_ids)
    if {"OB3GLOAssertOn400", "OB3GLOAssertOn403"}.issubset(legacy_id_set):
        return (400, 403)
    if _AIS_V40_ONE_OF_STATUS_SCRIPT_IDS.intersection(v40_ids) and "OB3GLOAssertOn403" in legacy_id_set:
        return (403,)
    return ()


def _legacy_schema_assertions(
    *,
    method: HttpMethod,
    path: str,
    v40_ids: tuple[str, ...],
    assertions: tuple[CatalogueAssertion, ...],
) -> tuple[CatalogueAssertion, ...]:
    """Build response-schema assertions for legacy v4 AIS schema checks.

    Args:
        method: HTTP method for the executable request.
        path: Standards path represented by the legacy case.
        v40_ids: Legacy v4 AIS manifest script identifiers in the group.
        assertions: Existing assertions used to infer the response status.

    Returns:
        Response-schema assertions for matching v4 legacy schema checks.
    """
    if not _AIS_V40_SCHEMA_CHECK_SCRIPT_IDS.intersection(v40_ids):
        return ()
    schema_assertions: list[CatalogueAssertion] = []
    for expected_status in _expected_statuses_from_assertions(assertions):
        schema_ref = _AIS_V40_RESPONSE_SCHEMA_REFS.get((method, path, expected_status))
        if schema_ref is None:
            continue
        schema_assertions.append(
            _assertion(
                f"response-schema-{expected_status}",
                "response_schema",
                f"Response body satisfies the legacy v4 AIS {expected_status} schema check.",
                {
                    "source": "bundled_openapi",
                    "document": "ob-read-write-v4.0-account-info-openapi",
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


def _specification_versions_for_legacy_ids(v31_ids: tuple[str, ...], v40_ids: tuple[str, ...]) -> tuple[str, ...]:
    """Return user-facing versions that can execute the represented AIS scripts.

    Args:
        v31_ids: Legacy v3.1 AIS manifest script identifiers in the group.
        v40_ids: Legacy v4.0 AIS manifest script identifiers in the group.

    Returns:
        Specification-version applicability for cases that are only valid for a
        single legacy major version, otherwise an empty tuple for shared cases.
    """
    if v40_ids and not v31_ids:
        return _AIS_V40_SPECIFICATION_VERSIONS
    if v31_ids and not v40_ids:
        return _AIS_V31_SPECIFICATION_VERSIONS
    return ()


def _with_ais_strict_v4_parity(test_case: CatalogueTestCase) -> CatalogueTestCase:
    """Add executable assertions required by legacy v4 AIS parity.

    Args:
        test_case: Catalogue case to post-process.

    Returns:
        Catalogue case with missing legacy v4 response-schema assertions added.
    """
    if any(assertion.kind == "response_schema" for assertion in test_case.assertions):
        return test_case
    schema_assertions = _legacy_schema_assertions(
        method=test_case.request_steps[0].method,
        path=test_case.request_steps[0].path,
        v40_ids=_legacy_v4_ids_from_scope(test_case.compliance_scope),
        assertions=test_case.assertions,
    )
    if not schema_assertions:
        return test_case
    return replace(test_case, assertions=(*test_case.assertions, *schema_assertions))


def _legacy_v4_ids_from_scope(compliance_scope: tuple[str, ...]) -> tuple[str, ...]:
    """Return legacy v4 AIS script ids from compliance-scope labels.

    Args:
        compliance_scope: Traceability labels attached to a catalogue case.

    Returns:
        Legacy v4 script identifiers represented by the case.
    """
    for scope in compliance_scope:
        if not scope.startswith("legacy-fcs-v4.0-ids:"):
            continue
        raw_ids = scope.removeprefix("legacy-fcs-v4.0-ids:")
        if raw_ids == "none":
            return ()
        return tuple(script_id for script_id in raw_ids.split(",") if script_id)
    return ()


def _legacy_extra_case(data: _LegacyExtraCaseData) -> CatalogueTestCase:
    """Build an AIS test case for one legacy manifest script group.

    Args:
        data: Compact legacy case tuple.

    Returns:
        Catalogue test case that keeps the legacy script group executable and traceable.
    """
    test_case_id, name, method, path, v31_ids, v40_ids, legacy_assertion_ids = data
    endpoint_path = _legacy_extra_endpoint_path(path)
    runtime_requirements = _legacy_extra_runtime_requirements(
        path=path,
        legacy_assertion_ids=legacy_assertion_ids,
    )
    request_path = path
    generated_values: dict[str, GeneratedRuntimeValue] = {}
    if _legacy_extra_uses_invalid_account_id(path=path, legacy_assertion_ids=legacy_assertion_ids):
        request_path = path.replace("{AccountId}", "${generated.invalidAccountId}")
        generated_values["invalidAccountId"] = "invalid-resource-id"
        runtime_requirements = tuple(
            requirement for requirement in runtime_requirements if requirement.input_id != "consentedAccountId"
        )
    return _case(
        test_case_id,
        name=name,
        role="resource",
        compliance_scope=_legacy_scope(
            v31_ids=v31_ids,
            v40_ids=v40_ids,
            legacy_assertions=legacy_assertion_ids,
        ),
        endpoint_refs=(EndpointRef(method=method, path=endpoint_path),),
        required_capability_ids=(_legacy_extra_capability_id(endpoint_path),),
        dependencies=("ais-at-setup-token",),
        mandatory=True,
        request_method=method,
        request_path=request_path,
        runtime_requirements=runtime_requirements,
        assertions=_legacy_extra_assertions(
            legacy_assertion_ids=legacy_assertion_ids,
            case_name=name,
            method=method,
            path=path,
            v40_ids=v40_ids,
        ),
        generated_values=generated_values,
        permission_profile=_legacy_permission_profile(v31_ids=v31_ids, v40_ids=v40_ids),
        specification_versions=_specification_versions_for_legacy_ids(v31_ids, v40_ids),
    )


def _legacy_permission_profile(*, v31_ids: tuple[str, ...], v40_ids: tuple[str, ...]) -> _AisPermissionProfile:
    """Return the legacy AIS permission profile for a script group.

    Args:
        v31_ids: Legacy v3.1 AIS manifest script identifiers in the group.
        v40_ids: Legacy v4.0 AIS manifest script identifiers in the group.

    Returns:
        ``"detail"`` when any grouped legacy script used a detail-only AIS
        permission set, otherwise ``"basic"``.
    """
    if (
        _AIS_DETAIL_PERMISSION_LEGACY_IDS.intersection(v31_ids)
        or _AIS_DETAIL_PERMISSION_LEGACY_IDS.intersection(v40_ids)
    ):
        return "detail"
    return "basic"


def _legacy_extra_uses_invalid_account_id(
    *,
    path: str,
    legacy_assertion_ids: tuple[str, ...],
) -> bool:
    """Return whether a legacy AIS case should substitute an invalid account id.

    Args:
        path: Standards path represented by the legacy case.
        legacy_assertion_ids: Legacy assertion ids attached to the case.

    Returns:
        ``True`` when the case targets an account-scoped negative path that
        expects a field-invalid or access-denied outcome rather than a valid
        consented-account response.
    """
    if "{AccountId}" not in path:
        return False
    if "OB3GLOAssertOn200" in legacy_assertion_ids:
        return False
    if "OB3GLOAssertOn404" in legacy_assertion_ids:
        return False
    if "OB3GLOAssertOn401" in legacy_assertion_ids:
        return False
    return "OB3GLOAssertOn400" in legacy_assertion_ids or "OB3GLOAssertOn403" in legacy_assertion_ids


def _legacy_extra_endpoint_path(path: str) -> str:
    """Return the canonical endpoint path for AIS legacy applicability.

    Args:
        path: Legacy manifest request path.

    Returns:
        Canonical endpoint path without a trailing slash unless it is the root path.
    """
    normalized = "/" + "/".join(segment for segment in path.split("/") if segment)
    return "/" if normalized == "/" else normalized.rstrip("/")


def _legacy_extra_capability_id(path: str) -> str:
    """Build a required AIS capability id for a legacy endpoint.

    Args:
        path: Standards endpoint path.

    Returns:
        Stable capability id for the endpoint.
    """
    suffix = path.removeprefix(f"{_AIS_BASE_PATH}/").strip("/")
    suffix = suffix.replace("{AccountId}", "account-id")
    suffix = suffix.replace("/", ".").replace("_", "-")
    return f"ais.legacy.{suffix}.core"


def _legacy_extra_capabilities() -> tuple[EndpointCapability, ...]:
    """Build required capabilities for legacy AIS endpoint families.

    Returns:
        De-duplicated endpoint capabilities for the generated legacy cases.
    """
    capabilities: list[EndpointCapability] = []
    seen_refs: set[EndpointRef] = set()
    seen_capability_ids: set[str] = set()
    for _test_case_id, _name, method, path, _v31_ids, _v40_ids, _assertions in _AIS_LEGACY_EXTRA_CASE_DATA:
        endpoint_path = _legacy_extra_endpoint_path(path)
        endpoint_ref = EndpointRef(method=method, path=endpoint_path)
        if endpoint_ref in seen_refs:
            continue
        seen_refs.add(endpoint_ref)
        capability_id = _legacy_extra_capability_id(endpoint_path)
        if capability_id in seen_capability_ids:
            capability_id = f"{capability_id}.{len(seen_capability_ids)}"
        seen_capability_ids.add(capability_id)
        capabilities.append(
            EndpointCapability(
                capability_id=capability_id,
                label=f"Legacy AIS coverage for {method} {endpoint_path.removeprefix(f'{_AIS_BASE_PATH}/')}",
                description="Baseline support for a legacy accounts-and-transactions endpoint family.",
                required=True,
                endpoint_refs=(endpoint_ref,),
            )
        )
    return tuple(capabilities)


_AIS_LEGACY_EXTRA_CAPABILITIES = _legacy_extra_capabilities()
"""Required capabilities for AIS legacy endpoint families added after the original slice."""

_AIS_LEGACY_EXTRA_CASES = tuple(_legacy_extra_case(data) for data in _AIS_LEGACY_EXTRA_CASE_DATA)
"""Generated AIS catalogue cases preserving full legacy manifest script coverage."""


AIS_ACCOUNTS_TRANSACTIONS_CATALOGUE = TestCatalogue(
    key=AIS_ACCOUNTS_TRANSACTIONS_CATALOGUE_KEY,
    catalogue_version=AIS_ACCOUNTS_TRANSACTIONS_CATALOGUE_VERSION,
    capabilities=(
        EndpointCapability(
            capability_id="ais.accounts.list.core",
            label="AIS accounts list baseline coverage",
            description="Baseline support for GET /open-banking/v4.0/aisp/accounts.",
            required=True,
            endpoint_refs=(_ACCOUNTS_ENDPOINT,),
        ),
        EndpointCapability(
            capability_id="ais.accounts.by-id.core",
            label="AIS account details baseline coverage",
            description="Baseline support for GET /open-banking/v4.0/aisp/accounts/{AccountId}.",
            required=True,
            endpoint_refs=(_ACCOUNT_BY_ID_ENDPOINT,),
        ),
        EndpointCapability(
            capability_id="ais.accounts.balances.core",
            label="AIS account balances baseline coverage",
            description="Baseline support for GET /open-banking/v4.0/aisp/accounts/{AccountId}/balances.",
            required=True,
            endpoint_refs=(_ACCOUNT_BALANCES_ENDPOINT,),
        ),
        EndpointCapability(
            capability_id="ais.accounts.transactions.core",
            label="AIS account transactions baseline coverage",
            description="Baseline support for GET /open-banking/v4.0/aisp/accounts/{AccountId}/transactions.",
            required=True,
            endpoint_refs=(_ACCOUNT_TRANSACTIONS_ENDPOINT,),
        ),
        EndpointCapability(
            capability_id="ais.transactions.list.core",
            label="AIS transactions list baseline coverage",
            description="Baseline support for GET /open-banking/v4.0/aisp/transactions.",
            required=True,
            endpoint_refs=(_TRANSACTIONS_ENDPOINT,),
        ),
        EndpointCapability(
            capability_id="ais.transactions.date-range-filtering",
            label="AIS transaction date-range filtering",
            description=(
                "Optional support for fromBookingDateTime and toBookingDateTime "
                "transaction filters on account and bulk transaction queries."
            ),
            required=False,
            endpoint_refs=(_ACCOUNT_TRANSACTIONS_ENDPOINT, _TRANSACTIONS_ENDPOINT),
        ),
    )
    + _AIS_LEGACY_EXTRA_CAPABILITIES,
    test_cases=tuple(
        _with_ais_strict_v4_parity(test_case)
        for test_case in (
            _case(
            "ais-at-setup-discovery",
            name="OpenID discovery for AIS AT preconditions",
            role="setup",
            compliance_scope=(
                "legacy-fcs-precondition:accounts-transactions-suite-bootstrap",
                "legacy-fcs-source:OpenBankingUK/conformance-suite@develop/manifests/ob_3.1_accounts_transactions_fca.json",
                "legacy-fcs-source:OpenBankingUK/conformance-suite@develop/manifests/ob_4.0_accounts_transactions_fca.json",
            ),
            endpoint_refs=(_ACCOUNTS_ENDPOINT,),
            required_capability_ids=(),
            dependencies=(),
            mandatory=True,
            request_method="GET",
            request_path="/.well-known/openid-configuration",
        ),
        _case(
            "ais-at-setup-consent",
            name="Account-access consent for AIS AT",
            role="consent",
            compliance_scope=(
                "legacy-fcs-precondition:account-access-consent",
                "legacy-fcs-source:OpenBankingUK/conformance-suite@develop/manifests/ob_3.1_accounts_transactions_fca.json",
                "legacy-fcs-source:OpenBankingUK/conformance-suite@develop/manifests/ob_4.0_accounts_transactions_fca.json",
            ),
            endpoint_refs=(_ACCOUNTS_ENDPOINT,),
            required_capability_ids=(),
            dependencies=("ais-at-setup-discovery",),
            mandatory=True,
            request_method="POST",
            request_path=f"{_AIS_BASE_PATH}/account-access-consents",
            runtime_requirements=(_RESOURCE_BASE_URL,),
            assertions=(
                _assertion(
                    "status-201",
                    "http_status",
                    "Consent creation returns HTTP 201",
                    {"expected": 201},
                ),
            ),
        ),
        _case(
            "ais-at-setup-token",
            name="Token acquisition for AIS AT resources",
            role="token",
            compliance_scope=(
                "legacy-fcs-precondition:resource-access-token",
                "legacy-fcs-source:OpenBankingUK/conformance-suite@develop/manifests/ob_3.1_accounts_transactions_fca.json",
                "legacy-fcs-source:OpenBankingUK/conformance-suite@develop/manifests/ob_4.0_accounts_transactions_fca.json",
            ),
            endpoint_refs=(_ACCOUNTS_ENDPOINT,),
            required_capability_ids=(),
            dependencies=("ais-at-setup-consent",),
            mandatory=True,
            request_method="POST",
            request_path="/oauth2/token",
            assertions=(
                _assertion(
                    "status-200",
                    "http_status",
                    "Token acquisition returns HTTP 200",
                    {"expected": 200},
                ),
            ),
        ),
        _case(
            "ais-at-accounts-list-200",
            name="List accounts returns HTTP 200 with FAPI headers",
            role="resource",
            compliance_scope=_legacy_scope(
                v31_ids=("OB-301-ACC-100300", "OB-313-ACC-000100"),
                v40_ids=("OB-400-ACC-100300",),
                legacy_assertions=(
                    "OB3GLOAssertOn200",
                    "OB3GLOFAPIHeader",
                    "AssertAllV3AccountDetailsNotPresent",
                    "AssertAllV4AccountDetailsNotPresent",
                ),
            ),
            endpoint_refs=(_ACCOUNTS_ENDPOINT,),
            required_capability_ids=("ais.accounts.list.core",),
            dependencies=("ais-at-setup-token",),
            mandatory=True,
            request_method="GET",
            request_path=f"{_AIS_BASE_PATH}/accounts",
            runtime_requirements=_COMMON_RESOURCE_RUNTIME_REQUIREMENTS,
            assertions=(
                _assertion("status-200", "http_status", "Accounts list returns HTTP 200", {"expected": 200}),
                _assertion(
                    "fapi-header",
                    "header",
                    "Accounts list response includes x-fapi-interaction-id",
                    {"name": "x-fapi-interaction-id", "rule": "present"},
                ),
                _assertion(
                    "permissions-filter",
                    "json_field",
                    "Accounts list honours basic-vs-detail permission filtering",
                    {
                        "fields": ["StatementFrequencyAndFormat", "Servicer", "Account"],
                        "path": "Data.Account",
                        "rule": "all_items_absent_fields",
                    },
                ),
            ),
        ),
        _case(
            "ais-at-accounts-list-detail-200",
            name="List accounts returns HTTP 200 with detail permission",
            role="resource",
            compliance_scope=_legacy_scope(
                v31_ids=("OB-301-ACC-100400",),
                v40_ids=("OB-400-ACC-100400",),
                legacy_assertions=("OB3GLOAssertOn200", "OB3GLOFAPIHeader"),
            ),
            endpoint_refs=(_ACCOUNTS_ENDPOINT,),
            required_capability_ids=("ais.accounts.list.core",),
            dependencies=("ais-at-setup-token",),
            mandatory=True,
            request_method="GET",
            request_path=f"{_AIS_BASE_PATH}/accounts",
            runtime_requirements=_COMMON_RESOURCE_RUNTIME_REQUIREMENTS,
            assertions=(
                _assertion("status-200", "http_status", "Accounts detail list returns HTTP 200", {"expected": 200}),
                _assertion(
                    "fapi-header",
                    "header",
                    "Accounts detail list response includes x-fapi-interaction-id",
                    {"name": "x-fapi-interaction-id", "rule": "present"},
                ),
            ),
            permission_profile="detail",
        ),
        _case(
            "ais-at-accounts-list-401",
            name="List accounts rejects invalid token with HTTP 401",
            role="security",
            compliance_scope=_legacy_scope(
                v31_ids=(),
                v40_ids=("OB-400-ACC-100600", "OB-400-ACC-101100"),
                legacy_assertions=("OB3GLOAssertOn401",),
            ),
            endpoint_refs=(_ACCOUNTS_ENDPOINT,),
            required_capability_ids=(),
            dependencies=("ais-at-setup-token",),
            mandatory=False,
            request_method="GET",
            request_path=f"{_AIS_BASE_PATH}/accounts",
            runtime_requirements=(_RESOURCE_BASE_URL, _INVALID_ACCESS_TOKEN),
            assertions=(
                _assertion(
                    "status-401",
                    "http_status",
                    "Accounts list invalid token returns HTTP 401",
                    {"expected": 401},
                ),
            ),
        ),
        _case(
            "ais-at-account-by-id-200",
            name="Account by id returns HTTP 200 with permission-aware data",
            role="resource",
            compliance_scope=_legacy_scope(
                v31_ids=("OB-301-ACC-100000",),
                v40_ids=("OB-400-ACC-100000",),
                legacy_assertions=(
                    "OB3GLOAssertOn200",
                    "OB3GLOFAPIHeader",
                    "AssertAllV3AccountDetailsNotPresent",
                    "AssertAllV4AccountDetailsNotPresent",
                ),
            ),
            endpoint_refs=(_ACCOUNT_BY_ID_ENDPOINT,),
            required_capability_ids=("ais.accounts.by-id.core",),
            dependencies=("ais-at-setup-token",),
            mandatory=True,
            request_method="GET",
            request_path=f"{_AIS_BASE_PATH}/accounts/{{AccountId}}",
            runtime_requirements=_ACCOUNT_RESOURCE_RUNTIME_REQUIREMENTS,
            assertions=(
                _assertion("status-200", "http_status", "Account by id returns HTTP 200", {"expected": 200}),
                _assertion(
                    "fapi-header",
                    "header",
                    "Account by id response includes x-fapi-interaction-id",
                    {"name": "x-fapi-interaction-id", "rule": "present"},
                ),
                _assertion(
                    "permissions-filter",
                    "json_field",
                    "Account by id honours basic-vs-detail permission filtering",
                    {
                        "fields": ["StatementFrequencyAndFormat", "Servicer", "Account"],
                        "path": "Data.Account",
                        "rule": "all_items_absent_fields",
                    },
                ),
            ),
        ),
        _case(
            "ais-at-account-by-id-detail-200",
            name="Account by id returns HTTP 200 with detail permission",
            role="resource",
            compliance_scope=_legacy_scope(
                v31_ids=("OB-301-ACC-100200",),
                v40_ids=("OB-400-ACC-100200",),
                legacy_assertions=("OB3GLOAssertOn200", "OB3GLOFAPIHeader"),
            ),
            endpoint_refs=(_ACCOUNT_BY_ID_ENDPOINT,),
            required_capability_ids=("ais.accounts.by-id.core",),
            dependencies=("ais-at-setup-token",),
            mandatory=True,
            request_method="GET",
            request_path=f"{_AIS_BASE_PATH}/accounts/{{AccountId}}",
            runtime_requirements=_ACCOUNT_RESOURCE_RUNTIME_REQUIREMENTS,
            assertions=(
                _assertion("status-200", "http_status", "Account by id detail returns HTTP 200", {"expected": 200}),
                _assertion(
                    "fapi-header",
                    "header",
                    "Account by id detail response includes x-fapi-interaction-id",
                    {"name": "x-fapi-interaction-id", "rule": "present"},
                ),
            ),
            permission_profile="detail",
        ),
        _case(
            "ais-at-account-by-id-401",
            name="Account by id rejects invalid token with HTTP 401",
            role="security",
            compliance_scope=_legacy_scope(
                v31_ids=(),
                v40_ids=("OB-400-ACC-100500", "OB-400-ACC-101000"),
                legacy_assertions=("OB3GLOAssertOn401",),
            ),
            endpoint_refs=(_ACCOUNT_BY_ID_ENDPOINT,),
            dependencies=("ais-at-setup-token",),
            mandatory=False,
            request_method="GET",
            request_path=f"{_AIS_BASE_PATH}/accounts/{{AccountId}}",
            runtime_requirements=(_RESOURCE_BASE_URL, _CONSENTED_ACCOUNT_ID, _INVALID_ACCESS_TOKEN),
            assertions=(
                _assertion(
                    "status-401",
                    "http_status",
                    "Account by id invalid token returns HTTP 401",
                    {"expected": 401},
                ),
            ),
        ),
        _case(
            "ais-at-account-by-id-playback",
            name="Account by id replays x-fapi-interaction-id",
            role="security",
            compliance_scope=_legacy_scope(
                v31_ids=("OB-301-ACC-100700", "OB-301-ACC-100800"),
                v40_ids=("OB-400-ACC-100700", "OB-400-ACC-100800"),
                legacy_assertions=("OB3GLOAssertFAPIPlayBack",),
            ),
            endpoint_refs=(_ACCOUNT_BY_ID_ENDPOINT,),
            required_capability_ids=(),
            dependencies=("ais-at-setup-token",),
            mandatory=False,
            request_method="GET",
            request_path=f"{_AIS_BASE_PATH}/accounts/{{AccountId}}",
            runtime_requirements=(
                _RESOURCE_BASE_URL,
                _ACCESS_TOKEN,
                _CONSENTED_ACCOUNT_ID,
            ),
            assertions=(
                _assertion(
                    "fapi-playback",
                    "header",
                    "Account by id response replays x-fapi-interaction-id",
                    {"name": "x-fapi-interaction-id", "rule": "playback"},
                ),
            ),
            permission_profile="detail",
        ),
        _case(
            "ais-at-account-by-id-404",
            name="Account by id invalid subresource returns HTTP 404",
            role="security",
            compliance_scope=_legacy_scope(
                v31_ids=("OB-301-ACC-101101",),
                v40_ids=("OB-400-ACC-101101",),
                legacy_assertions=("OB3GLOAssertOn404",),
            ),
            endpoint_refs=(_ACCOUNT_BY_ID_ENDPOINT,),
            required_capability_ids=(),
            dependencies=("ais-at-setup-token",),
            mandatory=False,
            request_method="GET",
            request_path=f"{_AIS_BASE_PATH}/accounts/{{AccountId}}/foobar",
            runtime_requirements=(
                _RESOURCE_BASE_URL,
                _ACCESS_TOKEN,
                _CONSENTED_ACCOUNT_ID,
            ),
            assertions=(
                _assertion(
                    "status-404",
                    "http_status",
                    "Invalid account subresource returns HTTP 404",
                    {"expected": 404},
                ),
            ),
        ),
        _case(
            "ais-at-account-balances-200",
            name="Account balances returns HTTP 200 with FAPI headers",
            role="resource",
            compliance_scope=_legacy_scope(
                v31_ids=("OB-301-BAL-101200",),
                v40_ids=("OB-400-BAL-101200",),
                legacy_assertions=("OB3GLOAssertOn200", "OB3GLOFAPIHeader"),
            ),
            endpoint_refs=(_ACCOUNT_BALANCES_ENDPOINT,),
            required_capability_ids=("ais.accounts.balances.core",),
            dependencies=("ais-at-setup-token",),
            mandatory=True,
            request_method="GET",
            request_path=f"{_AIS_BASE_PATH}/accounts/{{AccountId}}/balances",
            runtime_requirements=_ACCOUNT_RESOURCE_RUNTIME_REQUIREMENTS,
            assertions=(
                _assertion("status-200", "http_status", "Account balances returns HTTP 200", {"expected": 200}),
                _assertion(
                    "fapi-header",
                    "header",
                    "Account balances response includes x-fapi-interaction-id",
                    {"name": "x-fapi-interaction-id", "rule": "present"},
                ),
            ),
        ),
        _case(
            "ais-at-account-balances-playback",
            name="Account balances replays x-fapi-interaction-id",
            role="security",
            compliance_scope=_legacy_scope(
                v31_ids=("OB-301-BAL-101702",),
                v40_ids=("OB-400-BAL-101702",),
                legacy_assertions=("OB3GLOAssertFAPIPlayBack",),
            ),
            endpoint_refs=(_ACCOUNT_BALANCES_ENDPOINT,),
            required_capability_ids=(),
            dependencies=("ais-at-setup-token",),
            mandatory=False,
            request_method="GET",
            request_path=f"{_AIS_BASE_PATH}/accounts/{{AccountId}}/balances",
            runtime_requirements=(
                _RESOURCE_BASE_URL,
                _ACCESS_TOKEN,
                _CONSENTED_ACCOUNT_ID,
            ),
            assertions=(
                _assertion(
                    "fapi-playback",
                    "header",
                    "Account balances response replays x-fapi-interaction-id",
                    {"name": "x-fapi-interaction-id", "rule": "playback"},
                ),
            ),
        ),
        _case(
            "ais-at-account-balances-404",
            name="Account balances invalid subresource returns HTTP 404",
            role="security",
            compliance_scope=_legacy_scope(
                v31_ids=("OB-301-BAL-101701",),
                v40_ids=("OB-400-BAL-101701",),
                legacy_assertions=("OB3GLOAssertOn404",),
            ),
            endpoint_refs=(_ACCOUNT_BALANCES_ENDPOINT,),
            required_capability_ids=(),
            dependencies=("ais-at-setup-token",),
            mandatory=False,
            request_method="GET",
            request_path=f"{_AIS_BASE_PATH}/accounts/{{AccountId}}/balances/foobar",
            runtime_requirements=(_RESOURCE_BASE_URL, _ACCESS_TOKEN, _CONSENTED_ACCOUNT_ID),
            assertions=(
                _assertion(
                    "status-404",
                    "http_status",
                    "Invalid account balances subresource returns HTTP 404",
                    {"expected": 404},
                ),
            ),
        ),
        _case(
            "ais-at-account-transactions-200",
            name="Account transactions returns HTTP 200 with permission-aware data",
            role="resource",
            compliance_scope=_legacy_scope(
                v31_ids=("OB-301-TRA-105000",),
                v40_ids=("OB-400-TRA-105000",),
                legacy_assertions=(
                    "OB3GLOAssertOn200",
                    "OB3GLOFAPIHeader",
                    "AssertAllV3TransactionDetailsNotPresent",
                    "AssertAllV4TransactionDetailsNotPresent",
                ),
            ),
            endpoint_refs=(_ACCOUNT_TRANSACTIONS_ENDPOINT,),
            required_capability_ids=("ais.accounts.transactions.core", "ais.transactions.date-range-filtering"),
            dependencies=("ais-at-setup-token",),
            mandatory=True,
            request_method="GET",
            request_path=f"{_AIS_BASE_PATH}/accounts/{{AccountId}}/transactions",
            runtime_requirements=(
                _RESOURCE_BASE_URL,
                _ACCESS_TOKEN,
                _CONSENTED_ACCOUNT_ID,
                _FROM_BOOKING_DATE_TIME,
                _TO_BOOKING_DATE_TIME,
            ),
            assertions=(
                _assertion("status-200", "http_status", "Account transactions returns HTTP 200", {"expected": 200}),
                _assertion(
                    "fapi-header",
                    "header",
                    "Account transactions response includes x-fapi-interaction-id",
                    {"name": "x-fapi-interaction-id", "rule": "present"},
                ),
                _assertion(
                    "permissions-filter",
                    "json_field",
                    "Account transactions honours permission-based data filtering",
                    {
                        "fields": [
                            "TransactionInformation",
                            "Balance",
                            "MerchantDetails",
                            "CreditorAgent",
                            "CreditorAccount",
                            "UltimateCreditor",
                            "DebtorAgent",
                            "DebtorAccount",
                            "UltimateDebtor",
                        ],
                        "path": "Data.Transaction",
                        "rule": "all_items_absent_fields",
                    },
                ),
            ),
        ),
        _case(
            "ais-at-account-transactions-detail-200",
            name="Account transactions returns HTTP 200 with detail permission",
            role="resource",
            compliance_scope=_legacy_scope(
                v31_ids=("OB-301-TRA-105100", "OB-301-TRA-105110", "OB-301-TRA-105120"),
                v40_ids=("OB-400-TRA-105100", "OB-400-TRA-105110", "OB-400-TRA-105120"),
                legacy_assertions=("OB3GLOAssertOn200", "OB3GLOFAPIHeader"),
            ),
            endpoint_refs=(_ACCOUNT_TRANSACTIONS_ENDPOINT,),
            required_capability_ids=("ais.accounts.transactions.core", "ais.transactions.date-range-filtering"),
            dependencies=("ais-at-setup-token",),
            mandatory=True,
            request_method="GET",
            request_path=f"{_AIS_BASE_PATH}/accounts/{{AccountId}}/transactions",
            runtime_requirements=(
                _RESOURCE_BASE_URL,
                _ACCESS_TOKEN,
                _CONSENTED_ACCOUNT_ID,
                _FROM_BOOKING_DATE_TIME,
                _TO_BOOKING_DATE_TIME,
            ),
            assertions=(
                _assertion(
                    "status-200",
                    "http_status",
                    "Account transactions detail returns HTTP 200",
                    {"expected": 200},
                ),
                _assertion(
                    "fapi-header",
                    "header",
                    "Account transactions detail response includes x-fapi-interaction-id",
                    {"name": "x-fapi-interaction-id", "rule": "present"},
                ),
            ),
            permission_profile="detail",
        ),
        _case(
            "ais-at-account-transactions-401",
            name="Account transactions rejects invalid token with HTTP 401",
            role="security",
            compliance_scope=_legacy_scope(
                v31_ids=("OB-301-TRA-105300",),
                v40_ids=("OB-400-TRA-105300",),
                legacy_assertions=("OB3GLOAssertOn401",),
            ),
            endpoint_refs=(_ACCOUNT_TRANSACTIONS_ENDPOINT,),
            required_capability_ids=(),
            dependencies=("ais-at-setup-token",),
            mandatory=False,
            request_method="GET",
            request_path=f"{_AIS_BASE_PATH}/accounts/{{AccountId}}/transactions",
            runtime_requirements=(_RESOURCE_BASE_URL, _CONSENTED_ACCOUNT_ID, _INVALID_ACCESS_TOKEN),
            assertions=(
                _assertion(
                    "status-401",
                    "http_status",
                    "Account transactions invalid token returns HTTP 401",
                    {"expected": 401},
                ),
            ),
        ),
        _case(
            "ais-at-account-transactions-playback",
            name="Account transactions replays x-fapi-interaction-id",
            role="security",
            compliance_scope=_legacy_scope(
                v31_ids=("OB-301-TRA-105500",),
                v40_ids=("OB-400-TRA-105500",),
                legacy_assertions=("OB3GLOAssertFAPIPlayBack",),
            ),
            endpoint_refs=(_ACCOUNT_TRANSACTIONS_ENDPOINT,),
            required_capability_ids=(),
            dependencies=("ais-at-setup-token",),
            mandatory=False,
            request_method="GET",
            request_path=f"{_AIS_BASE_PATH}/accounts/{{AccountId}}/transactions",
            runtime_requirements=(_RESOURCE_BASE_URL, _ACCESS_TOKEN, _CONSENTED_ACCOUNT_ID),
            assertions=(
                _assertion(
                    "fapi-playback",
                    "header",
                    "Account transactions response replays x-fapi-interaction-id",
                    {"name": "x-fapi-interaction-id", "rule": "playback"},
                ),
            ),
        ),
        _case(
            "ais-at-transactions-list-200",
            name="Transactions list returns HTTP 200 with permission-aware data",
            role="resource",
            compliance_scope=_legacy_scope(
                v31_ids=("OB-301-TRA-105200", "OB-301-TRA-101200"),
                v40_ids=("OB-400-TRA-105200", "OB-400-TRA-101200"),
                legacy_assertions=(
                    "OB3GLOAssertOn200",
                    "OB3GLOFAPIHeader",
                    "AssertAllV3TransactionDetailsNotPresent",
                    "AssertAllV4TransactionDetailsNotPresent",
                ),
            ),
            endpoint_refs=(_TRANSACTIONS_ENDPOINT,),
            required_capability_ids=("ais.transactions.list.core", "ais.transactions.date-range-filtering"),
            dependencies=("ais-at-setup-token",),
            mandatory=True,
            request_method="GET",
            request_path=f"{_AIS_BASE_PATH}/transactions",
            runtime_requirements=(
                _RESOURCE_BASE_URL,
                _ACCESS_TOKEN,
                _FROM_BOOKING_DATE_TIME,
                _TO_BOOKING_DATE_TIME,
            ),
            assertions=(
                _assertion("status-200", "http_status", "Transactions list returns HTTP 200", {"expected": 200}),
                _assertion(
                    "fapi-header",
                    "header",
                    "Transactions list response includes x-fapi-interaction-id",
                    {"name": "x-fapi-interaction-id", "rule": "present"},
                ),
                _assertion(
                    "permissions-filter",
                    "json_field",
                    "Transactions list honours permission-based data filtering",
                    {
                        "fields": [
                            "TransactionInformation",
                            "Balance",
                            "MerchantDetails",
                            "CreditorAgent",
                            "CreditorAccount",
                            "UltimateCreditor",
                            "DebtorAgent",
                            "DebtorAccount",
                            "UltimateDebtor",
                        ],
                        "path": "Data.Transaction",
                        "rule": "all_items_absent_fields",
                    },
                ),
            ),
        ),
        _case(
            "ais-at-transactions-list-401",
            name="Transactions list rejects invalid token with HTTP 401",
            role="security",
            compliance_scope=_legacy_scope(
                v31_ids=("OB-301-TRA-105400",),
                v40_ids=("OB-400-TRA-105400",),
                legacy_assertions=("OB3GLOAssertOn401",),
            ),
            endpoint_refs=(_TRANSACTIONS_ENDPOINT,),
            required_capability_ids=(),
            dependencies=("ais-at-setup-token",),
            mandatory=False,
            request_method="GET",
            request_path=f"{_AIS_BASE_PATH}/transactions",
            runtime_requirements=(_RESOURCE_BASE_URL, _INVALID_ACCESS_TOKEN),
            assertions=(
                _assertion(
                    "status-401",
                    "http_status",
                    "Transactions list invalid token returns HTTP 401",
                    {"expected": 401},
                ),
            ),
        ),
        _case(
            "ais-at-transactions-list-playback",
            name="Transactions list replays x-fapi-interaction-id",
            role="security",
            compliance_scope=_legacy_scope(
                v31_ids=("OB-301-TRA-105600",),
                v40_ids=("OB-400-TRA-105600",),
                legacy_assertions=("OB3GLOAssertFAPIPlayBack",),
            ),
            endpoint_refs=(_TRANSACTIONS_ENDPOINT,),
            required_capability_ids=(),
            dependencies=("ais-at-setup-token",),
            mandatory=False,
            request_method="GET",
            request_path=f"{_AIS_BASE_PATH}/transactions",
            runtime_requirements=(_RESOURCE_BASE_URL, _ACCESS_TOKEN),
            assertions=(
                _assertion(
                    "fapi-playback",
                    "header",
                    "Transactions list response replays x-fapi-interaction-id",
                    {"name": "x-fapi-interaction-id", "rule": "playback"},
                ),
            ),
        ),
        _case(
            "ais-at-invalid-base-endpoint-404",
            name="Invalid AIS base endpoint returns HTTP 404",
            role="security",
            compliance_scope=_legacy_scope(
                v31_ids=("OB-301-ACC-001000",),
                v40_ids=("OB-400-ACC-001000",),
                legacy_assertions=("OB3GLOAssertOn404",),
            ),
            endpoint_refs=(_ACCOUNTS_ENDPOINT,),
            required_capability_ids=(),
            dependencies=("ais-at-setup-token",),
            mandatory=False,
            request_method="GET",
            request_path=f"{_AIS_BASE_PATH}/foobar",
            runtime_requirements=_COMMON_RESOURCE_RUNTIME_REQUIREMENTS,
            assertions=(
                _assertion(
                    "status-404",
                    "http_status",
                    "Invalid AIS base endpoint returns HTTP 404",
                    {"expected": 404},
                ),
            ),
        ),
        )
        + _AIS_LEGACY_EXTRA_CASES
    ),
)
"""Imported AIS accounts-and-transactions catalogue for legacy FCS coverage."""


def get_ais_accounts_transactions_catalogue() -> TestCatalogue:
    """Return the imported AIS accounts-and-transactions catalogue.

    Returns:
        The bundled legacy-derived AIS accounts-and-transactions catalogue.
    """
    return AIS_ACCOUNTS_TRANSACTIONS_CATALOGUE
