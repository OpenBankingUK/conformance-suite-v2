"""Unit tests for the legacy-derived AIS accounts-and-transactions catalogue."""

from __future__ import annotations

import pytest

from conformance.catalogue import CatalogueKey, ImplementedEndpoint, TestPlanSpec, compile_test_plan
from conformance.catalogues.ais import (
    AIS_ACCOUNTS_TRANSACTIONS_CATALOGUE_KEY,
    AIS_ACCOUNTS_TRANSACTIONS_CATALOGUE_VERSION,
    get_ais_accounts_transactions_catalogue,
)
from conformance.json_types import JsonValue

LEGACY_AIS_V31_SCRIPT_IDS = (
    "OB-301-ACC-001000",
    "OB-301-ACC-100000",
    "OB-301-ACC-100200",
    "OB-301-ACC-100300",
    "OB-301-ACC-100400",
    "OB-301-ACC-100500",
    "OB-301-ACC-100600",
    "OB-301-ACC-100700",
    "OB-301-ACC-100800",
    "OB-301-ACC-101000",
    "OB-301-ACC-101100",
    "OB-301-ACC-101101",
    "OB-301-BAL-101200",
    "OB-301-BAL-101300",
    "OB-301-BAL-101400",
    "OB-301-BAL-101500",
    "OB-301-BAL-101600",
    "OB-301-BAL-101700",
    "OB-301-BAL-101701",
    "OB-301-BAL-101702",
    "OB-301-BAL-101703",
    "OB-301-BEN-101800",
    "OB-301-BEN-101900",
    "OB-301-BEN-102000",
    "OB-301-BEN-102100",
    "OB-301-BEN-102200",
    "OB-301-BEN-102201",
    "OB-301-BEN-102203",
    "OB-301-BEN-102204",
    "OB-301-BEN-102205",
    "OB-301-DIR-102300",
    "OB-301-DIR-102400",
    "OB-301-DIR-102500",
    "OB-301-DIR-102501",
    "OB-301-DIR-102502",
    "OB-301-DIR-102503",
    "OB-301-DIR-102504",
    "OB-301-OFF-102600",
    "OB-301-OFF-102700",
    "OB-301-OFF-102800",
    "OB-301-OFF-102801",
    "OB-301-OFF-102802",
    "OB-301-OFF-102803",
    "OB-301-OFF-102804",
    "OB-301-PAR-102900",
    "OB-301-PAR-102901",
    "OB-301-PAR-102902",
    "OB-301-PAR-103000",
    "OB-301-PAR-103100",
    "OB-301-PAR-103101",
    "OB-301-PAR-103102",
    "OB-301-PAR-103103",
    "OB-301-PAR-103104",
    "OB-301-PAR-103105",
    "OB-301-PAR-103106",
    "OB-301-PAR-103107",
    "OB-301-PAR-103108",
    "OB-301-PRO-103200",
    "OB-301-PRO-103300",
    "OB-301-PRO-103400",
    "OB-301-PRO-103401",
    "OB-301-PRO-102802",
    "OB-301-PRO-103402",
    "OB-301-PRO-103403",
    "OB-301-SCP-103500",
    "OB-301-SCP-103600",
    "OB-301-SCP-103700",
    "OB-301-SCP-103701",
    "OB-301-SCP-103702",
    "OB-301-SCP-103703",
    "OB-301-SCP-103704",
    "OB-301-STO-103800",
    "OB-301-STO-103900",
    "OB-301-STO-103901",
    "OB-301-STO-104000",
    "OB-301-STO-104100",
    "OB-301-STO-104101",
    "OB-301-STO-104102",
    "OB-301-STO-104103",
    "OB-301-TRA-105000",
    "OB-301-TRA-105100",
    "OB-301-TRA-105110",
    "OB-301-TRA-105120",
    "OB-301-TRA-105200",
    "OB-301-TRA-101200",
    "OB-301-TRA-105300",
    "OB-301-TRA-105400",
    "OB-301-TRA-105500",
    "OB-301-TRA-105600",
    "OB-301-TRA-105700",
    "OB-301-STA-105900",
    "OB-301-STA-106000",
    "OB-301-STA-106100",
    "OB-301-STA-106200",
    "OB-301-STA-106300",
    "OB-313-ACC-000100",
)
"""Legacy v3.1 AIS accounts-and-transactions manifest script IDs."""

