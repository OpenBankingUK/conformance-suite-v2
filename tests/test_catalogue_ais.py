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


def _spec(
    *,
    endpoints: tuple[ImplementedEndpoint, ...],
    runtime_inputs: dict[str, JsonValue] | None = None,
) -> TestPlanSpec:
    return TestPlanSpec(
        schema_version="v1",
        catalogue_key=AIS_ACCOUNTS_TRANSACTIONS_CATALOGUE_KEY,
        security_profile="fapi1-advanced",
        implemented_endpoints=endpoints,
        runtime_inputs={} if runtime_inputs is None else runtime_inputs,
    )


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
            runtime_inputs={"resourceBaseUrl": "https://rs.example.com"},
        ),
    )

    selected_ids = list(compiled.traceability.generated_test_case_ids)
    assert selected_ids.index("ais-at-setup-discovery") < selected_ids.index("ais-at-setup-consent")
    assert selected_ids.index("ais-at-setup-consent") < selected_ids.index("ais-at-setup-token")
    assert "ais-at-accounts-list-200" in selected_ids
    assert "ais-at-account-transactions-200" in selected_ids
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
            runtime_inputs={"resourceBaseUrl": "https://rs.example.com"},
        ),
    )

    selected_ids = list(compiled.traceability.generated_test_case_ids)
    assert "ais-at-accounts-list-200" in selected_ids
    assert "ais-at-account-transactions-200" not in selected_ids
    assert "ais-at-transactions-list-200" not in selected_ids
    decisions = {decision.test_case_id: decision for decision in compiled.traceability.applicability_decisions}
    assert decisions["ais-at-account-transactions-200"].reason == (
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
            runtime_inputs={"resourceBaseUrl": "https://rs.example.com"},
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
            runtime_inputs={"resourceBaseUrl": "https://rs.example.com"},
        ),
    )

    snapshot = {entry.input_id: entry for entry in compiled.traceability.runtime_input_snapshot}
    assert snapshot["resourceBaseUrl"].provided is True
    assert snapshot["resourceBaseUrl"].value == "https://rs.example.com"
    assert snapshot["accessToken"].provided is False
    assert snapshot["accessToken"].sensitive is True
    assert snapshot["accessToken"].value is None
    assert snapshot["consentedAccountId"].provided is False
    assert snapshot["invalidAccessToken"].required is False
    assert snapshot["invalidAccessToken"].provided is False
    assert "xFapiAuthDate" not in snapshot
    assert "xFapiCustomerIpAddress" not in snapshot
    assert "xCustomerUserAgent" not in snapshot
    assert "xFapiInteractionId" not in snapshot


@pytest.mark.unit
def test_ais_catalogue_scopes_trace_back_to_legacy_31_and_40_manifests() -> None:
    catalogue = get_ais_accounts_transactions_catalogue()

    scope_blob = "\n".join(scope for case in catalogue.test_cases for scope in case.compliance_scope)
    assert "ob_3.1_accounts_transactions_fca.json" in scope_blob
    assert "ob_4.0_accounts_transactions_fca.json" in scope_blob
    assert "OB-301-ACC-100000" in scope_blob
    assert "OB-400-TRA-105000" in scope_blob
