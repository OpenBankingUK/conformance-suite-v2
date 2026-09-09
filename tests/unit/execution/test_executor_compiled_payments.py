"""Compiled payment-initiation and VRP plans lowered into executable manifests."""

from datetime import datetime
from pathlib import Path

import pytest

from conformance.context import RuntimeConfig
from conformance.json_types import JsonValue
from conformance.manifest import (
    GeneratedRequestObject,
    ManifestStep,
    PsuAuthorizationStep,
)

pytestmark = pytest.mark.unit


def test_compiled_pis_manifest_builds_signed_payment_bodies_and_authorisation_steps(tmp_path: Path) -> None:
    """PIS catalogue conversion emits request bodies, detached JWS, and PSU authorisation.

    Args:
        tmp_path: Pytest temporary directory used as the runtime-input base.
    """
    from conformance.catalogue import ImplementedEndpoint, TestPlanSpec, compile_test_plan
    from conformance.catalogues.pis import PIS_PAYMENT_CATALOGUE, PIS_PAYMENT_CATALOGUE_KEY
    from conformance.executor import _compiled_plan_to_manifest
    from conformance.manifest import DetachedJwsPolicy, FormBody, JsonBody

    runtime_inputs: dict[str, JsonValue] = {
        "resourceBaseUrl": "https://resource.example.com",
        "pisCreditorAccountSchemeName": "UK.OBIE.SortCodeAccountNumber",
        "pisCreditorAccountIdentification": "70000170000002",
        "pisCreditorAccountName": "Domestic creditor",
        "pisInstructedAmountAmount": "1.00",
        "pisInstructedAmountCurrency": "GBP",
    }
    spec = TestPlanSpec(
        schema_version="v1",
        catalogue_key=PIS_PAYMENT_CATALOGUE_KEY,
        security_profile="fapi1-advanced",
        implemented_endpoints=(
            ImplementedEndpoint(
                method="POST",
                path="/open-banking/v4.0/pisp/domestic-payments",
                resource_group="DomesticPayments",
            ),
        ),
        runtime_inputs=runtime_inputs,
    )
    compiled_plan = compile_test_plan(PIS_PAYMENT_CATALOGUE, spec)

    manifest = _compiled_plan_to_manifest(
        compiled_plan,
        runtime_inputs=runtime_inputs,
        runtime_input_base_dir=tmp_path,
        runtime_config=RuntimeConfig(discovery_url="https://auth.example.com/.well-known/openid-configuration"),
    )

    consent_step = next(step for step in manifest.steps if step.id == "pis-v4-domestic-payment-consent-create-request")
    assert isinstance(consent_step, ManifestStep)
    assert consent_step.request.detached_jws == DetachedJwsPolicy(source="fapi-signing")
    assert isinstance(consent_step.request.body, JsonBody)
    consent_body = consent_step.request.body.value
    assert isinstance(consent_body, dict)
    consent_data = consent_body["Data"]
    assert isinstance(consent_data, dict)
    consent_initiation = consent_data["Initiation"]
    assert isinstance(consent_initiation, dict)
    instruction_identification = consent_initiation["InstructionIdentification"]
    end_to_end_identification = consent_initiation["EndToEndIdentification"]
    assert isinstance(instruction_identification, str)
    assert isinstance(end_to_end_identification, str)
    assert len(instruction_identification) == 32
    assert end_to_end_identification == "e2e-domestic-pay"
    assert instruction_identification != "FCSV2DomesticPaymentInstruction"
    assert consent_step.request.body.value == {
        "Data": {
            "Initiation": {
                "InstructionIdentification": instruction_identification,
                "EndToEndIdentification": end_to_end_identification,
                "InstructedAmount": {"Amount": "1.00", "Currency": "GBP"},
                "CreditorAccount": {
                    "SchemeName": "UK.OBIE.SortCodeAccountNumber",
                    "Identification": "70000170000002",
                    "Name": "Domestic creditor",
                },
            },
        },
        "Risk": {},
    }

    step_ids = [step.id for step in manifest.steps]
    consent_index = step_ids.index("pis-v4-domestic-payment-consent-create-request")
    psu_index = step_ids.index("setup-pis-domestic-payment-consent-authorisation")
    token_index = step_ids.index("setup-token-pis-domestic-payment-access")
    payment_index = step_ids.index("pis-v4-domestic-payment-create-request")
    assert consent_index < psu_index < token_index < payment_index

    authorisation_step = manifest.steps[psu_index]
    assert isinstance(authorisation_step, PsuAuthorizationStep)
    assert authorisation_step.scope == "openid payments"
    assert isinstance(authorisation_step.request_object, GeneratedRequestObject)
    assert (
        authorisation_step.request_object.openbanking_intent_id
        == "${steps.pis-v4-domestic-payment-consent-create-request.response.body.Data.ConsentId}"
    )

    token_step = manifest.steps[token_index]
    assert isinstance(token_step, ManifestStep)
    assert token_step.phase == "execution"
    assert token_step.produces_token_id == "pis-domestic-payment-access"  # noqa: S105 - semantic token id
    assert token_step.token_endpoint_auth_policy is not None
    assert isinstance(token_step.request.body, FormBody)
    assert token_step.request.body.fields == {
        "grant_type": "authorization_code",
        "code": "${steps.setup-pis-domestic-payment-consent-authorisation.response.body.code}",
        "redirect_uri": "${config.oauth.redirectUri}",
        "client_id": "${config.oauth.clientId}",
    }

    consent_read_step = next(
        step for step in manifest.steps if step.id == "pis-v4-domestic-payment-consent-read-authorised-request"
    )
    assert isinstance(consent_read_step, ManifestStep)
    assert consent_read_step.required_token_id == "pis-payment-access"  # noqa: S105 - semantic token id

    payment_step = manifest.steps[payment_index]
    assert isinstance(payment_step, ManifestStep)
    assert payment_step.required_token_id == "pis-domestic-payment-access"  # noqa: S105 - semantic token id
    assert payment_step.request.detached_jws == DetachedJwsPolicy(source="fapi-signing")
    assert isinstance(payment_step.request.body, JsonBody)
    payment_body = payment_step.request.body.value
    assert isinstance(payment_body, dict)
    payment_data = payment_body["Data"]
    assert isinstance(payment_data, dict)
    payment_initiation = payment_data["Initiation"]
    assert isinstance(payment_initiation, dict)
    assert payment_data["ConsentId"] == (
        "${steps.pis-v4-domestic-payment-consent-create-request.response.body.Data.ConsentId}"
    )
    assert payment_initiation["InstructionIdentification"] == (
        "${steps.pis-v4-domestic-payment-consent-create-request.response.body.Data.Initiation.InstructionIdentification}"
    )