LEGACY_AIS_V40_SCRIPT_IDS = (
    "OB-400-ACC-001000",
    "OB-400-ACC-100000",
    "OB-400-ACC-100200",
    "OB-400-ACC-100300",
    "OB-400-ACC-100400",
    "OB-400-ACC-100500",
    "OB-400-ACC-100600",
    "OB-400-ACC-100700",
    "OB-400-ACC-100800",
    "OB-400-ACC-101000",
    "OB-400-ACC-101100",
    "OB-400-ACC-101101",
    "OB-400-BAL-101200",
    "OB-400-BAL-101300",
    "OB-400-BAL-101400",
    "OB-400-BAL-101500",
    "OB-400-BAL-101600",
    "OB-400-BAL-101700",
    "OB-400-BAL-101701",
    "OB-400-BAL-101702",
    "OB-400-BAL-101703",
    "OB-400-BEN-101800",
    "OB-400-BEN-101900",
    "OB-400-BEN-102000",
    "OB-400-BEN-102100",
    "OB-400-BEN-102200",
    "OB-400-BEN-102201",
    "OB-400-BEN-102203",
    "OB-400-BEN-102204",
    "OB-400-BEN-102205",
    "OB-400-DIR-102300",
    "OB-400-DIR-102400",
    "OB-400-DIR-102500",
    "OB-400-DIR-102501",
    "OB-400-DIR-102502",
    "OB-400-DIR-102503",
    "OB-400-DIR-102504",
    "OB-400-OFF-102600",
    "OB-400-OFF-102700",
    "OB-400-OFF-102800",
    "OB-400-OFF-102801",
    "OB-400-OFF-102802",
    "OB-400-OFF-102803",
    "OB-400-OFF-102804",
    "OB-400-PAR-102900",
    "OB-400-PAR-102901",
    "OB-400-PAR-102902",
    "OB-400-PAR-103000",
    "OB-400-PAR-103100",
    "OB-400-PAR-103101",
    "OB-400-PAR-103102",
    "OB-400-PAR-103103",
    "OB-400-PAR-103104",
    "OB-400-PAR-103105",
    "OB-400-PAR-103106",
    "OB-400-PAR-103107",
    "OB-400-PAR-103108",
    "OB-400-PRO-103200",
    "OB-400-PRO-103300",
    "OB-400-PRO-103400",
    "OB-400-PRO-103401",
    "OB-400-PRO-102802",
    "OB-400-PRO-103402",
    "OB-400-PRO-103403",
    "OB-400-SCP-103500",
    "OB-400-SCP-103600",
    "OB-400-SCP-103700",
    "OB-400-SCP-103701",
    "OB-400-SCP-103702",
    "OB-400-SCP-103703",
    "OB-400-SCP-103704",
    "OB-400-STO-103800",
    "OB-400-STO-103900",
    "OB-400-STO-103901",
    "OB-400-STO-104000",
    "OB-400-STO-104100",
    "OB-400-STO-104101",
    "OB-400-STO-104102",
    "OB-400-STO-104103",
    "OB-400-TRA-105000",
    "OB-400-TRA-105100",
    "OB-400-TRA-105110",
    "OB-400-TRA-105120",
    "OB-400-TRA-105200",
    "OB-400-TRA-101200",
    "OB-400-TRA-105300",
    "OB-400-TRA-105400",
    "OB-400-TRA-105500",
    "OB-400-TRA-105600",
    "OB-400-TRA-105700",
    "OB-400-STA-105900",
    "OB-400-STA-106000",
    "OB-400-STA-106100",
    "OB-400-STA-106200",
    "OB-400-STA-106300",
)
"""Legacy v4.0 AIS accounts-and-transactions manifest script IDs."""


