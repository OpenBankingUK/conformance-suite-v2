"""Builder wizard runtime, business, and visibility configuration derived from scope."""

from __future__ import annotations

from collections.abc import Mapping

import pytest
from django.contrib.sessions.backends.signed_cookies import SessionStore

from conformance.api.builder_draft_store import SessionBuilderDraftStore
from conformance.api.builder_wizard import (
    BusinessConfigForm,
    ConfigVisibility,
    business_config_form_initial,
    catalogue_scope_hierarchy,
    config_visibility_for_draft,
)
from conformance.catalogue import PlanDocumentBoundary
from conformance.json_types import JsonObject

pytestmark = pytest.mark.unit

DISCOVERY_CONFIG = {"discoveryUrl": "https://example.com/.well-known/openid-configuration"}
"""Minimal discovery config needed to build canonical draft documents."""


def test_config_visibility_uses_selected_endpoint_apis() -> None:
    """The grouped config page visibility follows selected endpoint API families."""
    draft = (
        SessionBuilderDraftStore(SessionStore())
        .create()
        .with_catalogue_boundary(
            scheme="open-banking-uk",
            specification="read-write",
            version="4.0.1",
        )
    )
    boundary = PlanDocumentBoundary("open-banking-uk", "read-write", "4.0.1")
    hierarchy = catalogue_scope_hierarchy(
        boundary,
        selected_resource_group_ids=("account-and-transaction", "payment-initiation", "confirmation-of-funds"),
    )
    ais_endpoint = next(
        endpoint
        for group in hierarchy.resource_groups
        for endpoint in group.endpoints
        if endpoint.path == "/open-banking/v4.0/aisp/accounts"
    )
    draft = draft.with_scope_selection(
        resource_group_ids=("account-and-transaction", "payment-initiation", "confirmation-of-funds"),
        endpoint_ids=(ais_endpoint.id,),
        endpoint_capability_ids={},
    ).with_config(
        config=DISCOVERY_CONFIG,
    )

    visibility = config_visibility_for_draft(draft)

    assert visibility.selected_api_ids == frozenset({"ais"})
    assert visibility.show_ais is True
    assert visibility.show_pis is False
    assert visibility.show_cbpii is False
    assert visibility.show_business_defaults is True
    assert visibility.ais_account_id_required is False


def test_config_visibility_requires_ais_account_id_for_account_scoped_endpoints() -> None:
    """AIS account-scoped endpoint selections mark the account id as required."""
    draft = (
        SessionBuilderDraftStore(SessionStore())
        .create()
        .with_catalogue_boundary(
            scheme="open-banking-uk",
            specification="read-write",
            version="4.0.1",
        )
    )
    boundary = PlanDocumentBoundary("open-banking-uk", "read-write", "4.0.1")
    hierarchy = catalogue_scope_hierarchy(
        boundary,
        selected_resource_group_ids=("account-and-transaction",),
    )
    balances_endpoint = next(
        endpoint
        for group in hierarchy.resource_groups
        for endpoint in group.endpoints
        if endpoint.path == "/open-banking/v4.0/aisp/accounts/{AccountId}/balances"
    )
    draft = draft.with_scope_selection(
        resource_group_ids=("account-and-transaction",),
        endpoint_ids=(balances_endpoint.id,),
        endpoint_capability_ids={},
    ).with_config(
        config=DISCOVERY_CONFIG,
    )

    visibility = config_visibility_for_draft(draft)
    missing_form = BusinessConfigForm(data={}, config_visibility=visibility)
    advanced_form = BusinessConfigForm(
        data={"ais_resource_ids_json": '{"accountIds": [{"accountId": "account-123"}]}'},
        config_visibility=visibility,
    )

    assert visibility.ais_account_id_required is True
    assert missing_form.is_valid() is False
    assert "required for selected account-scoped AIS endpoints" in missing_form.errors["ais_consented_account_id"][0]
    assert advanced_form.is_valid(), advanced_form.errors.as_json()
    assert advanced_form.config == {"ais": {"resourceIds": {"accountIds": [{"accountId": "account-123"}]}}}