def test_compiled_pis_manifest_builds_distinct_domestic_consent_parity_cases(tmp_path: Path) -> None:
    """PIS domestic consent parity cases compile to distinct request-signing policies.

    Args:
        tmp_path: Pytest temporary directory used as the runtime-input base.
    """
    from conformance.catalogue import ImplementedEndpoint, TestPlanSpec, compile_test_plan
    from conformance.catalogues.pis import PIS_PAYMENT_CATALOGUE, PIS_PAYMENT_CATALOGUE_KEY
    from conformance.executor import _compiled_plan_to_manifest
    from conformance.manifest import DetachedJwsPolicy, JsonBody

    runtime_inputs: dict[str, JsonValue] = {
        "resourceBaseUrl": "https://resource.example.com",
        "pisCreditorAccountSchemeName": "UK.OBIE.SortCodeAccountNumber",
        "pisCreditorAccountIdentification": "70000170000002",
        "pisCreditorAccountName": "Domestic creditor",
        "pisInstructedAmountAmount": "1.00",
        "pisInstructedAmountCurrency": "GBP",
    }
    spec = TestPlanSpec(
        schema_version="v1",
        catalogue_key=PIS_PAYMENT_CATALOGUE_KEY,
        security_profile="fapi1-advanced",
        implemented_endpoints=(
            ImplementedEndpoint(
                method="POST",
                path="/open-banking/v4.0/pisp/domestic-payment-consents",
                resource_group="DomesticPayments",
                capability_ids=("pis.domestic-payment-consent.reject-invalid-detached-jws",),
            ),
        ),
        runtime_inputs=runtime_inputs,
    )
    compiled_plan = compile_test_plan(PIS_PAYMENT_CATALOGUE, spec)

    manifest = _compiled_plan_to_manifest(
        compiled_plan,
        runtime_inputs=runtime_inputs,
        runtime_input_base_dir=tmp_path,
        runtime_config=RuntimeConfig(discovery_url="https://auth.example.com/.well-known/openid-configuration"),
    )

    without_authorisation_step = next(
        step
        for step in manifest.steps
        if step.id == "pis-v4-domestic-payment-consent-create-without-authorisation-request"
    )
    assert isinstance(without_authorisation_step, ManifestStep)
    assert without_authorisation_step.request.detached_jws == DetachedJwsPolicy(source="fapi-signing")
    assert without_authorisation_step.response_signature_policy is not None

    missing_claim_step = next(
        step
        for step in manifest.steps
        if step.id == "pis-v4-domestic-payment-consent-reject-missing-signature-claim-request"
    )
    assert isinstance(missing_claim_step, ManifestStep)
    assert missing_claim_step.request.detached_jws == DetachedJwsPolicy(
        source="fapi-signing",
        omit_protected_headers=("iss",),
    )
    assert isinstance(missing_claim_step.request.body, JsonBody)
    missing_signature_step = next(
        step for step in manifest.steps if step.id == "pis-v4-domestic-payment-consent-reject-invalid-signature-request"
    )
    assert isinstance(missing_signature_step, ManifestStep)
    assert missing_signature_step.request.detached_jws is None