def _spec(
    *,
    endpoints: tuple[ImplementedEndpoint, ...],
    runtime_inputs: dict[str, JsonValue] | None = None,
    specification_version: str | None = None,
) -> TestPlanSpec:
    return TestPlanSpec(
        schema_version="v1",
        catalogue_key=AIS_ACCOUNTS_TRANSACTIONS_CATALOGUE_KEY,
        security_profile="fapi1-advanced",
        implemented_endpoints=endpoints,
        runtime_inputs={} if runtime_inputs is None else runtime_inputs,
        specification_version=specification_version,
    )


def _legacy_script_ids(version: str) -> tuple[str, ...]:
    """Return AIS compliance-scope script ids for a legacy manifest version.

    Args:
        version: Legacy Open Banking Read/Write version, either ``"3.1"`` or ``"4.0"``.

    Returns:
        Legacy script ids claimed by the AIS catalogue for ``version``.
    """
    prefix = f"legacy-fcs-v{version}-ids:"
    catalogue = get_ais_accounts_transactions_catalogue()
    script_ids: list[str] = []
    for test_case in catalogue.test_cases:
        for scope in test_case.compliance_scope:
            if not scope.startswith(prefix):
                continue
            ids_blob = scope.removeprefix(prefix)
            if ids_blob != "none":
                script_ids.extend(ids_blob.split(","))
    return tuple(script_ids)


@pytest.mark.unit
def test_ais_catalogue_key_version_and_id_uniqueness() -> None:
    catalogue = get_ais_accounts_transactions_catalogue()

    assert catalogue.key == CatalogueKey(standard="open-banking", version="v4.0", api="ais")
    assert catalogue.catalogue_version == AIS_ACCOUNTS_TRANSACTIONS_CATALOGUE_VERSION
    case_ids = [case.test_case_id for case in catalogue.test_cases]
    assert len(case_ids) == len(set(case_ids))


@pytest.mark.unit
def test_compile_selects_ais_cases_for_implemented_endpoints_and_dependencies() -> None:
    """Select baseline AIS cases and optional date-range coverage together."""
    catalogue = get_ais_accounts_transactions_catalogue()
    compiled = compile_test_plan(
        catalogue,
        _spec(
            endpoints=(
                ImplementedEndpoint(
                    method="GET",
                    path="/open-banking/v4.0/aisp/accounts",
                    resource_group="Accounts",
                ),
                ImplementedEndpoint(
                    method="GET",
                    path="/open-banking/v4.0/aisp/accounts/{AccountId}/transactions",
                    resource_group="Transactions",
                    capability_ids=("ais.transactions.date-range-filtering",),
                ),
                ImplementedEndpoint(
                    method="GET",
                    path="/open-banking/v4.0/aisp/transactions",
                    resource_group="Transactions",
                    capability_ids=("ais.transactions.date-range-filtering",),
                ),
            ),
            runtime_inputs={"resourceBaseUrl": "https://rs.example.com", "consentedAccountId": "account-123"},
        ),
    )

    selected_ids = list(compiled.traceability.generated_test_case_ids)
    assert selected_ids.index("ais-at-setup-discovery") < selected_ids.index("ais-at-setup-consent")
    assert selected_ids.index("ais-at-setup-consent") < selected_ids.index("ais-at-setup-token")
    assert "ais-at-accounts-list-200" in selected_ids
    assert "ais-at-accounts-list-detail-200" in selected_ids
    assert "ais-at-account-transactions-200" in selected_ids
    assert "ais-at-account-transactions-detail-200" in selected_ids
    assert "ais-at-transactions-list-200" in selected_ids
    selected_capabilities = {
        selection.capability_id: selection for selection in compiled.traceability.selected_capabilities
    }
    assert selected_capabilities["ais.accounts.list.core"].required is True
    assert selected_capabilities["ais.accounts.transactions.core"].required is True
    assert selected_capabilities["ais.transactions.list.core"].required is True
    assert selected_capabilities["ais.transactions.date-range-filtering"].required is False


