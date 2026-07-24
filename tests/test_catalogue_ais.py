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
            ),
            runtime_inputs={
                "resourceBaseUrl": "https://rs.example.com",
                "accessToken": "access-token",
                "consentedAccountId": "account-123",
            },
        ),
    )

    selected_ids = list(compiled.traceability.generated_test_case_ids)
    assert selected_ids.index("ais-at-setup-discovery") < selected_ids.index("ais-at-setup-consent")
    assert selected_ids.index("ais-at-setup-consent") < selected_ids.index("ais-at-setup-token")
    assert "ais-at-accounts-list-200" in selected_ids
    assert "ais-at-account-transactions-200" in selected_ids
    assert "ais-at-transactions-list-200" not in selected_ids


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
            runtime_inputs={
                "resourceBaseUrl": "https://rs.example.com",
                "accessToken": "access-token",
                "consentedAccountId": "account-123",
            },
        ),
    )

    snapshot = {entry.input_id: entry for entry in compiled.traceability.runtime_input_snapshot}
    assert snapshot["resourceBaseUrl"].provided is True
    assert snapshot["resourceBaseUrl"].value == "https://rs.example.com"
    assert snapshot["accessToken"].sensitive is True
    assert snapshot["accessToken"].value is None
    assert snapshot["invalidAccessToken"].required is False
    assert snapshot["invalidAccessToken"].provided is False
    assert snapshot["xFapiInteractionId"].required is False


@pytest.mark.unit
def test_ais_catalogue_scopes_trace_back_to_legacy_31_and_40_manifests() -> None:
    catalogue = get_ais_accounts_transactions_catalogue()

    scope_blob = "\n".join(scope for case in catalogue.test_cases for scope in case.compliance_scope)
    assert "ob_3.1_accounts_transactions_fca.json" in scope_blob
    assert "ob_4.0_accounts_transactions_fca.json" in scope_blob
    assert "OB-301-ACC-100000" in scope_blob
    assert "OB-400-TRA-105000" in scope_blob