def test_config_visibility_restores_cbpii_business_defaults() -> None:
    """CBPII endpoint selections show the Confirmation of Funds business fields."""
    draft = (
        SessionBuilderDraftStore(SessionStore())
        .create()
        .with_catalogue_boundary(
            scheme="open-banking-uk",
            specification="read-write",
            version="4.0.1",
        )
    )
    boundary = PlanDocumentBoundary("open-banking-uk", "read-write", "4.0.1")
    hierarchy = catalogue_scope_hierarchy(
        boundary,
        selected_resource_group_ids=("confirmation-of-funds",),
    )
    cbpii_endpoint = next(
        endpoint
        for group in hierarchy.resource_groups
        for endpoint in group.endpoints
        if endpoint.path == "/open-banking/v4.0/cbpii/funds-confirmation-consents"
    )
    draft = draft.with_scope_selection(
        resource_group_ids=("confirmation-of-funds",),
        endpoint_ids=(cbpii_endpoint.id,),
        endpoint_capability_ids={},
    ).with_config(config=DISCOVERY_CONFIG)

    visibility = config_visibility_for_draft(draft)

    assert visibility.selected_api_ids == frozenset({"cbpii"})
    assert visibility.show_ais is False
    assert visibility.show_pis is False
    assert visibility.show_cbpii is True
    assert visibility.show_business_defaults is True


def test_config_visibility_classifies_v311_pisp_vrp_paths_as_vrp() -> None:
    """Show only VRP business fields for v3.1 VRP resources under ``pisp``."""
    boundary = PlanDocumentBoundary("open-banking-uk", "read-write", "3.1.11")
    hierarchy = catalogue_scope_hierarchy(
        boundary,
        selected_resource_group_ids=("variable-recurring-payments",),
    )
    vrp_endpoint = next(
        endpoint
        for group in hierarchy.resource_groups
        for endpoint in group.endpoints
        if endpoint.method == "POST" and endpoint.path == "/open-banking/v3.1/pisp/domestic-vrp-consents"
    )
    draft = (
        SessionBuilderDraftStore(SessionStore())
        .create()
        .with_catalogue_boundary(
            scheme="open-banking-uk",
            specification="read-write",
            version="3.1.11",
        )
        .with_scope_selection(
            resource_group_ids=("variable-recurring-payments",),
            endpoint_ids=(vrp_endpoint.id,),
            endpoint_capability_ids={},
        )
        .with_config(config=DISCOVERY_CONFIG)
    )

    visibility = config_visibility_for_draft(draft)
    form = BusinessConfigForm(data={}, config_visibility=visibility)

    assert visibility.selected_api_ids == frozenset({"vrp"})
    assert visibility.show_ais is False
    assert visibility.show_pis is False
    assert visibility.show_cbpii is False
    assert visibility.show_vrp is True
    assert visibility.show_business_defaults is True
    assert form.is_valid() is False
    assert "vrp_creditor_account_scheme_name" in form.errors
    assert "pis_creditor_account_scheme_name" not in form.errors


def test_config_visibility_restores_pis_business_defaults() -> None:
    """PIS endpoint selections show payment business defaults."""
    draft = (
        SessionBuilderDraftStore(SessionStore())
        .create()
        .with_catalogue_boundary(
            scheme="open-banking-uk",
            specification="read-write",
            version="4.0.1",
        )
    )
    boundary = PlanDocumentBoundary("open-banking-uk", "read-write", "4.0.1")
    hierarchy = catalogue_scope_hierarchy(
        boundary,
        selected_resource_group_ids=("payment-initiation",),
    )
    pis_endpoint = next(
        endpoint
        for group in hierarchy.resource_groups
        for endpoint in group.endpoints
        if endpoint.path == "/open-banking/v4.0/pisp/domestic-payments"
    )
    draft = draft.with_scope_selection(
        resource_group_ids=("payment-initiation",),
        endpoint_ids=(pis_endpoint.id,),
        endpoint_capability_ids={},
    ).with_config(config=DISCOVERY_CONFIG)

    visibility = config_visibility_for_draft(draft)

    assert visibility.selected_api_ids == frozenset({"pis"})
    assert visibility.show_ais is False
    assert visibility.show_pis is True
    assert visibility.show_cbpii is False
    assert visibility.show_business_defaults is True
    assert visibility.pis_domestic_creditor_account_required is True
    assert visibility.pis_instructed_amount_required is True
    assert visibility.pis_international_creditor_account_required is False
    assert visibility.pis_requested_execution_date_time_required is False