@pytest.mark.unit
def test_compile_excludes_optional_ais_transaction_cases_when_date_range_capability_is_omitted() -> None:
    """Exclude implementation-dependent AIS transaction cases without capability selection."""
    catalogue = get_ais_accounts_transactions_catalogue()
    compiled = compile_test_plan(
        catalogue,
        _spec(
            endpoints=(
                ImplementedEndpoint(
                    method="GET",
                    path="/open-banking/v4.0/aisp/accounts",
                    resource_group="Accounts",
                ),
                ImplementedEndpoint(
                    method="GET",
                    path="/open-banking/v4.0/aisp/accounts/{AccountId}/transactions",
                    resource_group="Transactions",
                ),
                ImplementedEndpoint(
                    method="GET",
                    path="/open-banking/v4.0/aisp/transactions",
                    resource_group="Transactions",
                ),
            ),
            runtime_inputs={"resourceBaseUrl": "https://rs.example.com", "consentedAccountId": "account-123"},
        ),
    )

    selected_ids = list(compiled.traceability.generated_test_case_ids)
    assert "ais-at-accounts-list-200" in selected_ids
    assert "ais-at-accounts-list-detail-200" in selected_ids
    assert "ais-at-account-transactions-200" not in selected_ids
    assert "ais-at-account-transactions-detail-200" not in selected_ids
    assert "ais-at-transactions-list-200" not in selected_ids
    decisions = {decision.test_case_id: decision for decision in compiled.traceability.applicability_decisions}
    assert decisions["ais-at-account-transactions-200"].reason == (
        "required capability not selected: ais.transactions.date-range-filtering"
    )
    assert decisions["ais-at-account-transactions-detail-200"].reason == (
        "required capability not selected: ais.transactions.date-range-filtering"
    )
    assert decisions["ais-at-transactions-list-200"].reason == (
        "required capability not selected: ais.transactions.date-range-filtering"
    )


@pytest.mark.unit
def test_compile_preserves_ais_generated_ids_and_legacy_provenance() -> None:
    """Keep generated AIS ids and legacy compliance scopes parity-safe."""
    catalogue = get_ais_accounts_transactions_catalogue()
    compiled = compile_test_plan(
        catalogue,
        _spec(
            endpoints=(
                ImplementedEndpoint(
                    method="GET",
                    path="/open-banking/v4.0/aisp/accounts",
                    resource_group="Accounts",
                ),
                ImplementedEndpoint(
                    method="GET",
                    path="/open-banking/v4.0/aisp/accounts/{AccountId}/transactions",
                    resource_group="Transactions",
                    capability_ids=("ais.transactions.date-range-filtering",),
                ),
                ImplementedEndpoint(
                    method="GET",
                    path="/open-banking/v4.0/aisp/transactions",
                    resource_group="Transactions",
                    capability_ids=("ais.transactions.date-range-filtering",),
                ),
            ),
            runtime_inputs={"resourceBaseUrl": "https://rs.example.com", "consentedAccountId": "account-123"},
        ),
    )

    generated_ids = compiled.traceability.generated_test_case_ids
    assert generated_ids.index("ais-at-setup-discovery") < generated_ids.index("ais-at-setup-token")
    assert generated_ids.index("ais-at-setup-token") < generated_ids.index("ais-at-account-transactions-200")
    assert generated_ids.index("ais-at-account-transactions-200") < generated_ids.index("ais-at-transactions-list-200")
    case_map = {case.test_case_id: case for case in catalogue.test_cases}
    legacy_source = (
        "legacy-fcs-source:OpenBankingUK/conformance-suite@develop/manifests/ob_3.1_accounts_transactions_fca.json"
    )
    account_transactions_scope = case_map["ais-at-account-transactions-200"].compliance_scope
    assert legacy_source in account_transactions_scope
    assert any("OB-301-TRA-105000" in scope for scope in account_transactions_scope)
    assert any(
        "OB-301-TRA-105100" in scope for scope in case_map["ais-at-account-transactions-detail-200"].compliance_scope
    )
    assert any("OB-400-TRA-105200" in scope for scope in case_map["ais-at-transactions-list-200"].compliance_scope)