def test_compiled_pis_manifest_builds_legacy_scheduled_datetime_variant_bodies(tmp_path: Path) -> None:
    """PIS scheduled consent datetime variants compile with generated future values.

    Args:
        tmp_path: Pytest temporary directory used as the runtime-input base.
    """
    from conformance.catalogue import ImplementedEndpoint, TestPlanSpec, compile_test_plan
    from conformance.catalogues.pis import PIS_PAYMENT_CATALOGUE, PIS_PAYMENT_CATALOGUE_KEY
    from conformance.executor import _compiled_plan_to_manifest
    from conformance.manifest import JsonBody

    runtime_inputs: dict[str, JsonValue] = {
        "resourceBaseUrl": "https://resource.example.com",
        "pisCreditorAccountSchemeName": "UK.OBIE.SortCodeAccountNumber",
        "pisCreditorAccountIdentification": "70000170000002",
        "pisCreditorAccountName": "Domestic creditor",
        "pisInstructedAmountAmount": "1.00",
        "pisInstructedAmountCurrency": "GBP",
        "pisRequestedExecutionDateTime": "2026-12-01T00:00:00+00:00",
    }
    spec = TestPlanSpec(
        schema_version="v1",
        catalogue_key=PIS_PAYMENT_CATALOGUE_KEY,
        security_profile="fapi1-advanced",
        implemented_endpoints=(
            ImplementedEndpoint(
                method="POST",
                path="/open-banking/v4.0/pisp/domestic-scheduled-payment-consents",
                resource_group="DomesticScheduledPayments",
            ),
        ),
        runtime_inputs=runtime_inputs,
    )
    compiled_plan = compile_test_plan(PIS_PAYMENT_CATALOGUE, spec)

    manifest = _compiled_plan_to_manifest(
        compiled_plan,
        runtime_inputs=runtime_inputs,
        runtime_input_base_dir=tmp_path,
        runtime_config=RuntimeConfig(discovery_url="https://auth.example.com/.well-known/openid-configuration"),
    )

    offset_step = next(
        step
        for step in manifest.steps
        if step.id == "pis-v4-domestic-scheduled-payment-consent-create-with-offset-datetime-request"
    )
    utc_step = next(
        step
        for step in manifest.steps
        if step.id == "pis-v4-domestic-scheduled-payment-consent-create-with-utc-datetime-request"
    )
    assert isinstance(offset_step, ManifestStep)
    assert isinstance(utc_step, ManifestStep)
    assert isinstance(offset_step.request.body, JsonBody)
    assert isinstance(utc_step.request.body, JsonBody)
    offset_body = offset_step.request.body.value
    utc_body = utc_step.request.body.value
    assert isinstance(offset_body, dict)
    assert isinstance(utc_body, dict)
    offset_data = offset_body["Data"]
    utc_data = utc_body["Data"]
    assert isinstance(offset_data, dict)
    assert isinstance(utc_data, dict)
    offset_initiation = offset_data["Initiation"]
    utc_initiation = utc_data["Initiation"]
    assert isinstance(offset_initiation, dict)
    assert isinstance(utc_initiation, dict)
    offset_value = offset_initiation["RequestedExecutionDateTime"]
    utc_value = utc_initiation["RequestedExecutionDateTime"]
    assert isinstance(offset_value, str)
    assert isinstance(utc_value, str)
    assert offset_value.endswith("+00:00")
    assert datetime.fromisoformat(offset_value).timetz().isoformat() == "00:00:00+00:00"
    assert utc_value.endswith("Z")
    assert datetime.fromisoformat(utc_value.replace("Z", "+00:00")).timetz().isoformat() == "00:00:00+00:00"


