"""AIS catalogue entries derived from legacy FCS accounts-and-transactions coverage."""

from __future__ import annotations

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

AIS_ACCOUNTS_TRANSACTIONS_CATALOGUE_KEY = CatalogueKey(standard="open-banking", version="v4.0", api="ais")
"""Catalogue boundary for AIS accounts-and-transactions legacy FCS import."""

AIS_ACCOUNTS_TRANSACTIONS_CATALOGUE_VERSION = "2026.07.legacy-fcs-ais-at.1"
"""Content version for the imported AIS accounts-and-transactions catalogue."""

_AIS_BASE_PATH = "/open-banking/v4.0/aisp"
_FAPI1_ADVANCED_ONLY = SecurityProfileApplicability(profiles=("fapi1-advanced",))

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
    source="fixture",
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
    source="fixture",
)
_TO_BOOKING_DATE_TIME = RuntimeInputRequirement(
    input_id="toBookingDateTime",
    input_type="string",
    label="Optional transaction to-booking-date-time filter",
    required=False,
    source="fixture",
)

_COMMON_RESOURCE_RUNTIME_REQUIREMENTS = (_RESOURCE_BASE_URL, _ACCESS_TOKEN)
_ACCOUNT_RESOURCE_RUNTIME_REQUIREMENTS = (_RESOURCE_BASE_URL, _ACCESS_TOKEN, _CONSENTED_ACCOUNT_ID)
_AIS_RESOURCE_AUTH_ID = "ais-account-access"
"""Semantic authorization id for AIS resource API requests."""

_AIS_FIXTURE_ACCOUNT_ID = "fixture-account-id"
"""Stable catalogue-owned account id used by account-scoped AIS fixtures."""


def _applicability(
    *endpoint_refs: EndpointRef,
    required_capability_ids: tuple[str, ...] = (),
) -> TestCaseApplicability:
    """Build profile and endpoint applicability for imported AIS cases.

    Args:
        *endpoint_refs: Endpoints that must be implemented for the case to apply.
        required_capability_ids: Endpoint capability ids that must be selected
            before the case becomes directly applicable.

    Returns:
        Applicability constrained to legacy FCS FCA profile coverage.
    """
    return TestCaseApplicability(
        security_profiles=_FAPI1_ADVANCED_ONLY,
        endpoint_refs=endpoint_refs,
        required_capability_ids=required_capability_ids,
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
    rule: dict[str, str | int],
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

    Returns:
        A fully populated ``CatalogueTestCase`` ready for compilation.
    """
    is_open_banking_api_request = request_path.startswith(_AIS_BASE_PATH)
    runtime_input_refs = tuple(
        requirement.input_id for requirement in runtime_requirements if requirement.source == "plan"
    )
    request_path = request_path.replace("{AccountId}", _AIS_FIXTURE_ACCOUNT_ID)
    request_headers = open_banking_request_headers_for() if is_open_banking_api_request else ()
    required_token_id = (
        _AIS_RESOURCE_AUTH_ID
        if _ACCESS_TOKEN in runtime_requirements and _INVALID_ACCESS_TOKEN not in runtime_requirements
        else None
    )
    produced_token_id = _AIS_RESOURCE_AUTH_ID if role == "token" else None
    generated_values: dict[str, GeneratedRuntimeValue] = (
        {"invalidAccessToken": "invalid-access-token"} if _INVALID_ACCESS_TOKEN in runtime_requirements else {}
    )
    return CatalogueTestCase(
        test_case_id=test_case_id,
        name=name,
        role=role,
        compliance_scope=compliance_scope,
        applicability=_applicability(*endpoint_refs, required_capability_ids=required_capability_ids),
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
                generated_values=generated_values,
                required_token_id=required_token_id,
                produced_token_id=produced_token_id,
            ),
        ),
        assertions=assertions,
    )


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
    ),
    test_cases=(
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
        ),
        _case(
            "ais-at-accounts-list-200",
            name="List accounts returns HTTP 200 with FAPI headers",
            role="resource",
            compliance_scope=_legacy_scope(
                v31_ids=("OB-301-ACC-100300", "OB-301-ACC-100400", "OB-313-ACC-000100"),
                v40_ids=("OB-400-ACC-100300", "OB-400-ACC-100400"),
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
                    {"path": "Data.Account", "rule": "permission_filtered"},
                ),
            ),
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
                v31_ids=("OB-301-ACC-100000", "OB-301-ACC-100200"),
                v40_ids=("OB-400-ACC-100000", "OB-400-ACC-100200"),
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
                    {"path": "Data.Account.0", "rule": "permission_filtered"},
                ),
            ),
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
                v31_ids=("OB-301-TRA-105000", "OB-301-TRA-105100", "OB-301-TRA-105110", "OB-301-TRA-105120"),
                v40_ids=("OB-400-TRA-105000", "OB-400-TRA-105100", "OB-400-TRA-105110", "OB-400-TRA-105120"),
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
                    {"path": "Data.Transaction", "rule": "permission_filtered"},
                ),
            ),
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
                    {"path": "Data.Transaction", "rule": "permission_filtered"},
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
    ),
)
"""Imported AIS accounts-and-transactions catalogue for legacy FCS coverage."""


def get_ais_accounts_transactions_catalogue() -> TestCatalogue:
    """Return the imported AIS accounts-and-transactions catalogue.

    Returns:
        The bundled legacy-derived AIS accounts-and-transactions catalogue.
    """
    return AIS_ACCOUNTS_TRANSACTIONS_CATALOGUE