@pytest.mark.unit
def test_compile_surfaces_runtime_input_requirements_for_selected_ais_cases() -> None:
    catalogue = get_ais_accounts_transactions_catalogue()
    compiled = compile_test_plan(
        catalogue,
        _spec(
            endpoints=(
                ImplementedEndpoint(
                    method="GET",
                    path="/open-banking/v4.0/aisp/accounts/{AccountId}",
                    resource_group="Accounts",
                ),
            ),
            runtime_inputs={"resourceBaseUrl": "https://rs.example.com", "consentedAccountId": "account-123"},
        ),
    )

    snapshot = {entry.input_id: entry for entry in compiled.traceability.runtime_input_snapshot}
    assert snapshot["resourceBaseUrl"].provided is True
    assert snapshot["resourceBaseUrl"].value == "https://rs.example.com"
    assert snapshot["accessToken"].provided is False
    assert snapshot["accessToken"].sensitive is True
    assert snapshot["accessToken"].value is None
    assert snapshot["consentedAccountId"].provided is True
    assert snapshot["consentedAccountId"].value == "account-123"
    assert snapshot["invalidAccessToken"].required is False
    assert snapshot["invalidAccessToken"].provided is False
    assert "xFapiAuthDate" not in snapshot
    assert "xFapiCustomerIpAddress" not in snapshot
    assert "xCustomerUserAgent" not in snapshot
    assert "xFapiInteractionId" not in snapshot


@pytest.mark.unit
def test_ais_resource_cases_bind_to_legacy_permission_profile_tokens() -> None:
    catalogue = get_ais_accounts_transactions_catalogue()
    compiled = compile_test_plan(
        catalogue,
        _spec(
            endpoints=(
                ImplementedEndpoint(
                    method="GET",
                    path="/open-banking/v4.0/aisp/accounts/{AccountId}",
                    resource_group="Accounts",
                ),
                ImplementedEndpoint(
                    method="GET",
                    path="/open-banking/v4.0/aisp/accounts/{AccountId}/scheduled-payments",
                    resource_group="ScheduledPayments",
                ),
            ),
            runtime_inputs={"resourceBaseUrl": "https://rs.example.com", "consentedAccountId": "account-123"},
        ),
    )

    step_by_case = {case.test_case_id: case.request_steps[0] for case in compiled.test_cases}
    assert step_by_case["ais-at-account-by-id-200"].required_token_id == "ais-account-access-basic"  # noqa: S105 - semantic token id
    assert step_by_case["ais-at-account-by-id-200"].authorization_profile == "basic"
    assert (
        step_by_case["ais-at-account-by-id-detail-200"].required_token_id == "ais-account-access-detail"  # noqa: S105 - semantic token id
    )
    assert step_by_case["ais-at-account-by-id-detail-200"].authorization_profile == "detail"
    assert (
        step_by_case["ais-at-legacy-scheduled-payment-scp-103500"].required_token_id == "ais-account-access-detail"  # noqa: S105 - semantic token id
    )
    assert step_by_case["ais-at-legacy-scheduled-payment-scp-103500"].authorization_profile == "detail"