def test_compiled_pis_manifest_uses_per_flow_authorisation_code_tokens(tmp_path: Path) -> None:
    """PIS payment families exchange and consume separate PSU-authorised tokens.

    Args:
        tmp_path: Pytest temporary directory used as the runtime-input base.
    """
    from conformance.catalogue import ImplementedEndpoint, TestPlanSpec, compile_test_plan
    from conformance.catalogues.pis import PIS_PAYMENT_CATALOGUE, PIS_PAYMENT_CATALOGUE_KEY
    from conformance.executor import _compiled_plan_to_manifest
    from conformance.manifest import FormBody

    runtime_inputs: dict[str, JsonValue] = {
        "resourceBaseUrl": "https://resource.example.com",
        "pisCreditorAccountSchemeName": "UK.OBIE.SortCodeAccountNumber",
        "pisCreditorAccountIdentification": "70000170000002",
        "pisCreditorAccountName": "Domestic creditor",
        "pisInternationalCreditorAccountSchemeName": "UK.OBIE.SortCodeAccountNumber",
        "pisInternationalCreditorAccountIdentification": "70000170000003",
        "pisInternationalCreditorAccountName": "International creditor",
        "pisInstructedAmountAmount": "1.00",
        "pisInstructedAmountCurrency": "GBP",
        "pisCurrencyOfTransfer": "USD",
        "pisRequestedExecutionDateTime": "2026-12-01T00:00:00+00:00",
        "pisFirstPaymentDateTime": "2026-12-01T00:00:00+00:00",
        "pisStandingOrderFrequencyType": "WEEK",
        "pisStandingOrderFrequencyPointInTime": "03",
    }
    spec = TestPlanSpec(
        schema_version="v1",
        catalogue_key=PIS_PAYMENT_CATALOGUE_KEY,
        security_profile="fapi1-advanced",
        implemented_endpoints=(
            ImplementedEndpoint(
                method="POST",
                path="/open-banking/v4.0/pisp/domestic-payments",
                resource_group="DomesticPayments",
            ),
            ImplementedEndpoint(
                method="GET",
                path="/open-banking/v4.0/pisp/domestic-payments/{domesticPaymentId}",
                resource_group="DomesticPayments",
            ),
            ImplementedEndpoint(
                method="GET",
                path="/open-banking/v4.0/pisp/domestic-payment-consents/{domesticPaymentConsentId}/funds-confirmation",
                resource_group="DomesticPayments",
            ),
            ImplementedEndpoint(
                method="POST",
                path="/open-banking/v4.0/pisp/domestic-scheduled-payments",
                resource_group="DomesticScheduledPayments",
            ),
            ImplementedEndpoint(
                method="GET",
                path="/open-banking/v4.0/pisp/domestic-scheduled-payments/{domesticScheduledPaymentId}",
                resource_group="DomesticScheduledPayments",
            ),
            ImplementedEndpoint(
                method="POST",
                path="/open-banking/v4.0/pisp/domestic-standing-orders",
                resource_group="DomesticStandingOrders",
            ),
            ImplementedEndpoint(
                method="GET",
                path="/open-banking/v4.0/pisp/domestic-standing-orders/{domesticStandingOrderId}",
                resource_group="DomesticStandingOrders",
            ),
            ImplementedEndpoint(
                method="POST",
                path="/open-banking/v4.0/pisp/international-payments",
                resource_group="InternationalPayments",
            ),
            ImplementedEndpoint(
                method="GET",
                path="/open-banking/v4.0/pisp/international-payments/{internationalPaymentId}",
                resource_group="InternationalPayments",
            ),
            ImplementedEndpoint(
                method="POST",
                path="/open-banking/v4.0/pisp/international-scheduled-payments",
                resource_group="InternationalScheduledPayments",
            ),
            ImplementedEndpoint(
                method="GET",
                path="/open-banking/v4.0/pisp/international-scheduled-payments/{internationalScheduledPaymentId}",
                resource_group="InternationalScheduledPayments",
            ),
        ),
        runtime_inputs=runtime_inputs,
    )
    compiled_plan = compile_test_plan(PIS_PAYMENT_CATALOGUE, spec)

    manifest = _compiled_plan_to_manifest(
        compiled_plan,
        runtime_inputs=runtime_inputs,
        runtime_input_base_dir=tmp_path,
        runtime_config=RuntimeConfig(discovery_url="https://auth.example.com/.well-known/openid-configuration"),
    )

    manifest_steps = [step for step in manifest.steps if isinstance(step, ManifestStep)]
    token_steps = {
        step.id: step
        for step in manifest_steps
        if step.id.startswith("setup-token-pis-") and step.id != "setup-token-pis-payment-access"
    }
    assert set(token_steps) == {
        "setup-token-pis-domestic-payment-access",
        "setup-token-pis-domestic-scheduled-payment-legacy-access",
        "setup-token-pis-domestic-standing-order-access",
        "setup-token-pis-international-payment-access",
        "setup-token-pis-international-scheduled-payment-access",
    }
    for token_step in token_steps.values():
        assert token_step.token_endpoint_auth_policy is not None
        assert isinstance(token_step.request.body, FormBody)
        assert token_step.request.body.fields["grant_type"] == "authorization_code"

    required_tokens_by_step_id = {
        step.id: step.required_token_id for step in manifest_steps if step.id.startswith("pis-v4-")
    }
    assert required_tokens_by_step_id["pis-v4-domestic-payment-consent-create-request"] == "pis-payment-access"
    assert required_tokens_by_step_id["pis-v4-domestic-payment-consent-read-authorised-request"] == (
        "pis-payment-access"
    )
    assert required_tokens_by_step_id["pis-v4-domestic-payment-funds-confirmation-request"] == (
        "pis-domestic-payment-access"
    )
    assert required_tokens_by_step_id["pis-v4-domestic-payment-create-request"] == "pis-domestic-payment-access"
    assert required_tokens_by_step_id["pis-v4-domestic-payment-read-request"] == "pis-payment-access"
    assert (
        required_tokens_by_step_id["pis-v4-domestic-scheduled-payment-consent-read-after-authorisation-request"]
        == "pis-payment-access"
    )
    assert required_tokens_by_step_id["pis-v4-domestic-scheduled-payment-create-request"] == (
        "pis-domestic-scheduled-payment-access"
    )
    assert required_tokens_by_step_id["pis-v4-domestic-standing-order-consent-read-request"] == "pis-payment-access"
    assert required_tokens_by_step_id["pis-v4-domestic-standing-order-create-request"] == (
        "pis-domestic-standing-order-access"
    )
    assert required_tokens_by_step_id["pis-v4-domestic-standing-order-read-request"] == "pis-payment-access"
    assert required_tokens_by_step_id["pis-v4-international-payment-consent-read-request"] == "pis-payment-access"
    assert required_tokens_by_step_id["pis-v4-international-payment-create-request"] == (
        "pis-international-payment-access"
    )
    assert required_tokens_by_step_id["pis-v4-international-payment-read-request"] == "pis-payment-access"
    assert required_tokens_by_step_id["pis-v4-international-scheduled-payment-consent-read-request"] == (
        "pis-payment-access"
    )
    assert required_tokens_by_step_id["pis-v4-international-scheduled-payment-create-request"] == (
        "pis-international-scheduled-payment-access"
    )
    assert required_tokens_by_step_id["pis-v4-international-scheduled-payment-read-request"] == "pis-payment-access"