def test_config_visibility_marks_all_pis_business_inputs_for_all_pis_endpoints() -> None:
    """Selecting all PIS endpoints marks every PIS product-family input required."""
    draft = (
        SessionBuilderDraftStore(SessionStore())
        .create()
        .with_catalogue_boundary(
            scheme="open-banking-uk",
            specification="read-write",
            version="4.0.1",
        )
    )
    boundary = PlanDocumentBoundary("open-banking-uk", "read-write", "4.0.1")
    hierarchy = catalogue_scope_hierarchy(
        boundary,
        selected_resource_group_ids=("payment-initiation",),
    )
    endpoint_ids = tuple(endpoint.id for group in hierarchy.resource_groups for endpoint in group.endpoints)
    draft = draft.with_scope_selection(
        resource_group_ids=("payment-initiation",),
        endpoint_ids=endpoint_ids,
        endpoint_capability_ids={},
    ).with_config(config=DISCOVERY_CONFIG)

    visibility = config_visibility_for_draft(draft)

    assert visibility.show_pis is True
    assert visibility.pis_domestic_creditor_account_required is True
    assert visibility.pis_international_creditor_account_required is True
    assert visibility.pis_instructed_amount_required is True
    assert visibility.pis_currency_of_transfer_required is True
    assert visibility.pis_requested_execution_date_time_required is True
    assert visibility.pis_first_payment_date_time_required is True
    assert visibility.pis_standing_order_frequency_required is True


def test_business_config_form_requires_cbpii_debtor_account_values() -> None:
    """CBPII business config serializes debtor account values into structured config."""
    form = BusinessConfigForm(
        data={
            "cbpii_debtor_account_scheme_name": "UK.OBIE.SortCodeAccountNumber",
            "cbpii_debtor_account_identification": "12345678901234",
            "cbpii_debtor_account_name": "Model Bank Account",
        },
        config_visibility=ConfigVisibility(
            selected_api_ids=frozenset({"cbpii"}),
            show_ais=False,
            show_pis=False,
            show_cbpii=True,
            show_vrp=False,
            show_business_defaults=True,
        ),
    )

    assert form.is_valid(), form.errors.as_json()
    assert form.config == {
        "cbpii": {
            "debtorAccount": {
                "schemeName": "UK.OBIE.SortCodeAccountNumber",
                "identification": "12345678901234",
                "name": "Model Bank Account",
            }
        }
    }


def test_business_config_form_requires_vrp_business_values() -> None:
    """VRP business config serializes creditor, amount, and validity defaults."""
    visibility = ConfigVisibility(
        selected_api_ids=frozenset({"vrp"}),
        show_ais=False,
        show_pis=False,
        show_cbpii=False,
        show_vrp=True,
        show_business_defaults=True,
    )
    missing_form = BusinessConfigForm(data={}, config_visibility=visibility)
    form = BusinessConfigForm(
        data={
            "vrp_creditor_account_scheme_name": "UK.OBIE.SortCodeAccountNumber",
            "vrp_creditor_account_identification": "70000170000002",
            "vrp_creditor_account_name": "VRP creditor",
            "vrp_instructed_amount_amount": "1.00",
            "vrp_instructed_amount_currency": "GBP",
            "vrp_valid_from_date_time": "2026-08-27T00:00:00+00:00",
            "vrp_valid_to_date_time": "2026-09-27T00:00:00+00:00",
        },
        config_visibility=visibility,
    )

    assert missing_form.is_valid() is False
    assert form.is_valid(), form.errors.as_json()
    assert form.config == {
        "vrp": {
            "validFromDateTime": "2026-08-27T00:00:00+00:00",
            "validToDateTime": "2026-09-27T00:00:00+00:00",
            "creditorAccount": {
                "schemeName": "UK.OBIE.SortCodeAccountNumber",
                "identification": "70000170000002",
                "name": "VRP creditor",
            },
            "instructedAmount": {"amount": "1.00", "currency": "GBP"},
        }
    }