@pytest.mark.unit
def test_compile_legacy_ais_invalid_account_cases_use_generated_account_id() -> None:
    catalogue = get_ais_accounts_transactions_catalogue()
    compiled = compile_test_plan(
        catalogue,
        _spec(
            endpoints=(
                ImplementedEndpoint(
                    method="GET",
                    path="/open-banking/v4.0/aisp/accounts/{AccountId}/balances",
                    resource_group="Balances",
                ),
            ),
            runtime_inputs={"resourceBaseUrl": "https://rs.example.com", "consentedAccountId": "account-123"},
        ),
    )

    invalid_case = next(case for case in compiled.test_cases if case.test_case_id == "ais-at-legacy-balance-bal-101600")
    request_step = invalid_case.request_steps[0]
    assert request_step.path == "/open-banking/v4.0/aisp/accounts/${generated.invalidAccountId}/balances"
    assert request_step.generated_values["invalidAccountId"] == "invalid-resource-id"
    assert "consentedAccountId" not in request_step.runtime_input_refs
    assert invalid_case.assertions[0].rule["expectedOneOf"] == [400, 403]


@pytest.mark.unit
def test_legacy_ais_product_playback_uses_canonical_products_endpoint() -> None:
    """Treat the legacy singular Product URI as covered by canonical bulk Products."""
    catalogue = get_ais_accounts_transactions_catalogue()
    compiled = compile_test_plan(
        catalogue,
        _spec(
            endpoints=(
                ImplementedEndpoint(
                    method="GET",
                    path="/open-banking/v4.0/aisp/products",
                    resource_group="Products",
                ),
            ),
            runtime_inputs={"resourceBaseUrl": "https://rs.example.com"},
        ),
    )

    product_case = next(case for case in compiled.test_cases if case.test_case_id == "ais-at-legacy-product-pro-103403")
    assert product_case.request_steps[0].path == "/open-banking/v4.0/aisp/products"
    assert {endpoint.path for endpoint in product_case.applicability.endpoint_refs} == {
        "/open-banking/v4.0/aisp/products"
    }
    assert any("legacy-fcs-v4.0-ids:OB-400-PRO-103403" in scope for scope in product_case.compliance_scope)


@pytest.mark.unit
def test_legacy_ais_product_endpoint_typo_in_imported_plan_is_canonicalised() -> None:
    """Keep exported plans containing the old singular Product URI importable."""
    catalogue = get_ais_accounts_transactions_catalogue()
    compiled = compile_test_plan(
        catalogue,
        _spec(
            endpoints=(
                ImplementedEndpoint(
                    method="GET",
                    path="/open-banking/v4.0/aisp/product",
                    resource_group="account-and-transaction",
                ),
            ),
            runtime_inputs={"resourceBaseUrl": "https://rs.example.com"},
        ),
    )

    product_case = next(case for case in compiled.test_cases if case.test_case_id == "ais-at-legacy-product-pro-103403")
    assert product_case.request_steps[0].path == "/open-banking/v4.0/aisp/products"


@pytest.mark.unit
def test_ais_catalogue_scopes_trace_back_to_legacy_31_and_40_manifests() -> None:
    catalogue = get_ais_accounts_transactions_catalogue()

    scope_blob = "\n".join(scope for case in catalogue.test_cases for scope in case.compliance_scope)
    assert "ob_3.1_accounts_transactions_fca.json" in scope_blob
    assert "ob_4.0_accounts_transactions_fca.json" in scope_blob
    assert "OB-301-ACC-100000" in scope_blob
    assert "OB-400-TRA-105000" in scope_blob


@pytest.mark.unit
def test_ais_catalogue_covers_all_legacy_manifest_scripts() -> None:
    v31_script_ids = _legacy_script_ids("3.1")
    v40_script_ids = _legacy_script_ids("4.0")

    assert len(v31_script_ids) == len(set(v31_script_ids))
    assert len(v40_script_ids) == len(set(v40_script_ids))
    assert set(v31_script_ids) == set(LEGACY_AIS_V31_SCRIPT_IDS)
    assert set(v40_script_ids) == set(LEGACY_AIS_V40_SCRIPT_IDS)