def test_compiled_vrp_manifest_builds_split_signed_bodies_and_authorisation_steps(tmp_path: Path) -> None:
    """VRP parity cases compile to signed legacy body variants and PSU setup."""
    from conformance.catalogue import ImplementedEndpoint, TestPlanSpec, compile_test_plan
    from conformance.catalogues.vrp import VRP_LEGACY_FCS_CATALOGUE
    from conformance.executor import _compiled_plan_to_manifest
    from conformance.manifest import DetachedJwsPolicy, FormBody, JsonBody

    runtime_inputs: dict[str, JsonValue] = {
        "resourceBaseUrl": "https://resource.example.com",
        "vrpCreditorAccountSchemeName": "UK.OBIE.SortCodeAccountNumber",
        "vrpCreditorAccountIdentification": "70000170000002",
        "vrpCreditorAccountName": "VRP creditor",
        "vrpInstructedAmountAmount": "1.00",
        "vrpInstructedAmountCurrency": "GBP",
        "vrpValidFromDateTime": "2026-08-27T00:00:00+00:00",
        "vrpValidToDateTime": "2026-09-27T00:00:00+00:00",
    }
    spec = TestPlanSpec(
        schema_version="v1",
        catalogue_key=VRP_LEGACY_FCS_CATALOGUE.key,
        security_profile="fapi1-advanced",
        implemented_endpoints=(
            ImplementedEndpoint(
                method="POST",
                path="/domestic-vrps",
                resource_group="DomesticVRP",
            ),
        ),
        runtime_inputs=runtime_inputs,
    )
    compiled_plan = compile_test_plan(VRP_LEGACY_FCS_CATALOGUE, spec)

    manifest = _compiled_plan_to_manifest(
        compiled_plan,
        runtime_inputs=runtime_inputs,
        runtime_input_base_dir=tmp_path,
        runtime_config=RuntimeConfig(discovery_url="https://auth.example.com/.well-known/openid-configuration"),
    )

    step_ids = [step.id for step in manifest.steps]
    assert "setup-token-vrp-payment-access" in step_ids
    consent_flow_ids = (
        "vrp-consent-create-awaiting-authorisation-v31-pre-3111",
        "vrp-consent-create-awaiting-authorisation-v31-3111",
        "vrp-consent-create-awaiting-authorisation-v4",
    )
    for consent_flow_id in consent_flow_ids:
        consent_step_id = f"{consent_flow_id}-request"
        authorisation_step_id = f"{consent_flow_id}-authorisation"
        token_step_id = f"{consent_flow_id}-psu-payment-token"
        assert step_ids.index(consent_step_id) < step_ids.index(authorisation_step_id)
        assert step_ids.index(authorisation_step_id) < step_ids.index(token_step_id)

    assert step_ids.index("vrp-consent-create-awaiting-authorisation-v31-pre-3111-psu-payment-token") < step_ids.index(
        "vrp-payment-create-initial-v31-pre-3111-request"
    )
    assert step_ids.index("vrp-consent-create-awaiting-authorisation-v31-3111-psu-payment-token") < step_ids.index(
        "vrp-payment-create-initial-v31-3111-request"
    )
    assert step_ids.index("vrp-consent-create-awaiting-authorisation-v4-psu-payment-token") < step_ids.index(
        "vrp-payment-create-initial-v4-request"
    )

    authorisation_step = next(
        step for step in manifest.steps if step.id == "vrp-consent-create-awaiting-authorisation-v4-authorisation"
    )
    assert isinstance(authorisation_step, PsuAuthorizationStep)
    assert authorisation_step.scope == "openid payments"
    assert isinstance(authorisation_step.request_object, GeneratedRequestObject)
    assert authorisation_step.request_object.openbanking_intent_id == (
        "${steps.vrp-consent-create-awaiting-authorisation-v4-request.response.body.Data.ConsentId}"
    )

    token_step = next(
        step for step in manifest.steps if step.id == "vrp-consent-create-awaiting-authorisation-v4-psu-payment-token"
    )
    assert isinstance(token_step, ManifestStep)
    assert token_step.produces_token_id == (
        "vrp-consent-create-awaiting-authorisation-v4-psu-payment-access"  # noqa: S105 - semantic token id fixture
    )  # noqa: S105 - semantic token id fixture
    assert token_step.token_endpoint_auth_policy is not None
    assert isinstance(token_step.request.body, FormBody)

    pre_3111_payment_step = next(
        step for step in manifest.steps if step.id == "vrp-payment-create-initial-v31-pre-3111-request"
    )
    post_3111_payment_step = next(
        step for step in manifest.steps if step.id == "vrp-payment-create-initial-v31-3111-request"
    )
    v4_payment_step = next(step for step in manifest.steps if step.id == "vrp-payment-create-initial-v4-request")
    assert isinstance(pre_3111_payment_step, ManifestStep)
    assert isinstance(post_3111_payment_step, ManifestStep)
    assert isinstance(v4_payment_step, ManifestStep)
    assert pre_3111_payment_step.request.url == "https://resource.example.com/open-banking/v3.1/pisp/domestic-vrps"
    assert post_3111_payment_step.request.url == "https://resource.example.com/open-banking/v3.1/pisp/domestic-vrps"
    assert v4_payment_step.request.url == "https://resource.example.com/open-banking/v4.0/pisp/domestic-vrps"
    assert pre_3111_payment_step.required_token_id == (
        "vrp-consent-create-awaiting-authorisation-v31-pre-3111-psu-payment-access"  # noqa: S105
    )
    assert post_3111_payment_step.required_token_id == (
        "vrp-consent-create-awaiting-authorisation-v31-3111-psu-payment-access"  # noqa: S105
    )
    assert v4_payment_step.required_token_id == (
        "vrp-consent-create-awaiting-authorisation-v4-psu-payment-access"  # noqa: S105
    )
    for payment_step in (pre_3111_payment_step, post_3111_payment_step, v4_payment_step):
        assert payment_step.request.detached_jws == DetachedJwsPolicy(source="fapi-signing")
        assert isinstance(payment_step.request.body, JsonBody)

    assert isinstance(pre_3111_payment_step.request.body, JsonBody)
    assert isinstance(post_3111_payment_step.request.body, JsonBody)
    assert isinstance(v4_payment_step.request.body, JsonBody)
    pre_3111_body = pre_3111_payment_step.request.body.value
    post_3111_body = post_3111_payment_step.request.body.value
    v4_body = v4_payment_step.request.body.value
    assert isinstance(pre_3111_body, dict)
    assert isinstance(post_3111_body, dict)
    assert isinstance(v4_body, dict)
    pre_3111_data = pre_3111_body["Data"]
    post_3111_data = post_3111_body["Data"]
    v4_data = v4_body["Data"]
    assert isinstance(pre_3111_data, dict)
    assert isinstance(post_3111_data, dict)
    assert isinstance(v4_data, dict)
    assert "VRPType" not in pre_3111_data
    assert post_3111_data["VRPType"] == "UK.OBIE.VRPType.Sweeping"
    assert v4_data["VRPType"] == "UK.OBIE.VRPType.Sweeping"
    v4_initiation = v4_data["Initiation"]
    assert isinstance(v4_initiation, dict)
    v4_remittance = v4_initiation["RemittanceInformation"]
    assert isinstance(v4_remittance, dict)
    assert v4_remittance["Unstructured"] == ["Test Unstructured Data"]