def test_business_config_form_requires_selected_pis_values() -> None:
    """PIS validation requires selected product-family payment defaults."""
    visibility = ConfigVisibility(
        selected_api_ids=frozenset({"pis"}),
        show_ais=False,
        show_pis=True,
        show_cbpii=False,
        show_vrp=False,
        show_business_defaults=True,
        pis_domestic_creditor_account_required=True,
        pis_instructed_amount_required=True,
    )

    missing_form = BusinessConfigForm(data={}, config_visibility=visibility)
    json_fallback_form = BusinessConfigForm(
        data={
            "pis_creditor_account_json": (
                '{"schemeName": "UK.OBIE.SortCodeAccountNumber", '
                '"identification": "12345678901234", "name": "Model Bank Account"}'
            ),
            "pis_instructed_amount_json": '{"amount": "10.00", "currency": "GBP"}',
        },
        config_visibility=visibility,
    )

    assert missing_form.is_valid() is False
    assert "required for selected PIS endpoints" in missing_form.errors["pis_creditor_account_scheme_name"][0]
    assert "required for selected PIS endpoints" in missing_form.errors["pis_instructed_amount_amount"][0]
    assert json_fallback_form.is_valid(), json_fallback_form.errors.as_json()
    assert json_fallback_form.config == {
        "pis": {
            "creditorAccount": {
                "schemeName": "UK.OBIE.SortCodeAccountNumber",
                "identification": "12345678901234",
                "name": "Model Bank Account",
            },
            "instructedAmount": {"amount": "10.00", "currency": "GBP"},
        }
    }


def test_business_config_form_requires_only_selected_pis_family() -> None:
    """International PIS requiredness does not require domestic-only fields."""
    visibility = ConfigVisibility(
        selected_api_ids=frozenset({"pis"}),
        show_ais=False,
        show_pis=True,
        show_cbpii=False,
        show_vrp=False,
        show_business_defaults=True,
        pis_international_creditor_account_required=True,
        pis_instructed_amount_required=True,
        pis_currency_of_transfer_required=True,
    )
    form = BusinessConfigForm(
        data={
            "pis_international_creditor_account_scheme_name": "UK.OBIE.IBAN",
            "pis_international_creditor_account_identification": "GB29NWBK60161331926819",
            "pis_international_creditor_account_name": "International Creditor",
            "pis_instructed_amount_amount": "10.00",
            "pis_instructed_amount_currency": "GBP",
            "pis_currency_of_transfer": "GBP",
        },
        config_visibility=visibility,
    )

    assert form.is_valid(), form.errors.as_json()
    assert form.config == {
        "pis": {
            "currencyOfTransfer": "GBP",
            "internationalCreditorAccount": {
                "schemeName": "UK.OBIE.IBAN",
                "identification": "GB29NWBK60161331926819",
                "name": "International Creditor",
            },
            "instructedAmount": {"amount": "10.00", "currency": "GBP"},
        }
    }


CBPII_VISIBILITY = ConfigVisibility(
    selected_api_ids=frozenset({"cbpii"}),
    show_ais=False,
    show_pis=False,
    show_cbpii=True,
    show_vrp=False,
    show_business_defaults=True,
)
"""Visibility exposing only the CBPII business-default fields."""

IMPORTED_CBPII_CONFIG: JsonObject = {
    "cbpii": {
        "debtorAccount": {
            "schemeName": "UK.OBIE.SortCodeAccountNumber",
            "identification": "10000109010102",
            "name": "Imported Account",
            "secondaryIdentification": "ROLL-1",
        }
    }
}
"""Imported CBPII debtor account carrying a key the friendly fields cannot express."""


def _resubmitted_business_data(
    initial: Mapping[str, object],
    **overrides: str,
) -> dict[str, object]:
    """Simulate a browser resubmitting every pre-filled business field.

    Args:
        initial: Form initial values rendered into the page.
        overrides: Field values the participant edited.

    Returns:
        POST data equivalent to the rendered form with the given edits applied.
    """
    data: dict[str, object] = {key: "" if value is None else value for key, value in initial.items()}
    data.update(overrides)
    return data


def test_business_config_form_friendly_edit_overrides_prefilled_json() -> None:
    """An edited friendly field wins over the pre-filled advanced JSON fallback."""
    initial = business_config_form_initial(IMPORTED_CBPII_CONFIG)
    form = BusinessConfigForm(
        data=_resubmitted_business_data(initial, cbpii_debtor_account_identification="100002"),
        initial=initial,
        config_visibility=CBPII_VISIBILITY,
    )

    assert form.is_valid(), form.errors.as_json()
    assert form.config == {
        "cbpii": {
            "debtorAccount": {
                "schemeName": "UK.OBIE.SortCodeAccountNumber",
                "identification": "100002",
                "name": "Imported Account",
                "secondaryIdentification": "ROLL-1",
            }
        }
    }