@pytest.mark.unit
def test_compile_selects_legacy_ais_resource_family_cases() -> None:
    catalogue = get_ais_accounts_transactions_catalogue()
    compiled = compile_test_plan(
        catalogue,
        _spec(
            endpoints=(
                ImplementedEndpoint(
                    method="GET",
                    path="/open-banking/v4.0/aisp/standing-orders",
                    resource_group="StandingOrders",
                ),
                ImplementedEndpoint(
                    method="GET",
                    path="/open-banking/v4.0/aisp/statements",
                    resource_group="Statements",
                ),
            ),
            runtime_inputs={"resourceBaseUrl": "https://rs.example.com", "consentedAccountId": "account-123"},
        ),
    )

    selected_ids = set(compiled.traceability.generated_test_case_ids)
    assert "ais-at-legacy-standing-order-sto-103900" in selected_ids
    assert "ais-at-legacy-standing-order-sto-104103" in selected_ids
    assert "ais-at-legacy-statement-sta-105900" in selected_ids
    assert "ais-at-legacy-statement-sta-106300" in selected_ids


@pytest.mark.unit
def test_compile_v4_filters_ais_v3_only_variants_and_keeps_schema_checks() -> None:
    """v4 AIS execution excludes v3-only legacy cases and emits schema checks."""
    catalogue = get_ais_accounts_transactions_catalogue()
    compiled = compile_test_plan(
        catalogue,
        _spec(
            endpoints=(
                ImplementedEndpoint(
                    method="GET",
                    path="/open-banking/v4.0/aisp/accounts/{AccountId}",
                    resource_group="Accounts",
                ),
            ),
            runtime_inputs={"resourceBaseUrl": "https://rs.example.com", "consentedAccountId": "account-123"},
            specification_version="4.0.1",
        ),
    )

    selected_ids = set(compiled.traceability.generated_test_case_ids)
    account_case = next(case for case in compiled.test_cases if case.test_case_id == "ais-at-account-by-id-200")

    assert "ais-at-legacy-account-acc-100500" not in selected_ids
    assert "ais-at-legacy-account-acc-100600" not in selected_ids
    assert any(assertion.kind == "response_schema" for assertion in account_case.assertions)


@pytest.mark.unit
def test_legacy_ais_trailing_slash_case_uses_canonical_beneficiaries_endpoint() -> None:
    """Keep legacy trailing-slash requests executable without duplicating scope endpoints."""
    catalogue = get_ais_accounts_transactions_catalogue()
    compiled = compile_test_plan(
        catalogue,
        _spec(
            endpoints=(
                ImplementedEndpoint(
                    method="GET",
                    path="/open-banking/v4.0/aisp/accounts/{AccountId}/beneficiaries",
                    resource_group="Beneficiaries",
                ),
            ),
            runtime_inputs={"resourceBaseUrl": "https://rs.example.com", "consentedAccountId": "account-123"},
        ),
    )

    selected_ids = set(compiled.traceability.generated_test_case_ids)
    playback_case = next(
        case for case in catalogue.test_cases if case.test_case_id == "ais-at-legacy-beneficiary-ben-102204"
    )

    assert "ais-at-legacy-beneficiary-ben-101800" in selected_ids
    assert "ais-at-legacy-beneficiary-ben-102204" in selected_ids
    assert playback_case.request_steps[0].path == "/open-banking/v4.0/aisp/accounts/{AccountId}/beneficiaries/"
    assert {endpoint.path for endpoint in playback_case.applicability.endpoint_refs} == {
        "/open-banking/v4.0/aisp/accounts/{AccountId}/beneficiaries"
    }