def test_compiled_vrp_v4_manifest_excludes_v31_variants(tmp_path: Path) -> None:
    """VRP v4 plan compilation does not execute legacy v3.1 body variants.

    Args:
        tmp_path: Pytest temporary directory used as the runtime-input base.
    """
    from conformance.catalogue import ImplementedEndpoint, TestPlanSpec, compile_test_plan
    from conformance.catalogues.vrp import VRP_LEGACY_FCS_CATALOGUE
    from conformance.executor import _compiled_plan_to_manifest
    from conformance.manifest import JsonBody

    runtime_inputs: dict[str, JsonValue] = {
        "resourceBaseUrl": "https://resource.example.com",
        "vrpCreditorAccountSchemeName": "UK.OBIE.SortCodeAccountNumber",
        "vrpCreditorAccountIdentification": "70000170000002",
        "vrpCreditorAccountName": "VRP creditor",
        "vrpInstructedAmountAmount": "1.00",
        "vrpInstructedAmountCurrency": "GBP",
        "vrpValidFromDateTime": "2026-08-27T00:00:00+00:00",
        "vrpValidToDateTime": "2026-09-27T00:00:00+00:00",
    }
    spec = TestPlanSpec(
        schema_version="v1",
        catalogue_key=VRP_LEGACY_FCS_CATALOGUE.key,
        security_profile="fapi1-advanced",
        implemented_endpoints=(
            ImplementedEndpoint(
                method="POST",
                path="/domestic-vrps",
                resource_group="DomesticVRP",
            ),
        ),
        runtime_inputs=runtime_inputs,
        specification_version="4.0.1",
    )
    compiled_plan = compile_test_plan(VRP_LEGACY_FCS_CATALOGUE, spec)

    assert [test_case.test_case_id for test_case in compiled_plan.test_cases] == [
        "vrp-consent-create-awaiting-authorisation-v4",
        "vrp-payment-create-initial-v4",
        "vrp-payment-create-repeated-v4",
    ]

    manifest = _compiled_plan_to_manifest(
        compiled_plan,
        runtime_inputs=runtime_inputs,
        runtime_input_base_dir=tmp_path,
        runtime_config=RuntimeConfig(discovery_url="https://auth.example.com/.well-known/openid-configuration"),
    )

    step_ids = [step.id for step in manifest.steps]
    assert all("-v31" not in step_id for step_id in step_ids)
    v4_payment_step = next(step for step in manifest.steps if step.id == "vrp-payment-create-initial-v4-request")
    assert isinstance(v4_payment_step, ManifestStep)
    assert v4_payment_step.request.url == "https://resource.example.com/open-banking/v4.0/pisp/domestic-vrps"
    assert isinstance(v4_payment_step.request.body, JsonBody)
    v4_body = v4_payment_step.request.body.value
    assert isinstance(v4_body, dict)
    v4_data = v4_body["Data"]
    assert isinstance(v4_data, dict)
    assert v4_data["VRPType"] == "UK.OBIE.VRPType.Sweeping"