def test_business_config_form_unchanged_submission_preserves_imported_json() -> None:
    """Resubmitting an imported config without edits leaves the JSON fallback intact."""
    initial = business_config_form_initial(IMPORTED_CBPII_CONFIG)
    form = BusinessConfigForm(
        data=_resubmitted_business_data(initial),
        initial=initial,
        config_visibility=CBPII_VISIBILITY,
    )

    assert form.is_valid(), form.errors.as_json()
    assert form.config == IMPORTED_CBPII_CONFIG


def test_business_config_form_json_edit_alone_is_authoritative() -> None:
    """Editing only the advanced JSON keeps that value without friendly-field overlay."""
    initial = business_config_form_initial(IMPORTED_CBPII_CONFIG)
    form = BusinessConfigForm(
        data=_resubmitted_business_data(
            initial,
            cbpii_debtor_account_json='{"schemeName": "UK.OBIE.IBAN", "identification": "GB29NWBK60161331926819"}',
        ),
        initial=initial,
        config_visibility=CBPII_VISIBILITY,
    )

    assert form.is_valid(), form.errors.as_json()
    assert form.config == {
        "cbpii": {
            "debtorAccount": {
                "schemeName": "UK.OBIE.IBAN",
                "identification": "GB29NWBK60161331926819",
            }
        }
    }


def test_business_config_form_friendly_edit_overlays_edited_json() -> None:
    """When both inputs change, the friendly field overlays the edited JSON base."""
    initial = business_config_form_initial(IMPORTED_CBPII_CONFIG)
    form = BusinessConfigForm(
        data=_resubmitted_business_data(
            initial,
            cbpii_debtor_account_identification="100002",
            cbpii_debtor_account_json='{"schemeName": "UK.OBIE.IBAN", "identification": "GB29NWBK60161331926819"}',
        ),
        initial=initial,
        config_visibility=CBPII_VISIBILITY,
    )

    assert form.is_valid(), form.errors.as_json()
    assert form.config == {
        "cbpii": {
            "debtorAccount": {
                "schemeName": "UK.OBIE.IBAN",
                "identification": "100002",
            }
        }
    }


def test_business_config_form_pis_friendly_edit_overrides_prefilled_json() -> None:
    """PIS creditor account edits survive the pre-filled advanced JSON fallback."""
    config: JsonObject = {
        "pis": {
            "creditorAccount": {
                "schemeName": "UK.OBIE.SortCodeAccountNumber",
                "identification": "12345678901234",
                "name": "Model Bank Account",
                "secondaryIdentification": "ROLL-2",
            },
            "instructedAmount": {"amount": "10.00", "currency": "GBP"},
        }
    }
    visibility = ConfigVisibility(
        selected_api_ids=frozenset({"pis"}),
        show_ais=False,
        show_pis=True,
        show_cbpii=False,
        show_vrp=False,
        show_business_defaults=True,
    )
    initial = business_config_form_initial(config)
    form = BusinessConfigForm(
        data=_resubmitted_business_data(initial, pis_creditor_account_identification="99999999999999"),
        initial=initial,
        config_visibility=visibility,
    )

    assert form.is_valid(), form.errors.as_json()
    assert form.config == {
        "pis": {
            "creditorAccount": {
                "schemeName": "UK.OBIE.SortCodeAccountNumber",
                "identification": "99999999999999",
                "name": "Model Bank Account",
                "secondaryIdentification": "ROLL-2",
            },
            "instructedAmount": {"amount": "10.00", "currency": "GBP"},
        }
    }


def test_business_config_form_ais_account_edit_preserves_other_resource_ids() -> None:
    """An edited AIS account identifier replaces only the first consented account."""
    config: JsonObject = {
        "ais": {
            "resourceIds": {
                "accountIds": [{"accountId": "70000170000001"}, {"accountId": "70000170000002"}],
                "statementIds": [{"statementId": "140"}],
            }
        }
    }
    visibility = ConfigVisibility(
        selected_api_ids=frozenset({"ais"}),
        show_ais=True,
        show_pis=False,
        show_cbpii=False,
        show_vrp=False,
        show_business_defaults=True,
    )
    initial = business_config_form_initial(config)
    form = BusinessConfigForm(
        data=_resubmitted_business_data(initial, ais_consented_account_id="70000170000009"),
        initial=initial,
        config_visibility=visibility,
    )

    assert form.is_valid(), form.errors.as_json()
    assert form.config == {
        "ais": {
            "resourceIds": {
                "accountIds": [{"accountId": "70000170000009"}, {"accountId": "70000170000002"}],
                "statementIds": [{"statementId": "140"}],
            }
        }
    }