def test_compiled_vrp_v4_manifest_keeps_single_psu_authorisation_and_one_of_assertions(tmp_path: Path) -> None:
    """Full v4 VRP plans keep old-FCS PSU and one-of assertion parity.

    Args:
        tmp_path: Pytest temporary directory used as the runtime-input base.
    """
    from conformance.catalogue import compile_test_plan_document, parse_test_plan_document
    from conformance.catalogue_registry import supported_catalogues
    from conformance.executor import _compiled_plan_to_manifest
    from conformance.manifest import HttpStatusAssertion

    runtime_inputs: dict[str, JsonValue] = {
        "resourceBaseUrl": "https://resource.example.com",
        "vrpCreditorAccountSchemeName": "UK.OBIE.SortCodeAccountNumber",
        "vrpCreditorAccountIdentification": "70000170000002",
        "vrpCreditorAccountName": "VRP creditor",
        "vrpInstructedAmountAmount": "1.00",
        "vrpInstructedAmountCurrency": "GBP",
        "vrpValidFromDateTime": "2026-08-27T00:00:00+00:00",
        "vrpValidToDateTime": "2026-09-27T00:00:00+00:00",
    }
    document = parse_test_plan_document(
        {
            "schemaVersion": "1.0",
            "specification": {"family": "OBL_READ_WRITE", "version": "4.0.1", "profile": "FAPI1_ADVANCED"},
            "securityEnvironment": {"discoveryUrl": "https://auth.example.com/.well-known/openid-configuration"},
            "resourceGroups": [
                {
                    "id": "VRP",
                    "endpoints": [
                        {"method": "POST", "path": "/domestic-vrp-consents"},
                        {"method": "GET", "path": "/domestic-vrp-consents/{consentId}"},
                        {
                            "method": "POST",
                            "path": "/domestic-vrp-consents/{consentId}/funds-confirmation",
                            "capabilities": ["vrp.funds-confirmation"],
                        },
                        {"method": "DELETE", "path": "/domestic-vrp-consents/{consentId}"},
                        {"method": "POST", "path": "/domestic-vrps"},
                        {"method": "GET", "path": "/domestic-vrps/{vrpId}"},
                        {"method": "GET", "path": "/domestic-vrps/{vrpId}/payment-details"},
                    ],
                }
            ],
            "businessTestData": {"runtimeInputs": runtime_inputs},
            "metadata": {},
        }
    )
    compiled_plan = compile_test_plan_document(document, supported_catalogues())

    manifest = _compiled_plan_to_manifest(
        compiled_plan,
        runtime_inputs=runtime_inputs,
        runtime_input_base_dir=tmp_path,
        runtime_config=RuntimeConfig(discovery_url="https://auth.example.com/.well-known/openid-configuration"),
    )

    assert sum(isinstance(step, PsuAuthorizationStep) for step in manifest.steps) == 1
    assert [case.test_case_id for case in compiled_plan.test_cases] == [
        "vrp-consent-create-awaiting-authorisation-v4",
        "vrp-payment-create-initial-v4",
        "vrp-consent-get-authorised",
        "vrp-consent-funds-confirmation",
        "vrp-payment-get-initial",
        "vrp-payment-create-repeated-v4",
        "vrp-payment-get-repeated",
        "vrp-payment-get-details",
        "vrp-consent-delete",
        "vrp-consent-get-after-delete",
        "vrp-consent-delete-after-delete",
    ]
    assertions_by_step_id = {step.id: step.assertions for step in manifest.steps if isinstance(step, ManifestStep)}
    funds_confirmation_assertion = assertions_by_step_id["vrp-consent-funds-confirmation-request"][0]
    delete_after_delete_assertion = assertions_by_step_id["vrp-consent-delete-after-delete-request"][0]
    assert isinstance(funds_confirmation_assertion, HttpStatusAssertion)
    assert isinstance(delete_after_delete_assertion, HttpStatusAssertion)
    assert funds_confirmation_assertion.expected_one_of == (201,)
    assert delete_after_delete_assertion.expected_one_of == (400, 204)
