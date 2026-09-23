"""Component coverage for participant-plan browser and REST surfaces."""

from __future__ import annotations

import json
import re
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from django.test import Client
from django.urls import reverse

import conformance.api.builder_wizard
import conformance.test_plan_validation
from conformance.api.builder_draft_store import BuilderDraft
from conformance.api.builder_wizard import participant_plan_from_draft
from conformance.api.run_store import run_store
from conformance.configuration_contracts import PreparedExecutionManifest
from conformance.configuration_contracts.models import StandingOrderFrequency
from conformance.participant_surface import supported_participant_catalogues
from tests.support.paths import REPO_ROOT
from tests.support.run_execution import StubbedRunExecution

pytestmark = pytest.mark.component

_SURFACE_PLAN_PATH = (
    REPO_ROOT / "tests" / "fixtures" / "configuration_contracts" / "pis" / "v4_0_1" / "participant-plan.surface.json"
)
_AIS_ACCOUNTS_SURFACE_PLAN_PATH = (
    REPO_ROOT / "tests" / "fixtures" / "configuration_contracts" / "ais" / "v4_0_1" / "participant-plan.surface.json"
)
_CBPII_PLAN_PATH = (
    REPO_ROOT / "tests" / "fixtures" / "configuration_contracts" / "cbpii" / "v4_0_1" / "participant-plan.json"
)
_VRP_PLAN_PATH = (
    REPO_ROOT / "tests" / "fixtures" / "configuration_contracts" / "vrp" / "v4_0_1" / "participant-plan.json"
)


@pytest.fixture(autouse=True)
def _reset_run_store() -> None:
    run_store.reset()


def _draft_id(location: str) -> str:
    return location.rstrip("/").rsplit("/", maxsplit=2)[-2]


@patch("conformance.api.ui_views._fetch_discovery_metadata")
def test_browser_builds_reviews_exports_and_launches_participant_plan(
    mock_fetch_discovery: Mock,
    stubbed_run_execution: StubbedRunExecution,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(*args: object, **kwargs: object) -> None:
        raise AssertionError("legacy authority was called")

    monkeypatch.setattr(conformance.api.builder_wizard, "supported_catalogues", fail)
    monkeypatch.setattr(conformance.api.builder_wizard, "compile_test_plan_document", fail)
    monkeypatch.setattr(conformance.test_plan_validation, "prepare_test_plan_for_run", fail)
    mock_fetch_discovery.return_value = {}
    client = Client()
    response = client.post("/builder/new/")
    draft_id = _draft_id(response["Location"])
    response = client.post(
        response["Location"],
        data={
            "scheme": "open-banking-uk",
            "specification": "read-write",
            "version": "4.0.1",
        },
    )
    response = client.post(
        response["Location"],
        data={"discovery_url": "https://as.example.com/.well-known/openid-configuration"},
    )
    response = client.post(
        response["Location"],
        data={
            "oauth_client_id": "client-123",
            "oauth_redirect_uri": "https://client.example.com/callback",
            "resource_server_base_url": "https://rs.example.com",
        },
    )

    scope_page = client.get(response["Location"])
    scope_content = scope_page.content.decode()
    assert "Test scope and capabilities" in scope_content
    assert "Payment Initiation" in scope_content
    assert "GET /open-banking" not in scope_content

    response = client.post(
        response["Location"],
        data={
            "test_scope": "pis",
            "capabilities": ["pis.v401.capability.domestic-standing-order"],
        },
    )
    response = client.post(
        response["Location"],
        data={
            "pis_creditor_account_scheme_name": "UK.OBIE.SortCodeAccountNumber",
            "pis_creditor_account_identification": "08080021325698",
            "pis_creditor_account_name": "Merchant",
            "pis_instructed_amount_amount": "10.00",
            "pis_instructed_amount_currency": "GBP",
            "pis_first_payment_date_time": "2026-10-01T00:00:00Z",
        },
    )
    response = client.post(
        response["Location"],
        data={
            "runtime_input__pis.v401.input.standing-order-frequency": ('{"frequencyType":"WEEK","pointInTime":"03"}')
        },
    )
    review_url = response["Location"]
    review = client.get(review_url)
    content = review.content.decode()
    assert "Ready to launch from this reviewed plan." in content
    assert "pis.v401.test.domestic_standing_order_consents.positive.instance" in content

    exported = client.post(
        reverse("builder-export", kwargs={"draft_id": draft_id}),
        data={"include_secrets": "1"},
    )
    exported_plan = json.loads(exported.content)
    assert exported_plan["documentType"] == "participant-plan"
    assert exported_plan["selectedCapabilityIds"] == ["pis.v401.capability.domestic-standing-order"]
    compatibility_inputs = exported_plan["executionConfiguration"]["compatibilityRuntimeInputs"]
    assert set(compatibility_inputs) == {"discoveryUrl", "resourceBaseUrl"}
    assert not any(key.startswith("pis") for key in compatibility_inputs)
    assert {item["inputId"] for item in exported_plan["predefinedInputs"]} == {
        "pis.v401.input.creditor-account-scheme-name",
        "pis.v401.input.creditor-account-identification",
        "pis.v401.input.creditor-account-name",
        "pis.v401.input.instructed-amount",
        "pis.v401.input.instructed-currency",
        "pis.v401.input.first-payment-date-time",
        "pis.v401.input.standing-order-frequency",
    }
    assert "resourceGroups" not in exported_plan
    assert "endpoints" not in exported_plan

    launch_response = client.post(reverse("builder-launch", kwargs={"draft_id": draft_id}))
    assert launch_response.status_code == 302
    launch = stubbed_run_execution.wait_for_launch()
    prepared_manifest = launch.args[2]
    assert isinstance(prepared_manifest, PreparedExecutionManifest)
    assert prepared_manifest.artifact_resolver.manifest == prepared_manifest.manifest
    assert any(item.redacted for item in prepared_manifest.manifest.inputs)


def test_rest_accepts_the_same_participant_plan(
    stubbed_run_execution: StubbedRunExecution,
) -> None:
    client = Client()
    raw_plan = json.loads(_SURFACE_PLAN_PATH.read_text(encoding="utf-8"))

    response = client.post("/api/runs/", data=json.dumps(raw_plan), content_type="application/json")

    assert response.status_code == 201
    record = run_store.get_run(response.json()["id"])
    assert record is not None
    assert record.plan_snapshot is not None
    assert record.plan_snapshot["documentType"] == "participant-plan"
    assert "08080021325698" not in json.dumps(record.plan_snapshot)
    launch = stubbed_run_execution.wait_for_launch()
    assert isinstance(launch.args[2], PreparedExecutionManifest)


@pytest.mark.parametrize("test_scope", ["cbpii", "vrp"])
def test_rest_launches_remaining_v2_catalogue_families(
    stubbed_run_execution: StubbedRunExecution,
    test_scope: str,
) -> None:
    participant_catalogue = next(
        item
        for item in supported_participant_catalogues()
        if item.test_catalogue.specification.test_scope == test_scope
        and item.test_catalogue.specification.version == "4.0.1"
    )
    catalogue = participant_catalogue.test_catalogue
    capability = catalogue.capabilities[0]
    inputs = []
    for item in catalogue.predefined_inputs:
        if capability.id not in item.required_for_capability_ids:
            continue
        value = item.example_value
        inputs.append(
            {
                "inputId": str(item.id),
                "value": (
                    {
                        "frequencyType": value.frequency_type,
                        **(
                            {"countPerPeriod": value.count_per_period}
                            if isinstance(value, StandingOrderFrequency) and value.count_per_period is not None
                            else {}
                        ),
                        **(
                            {"pointInTime": value.point_in_time}
                            if isinstance(value, StandingOrderFrequency) and value.point_in_time is not None
                            else {}
                        ),
                    }
                    if isinstance(value, StandingOrderFrequency)
                    else value
                ),
            }
        )
    raw_plan = {
        "documentType": "participant-plan",
        "executionConfiguration": {
            "compatibilityRuntimeInputs": {
                "resourceBaseUrl": "https://rs.example.com",
            },
            "dynamicClientRegistration": {},
            "metadata": {},
            "securityEnvironment": {"discoveryUrl": "https://as.example.com/.well-known/openid-configuration"},
        },
        "id": f"participant.{test_scope}.component",
        "predefinedInputs": inputs,
        "schemaVersion": "2.0",
        "scheme": str(catalogue.scheme),
        "securityProfile": "fapi1-advanced",
        "selectedCapabilityIds": [str(capability.id)],
        "specification": {
            "id": str(catalogue.specification.id),
            "testScope": test_scope,
            "version": catalogue.specification.version,
        },
        "suiteReleaseId": str(participant_catalogue.suite_release.id),
    }

    response = Client().post("/api/runs/", data=json.dumps(raw_plan), content_type="application/json")

    assert response.status_code == 201
    prepared = stubbed_run_execution.wait_for_launch().args[2]
    assert isinstance(prepared, PreparedExecutionManifest)
    assert prepared.manifest.schema_version == "2.0"
    assert prepared.result_traceability is not None
    if test_scope == "cbpii":
        debtor_name = next(item for item in prepared.manifest.inputs if item.id.endswith("debtor-account-name"))
        assert debtor_name.value == "Debtor account"
        assert debtor_name.redacted is False


def test_browser_import_rejects_catalogue_unsupported_security_profile() -> None:
    client = Client()
    raw_plan = json.loads(_SURFACE_PLAN_PATH.read_text(encoding="utf-8"))
    raw_plan["securityProfile"] = "all"

    response = client.post(
        reverse("builder-import"),
        data={"plan_json": json.dumps(raw_plan)},
    )

    assert response.status_code == 400
    assert b"plan.reference.security-profile-unsupported" in response.content


def test_browser_import_hydrates_scope_and_capability_controls() -> None:
    """Imported scope selections remain checked in the guided editor."""
    client = Client()
    raw_plan = json.loads(_CBPII_PLAN_PATH.read_text(encoding="utf-8"))
    raw_plan["predefinedInputs"].append(
        {
            "inputId": "cbpii.v401.input.debtor-account-name",
            "value": "Debtor account",
        }
    )

    imported = client.post(
        reverse("builder-import"),
        data={"plan_json": json.dumps(raw_plan)},
    )

    assert imported.status_code == 302
    draft_id = _draft_id(imported["Location"])
    scope_page = client.get(reverse("builder-scope", kwargs={"draft_id": draft_id}))
    content = scope_page.content.decode()
    assert scope_page.status_code == 200
    assert re.search(r'name="test_scope"\s+value="cbpii"\s+checked', content)
    assert re.search(
        r'name="capabilities"\s+value="cbpii\.v401\.capability\.confirmation-of-funds"\s+checked',
        content,
    )
    assert not re.search(r'name="test_scope"\s+value="ais"\s+checked', content)


def test_browser_import_hydrates_and_replaces_business_defaults() -> None:
    """Imported Business values remain visible and authoritative after edits."""
    client = Client()
    raw_plan = json.loads(_CBPII_PLAN_PATH.read_text(encoding="utf-8"))
    raw_plan["predefinedInputs"].append(
        {
            "inputId": "cbpii.v401.input.debtor-account-name",
            "value": "Debtor account",
        }
    )

    imported = client.post(
        reverse("builder-import"),
        data={"plan_json": json.dumps(raw_plan)},
    )

    assert imported.status_code == 302
    draft_id = _draft_id(imported["Location"])
    business_page = client.get(reverse("builder-config", kwargs={"draft_id": draft_id}))
    content = business_page.content.decode()
    assert business_page.status_code == 200
    assert 'name="cbpii_debtor_account_scheme_name"' in content
    assert 'value="UK.OBIE.SortCodeAccountNumber"' in content
    assert 'name="cbpii_debtor_account_identification"' in content
    assert 'value="08080021325698"' in content
    assert 'name="cbpii_debtor_account_name"' in content
    assert 'value="Debtor account"' in content

    updated = {
        "cbpii_debtor_account_scheme_name": "updated-scheme",
        "cbpii_debtor_account_identification": "updated-identification",
        "cbpii_debtor_account_name": "Updated debtor",
    }
    saved = client.post(
        reverse("builder-config", kwargs={"draft_id": draft_id}),
        data=updated,
    )
    exported = client.post(
        reverse("builder-export", kwargs={"draft_id": draft_id}),
        data={"include_secrets": "1"},
    )

    assert saved.status_code == 302
    assert exported.status_code == 200
    original_inputs = {item["inputId"]: item["value"] for item in raw_plan["predefinedInputs"]}
    exported_inputs = {item["inputId"]: item["value"] for item in json.loads(exported.content)["predefinedInputs"]}
    assert exported_inputs["cbpii.v401.input.debtor-account-scheme"] == "updated-scheme"
    assert exported_inputs["cbpii.v401.input.debtor-account-identification"] == "updated-identification"
    assert exported_inputs["cbpii.v401.input.debtor-account-name"] == "Updated debtor"
    assert (
        exported_inputs["cbpii.v401.input.instructed-amount"] == original_inputs["cbpii.v401.input.instructed-amount"]
    )
    assert (
        exported_inputs["cbpii.v401.input.instructed-currency"]
        == original_inputs["cbpii.v401.input.instructed-currency"]
    )
    safe_export = client.get(reverse("builder-export", kwargs={"draft_id": draft_id}))
    safe_inputs = {item["inputId"]: item["value"] for item in json.loads(safe_export.content)["predefinedInputs"]}
    assert safe_inputs["cbpii.v401.input.debtor-account-scheme"] == "updated-scheme"
    assert safe_inputs["cbpii.v401.input.debtor-account-identification"] == "updated-identification"
    assert safe_inputs["cbpii.v401.input.debtor-account-name"] == "Updated debtor"


@pytest.mark.parametrize(
    ("plan_path", "field_name", "expected_value"),
    [
        (_AIS_ACCOUNTS_SURFACE_PLAN_PATH, "ais_consented_account_id", "account-123"),
        (_SURFACE_PLAN_PATH, "pis_creditor_account_name", "Merchant"),
        (_VRP_PLAN_PATH, "vrp_creditor_account_name", "VRP creditor"),
    ],
)
def test_browser_import_hydrates_business_defaults_for_each_scope(
    plan_path: Path,
    field_name: str,
    expected_value: str,
) -> None:
    """Imported AIS, PIS, and VRP inputs cross the real browser boundary."""
    client = Client()
    raw_plan = json.loads(plan_path.read_text(encoding="utf-8"))

    imported = client.post(
        reverse("builder-import"),
        data={"plan_json": json.dumps(raw_plan)},
    )

    assert imported.status_code == 302
    draft_id = _draft_id(imported["Location"])
    business_page = client.get(reverse("builder-config", kwargs={"draft_id": draft_id}))
    content = business_page.content.decode()
    assert business_page.status_code == 200
    assert f'name="{field_name}"' in content
    assert f'value="{expected_value}"' in content


@patch("conformance.api.ui_views._fetch_discovery_metadata")
def test_browser_import_hydrates_and_replaces_canonical_security_fields(
    mock_fetch_discovery: Mock,
    tmp_path: Path,
) -> None:
    """Imported security values remain visible, editable, and clearable."""
    mock_fetch_discovery.return_value = {}
    signing_certificate = tmp_path / "signing.pem"
    signing_private_key = tmp_path / "signing.key"
    ca_bundle = tmp_path / "ca.pem"
    transport_certificate = tmp_path / "transport.pem"
    transport_private_key = tmp_path / "transport.key"
    for path in (
        signing_certificate,
        signing_private_key,
        ca_bundle,
        transport_certificate,
        transport_private_key,
    ):
        path.touch()
    client = Client()
    raw_plan = json.loads(_SURFACE_PLAN_PATH.read_text(encoding="utf-8"))
    imported_security = {
        "discoveryUrl": "https://as.example.com/.well-known/openid-configuration",
        "clientId": "imported-client",
        "redirectUri": "https://client.example.com/imported-callback",
        "authorizationEndpoint": "https://as.example.com/authorize",
        "issuer": "https://as.example.com",
        "tokenEndpoint": "https://as.example.com/token",
        "resourceBaseUrl": "https://rs.example.com",
        "responseType": "code id_token",
        "signingAlgorithm": "PS256",
        "signingCertificatePath": str(signing_certificate),
        "signingPrivateKeyPath": str(signing_private_key),
        "signingKeyId": "imported-signing-key",
        "clientAssertionIssuer": "imported-assertion-issuer",
        "clientAssertionSubject": "imported-assertion-subject",
        "clientAuthMethod": "private_key_jwt",
        "clientAuthSigningAlgorithm": "PS256",
        "mtls": {
            "enabled": False,
            "caBundlePath": str(ca_bundle),
            "certificatePath": str(transport_certificate),
            "privateKeyPath": str(transport_private_key),
        },
    }
    raw_plan["executionConfiguration"]["securityEnvironment"] = imported_security

    imported = client.post(
        reverse("builder-import"),
        data={"plan_json": json.dumps(raw_plan)},
    )

    assert imported.status_code == 302
    draft_id = _draft_id(imported["Location"])
    untouched_export = client.post(
        reverse("builder-export", kwargs={"draft_id": draft_id}),
        data={"include_secrets": "1"},
    )
    assert untouched_export.status_code == 200
    assert json.loads(untouched_export.content)["executionConfiguration"]["securityEnvironment"] == imported_security

    discovery_page = client.get(reverse("builder-discovery-config", kwargs={"draft_id": draft_id}))
    assert discovery_page.status_code == 200
    assert 'value="https://as.example.com/.well-known/openid-configuration"' in discovery_page.content.decode()

    security_page = client.get(reverse("builder-security-config", kwargs={"draft_id": draft_id}))
    assert security_page.status_code == 200
    security_content = security_page.content.decode()
    for imported_value in (
        "imported-client",
        "https://client.example.com/imported-callback",
        "https://as.example.com/authorize",
        "https://as.example.com",
        "https://as.example.com/token",
        "https://rs.example.com",
        "code id_token",
        "PS256",
        str(signing_certificate),
        str(signing_private_key),
        "imported-signing-key",
        "imported-assertion-issuer",
        "imported-assertion-subject",
        str(ca_bundle),
        str(transport_certificate),
        str(transport_private_key),
    ):
        assert f'value="{imported_value}"' in security_content
    assert '<option value="private_key_jwt" selected>private_key_jwt</option>' in security_content
    assert '<option value="false" selected>Disabled</option>' in security_content

    updated_discovery_url = "https://new-as.example.com/.well-known/openid-configuration"
    discovery_update = client.post(
        reverse("builder-discovery-config", kwargs={"draft_id": draft_id}),
        data={"discovery_url": updated_discovery_url},
    )
    assert discovery_update.status_code == 302

    security_update = client.post(
        reverse("builder-security-config", kwargs={"draft_id": draft_id}),
        data={
            "oauth_client_id": "updated-client",
            "oauth_redirect_uri": "https://client.example.com/updated-callback",
            "oauth_authorization_endpoint": "https://as.example.com/authorize",
            "oauth_issuer": "",
            "oauth_token_endpoint": "https://as.example.com/updated-token",
            "oauth_response_type": "code",
            "oauth_request_object_signing_alg": "PS256",
            "resource_server_base_url": "https://updated-rs.example.com",
            "signing_certificate_path": str(signing_certificate),
            "signing_private_key_path": str(signing_private_key),
            "signing_kid": "updated-signing-key",
            "signing_client_assertion_issuer": "updated-assertion-issuer",
            "signing_client_assertion_subject": "updated-assertion-subject",
            "signing_token_endpoint_auth_method": "private_key_jwt",
            "signing_client_auth_algorithm": "",
            "mtls_enabled": "false",
            "tls_ca_bundle_path": "",
            "tls_client_certificate_path": str(transport_certificate),
            "tls_client_private_key_path": str(transport_private_key),
        },
    )
    assert security_update.status_code == 302, security_update.content.decode()

    exported = client.post(
        reverse("builder-export", kwargs={"draft_id": draft_id}),
        data={"include_secrets": "1"},
    )
    assert exported.status_code == 200
    exported_security = json.loads(exported.content)["executionConfiguration"]["securityEnvironment"]
    assert exported_security["discoveryUrl"] == updated_discovery_url
    assert exported_security["clientId"] == "updated-client"
    assert exported_security["tokenEndpoint"] == "https://as.example.com/updated-token"
    assert exported_security["resourceBaseUrl"] == "https://updated-rs.example.com"
    assert exported_security["mtls"] == {
        "enabled": False,
        "certificatePath": str(transport_certificate),
        "privateKeyPath": str(transport_private_key),
    }
    assert "issuer" not in exported_security
    assert "clientAuthSigningAlgorithm" not in exported_security

    safe_export = client.get(reverse("builder-export", kwargs={"draft_id": draft_id}))
    safe_security = json.loads(safe_export.content)["executionConfiguration"]["securityEnvironment"]
    assert "clientId" not in safe_security
    assert "signingKeyId" not in safe_security


def test_browser_promotes_cbpii_debtor_name_to_catalogue_input() -> None:
    draft = (
        BuilderDraft.create()
        .with_catalogue_boundary(
            scheme="open-banking-uk",
            specification="read-write",
            version="4.0.1",
        )
        .with_scope_selection(
            resource_group_ids=("cbpii",),
            endpoint_ids=("cbpii.v401.capability.confirmation-of-funds",),
            endpoint_capability_ids={},
        )
        .with_config(
            config={
                "cbpii": {
                    "debtorAccount": {
                        "schemeName": "UK.OBIE.SortCodeAccountNumber",
                        "identification": "08080021325698",
                        "name": "Participant debtor",
                    },
                    "instructedAmount": {"amount": "10.00", "currency": "GBP"},
                }
            }
        )
    )

    plan = participant_plan_from_draft(draft)

    inputs = {str(item.input_id): item.value for item in plan.predefined_inputs}
    assert inputs["cbpii.v401.input.debtor-account-name"] == "Participant debtor"
    assert plan.execution_configuration is not None
    assert "debtorAccountName" not in plan.execution_configuration.compatibility_runtime_inputs


@pytest.mark.parametrize(
    ("version", "id_version"),
    [("3.1.11", "v311"), ("4.0.1", "v401")],
)
def test_rest_launches_ais_accounts_plan_with_executable_consent_delete(
    stubbed_run_execution: StubbedRunExecution,
    version: str,
    id_version: str,
) -> None:
    client = Client()
    raw_plan = json.loads(_AIS_ACCOUNTS_SURFACE_PLAN_PATH.read_text(encoding="utf-8"))
    raw_plan["id"] = f"participant.ais-{id_version}.accounts-surface"
    raw_plan["specification"]["version"] = version
    raw_plan["selectedCapabilityIds"] = [f"ais.{id_version}.capability.accounts"]

    response = client.post("/api/runs/", data=json.dumps(raw_plan), content_type="application/json")

    assert response.status_code == 201
    launch = stubbed_run_execution.wait_for_launch()
    prepared = launch.args[2]
    assert isinstance(prepared, PreparedExecutionManifest)
    assert prepared.manifest is not None
    assert prepared.result_traceability is not None
    assert prepared.result_traceability.resolved_plan.selection_valid is True

    delete_manifest_steps = {
        str(step.id): str(step.test_definition_id)
        for step in prepared.manifest.steps
        if step.request.method.value == "DELETE"
    }
    assert delete_manifest_steps == {
        f"ais.{id_version}.test.delete.account_access_consents.consentid.positive.instance.request": (
            f"ais.{id_version}.test.delete.account_access_consents.consentid.positive"
        ),
        f"ais.{id_version}.test.delete.account_access_consents.consentid.unauthorized.instance.request": (
            f"ais.{id_version}.test.delete.account_access_consents.consentid.unauthorized"
        ),
        f"ais.{id_version}.test.delete.account_access_consents.consentid.invalid-resource.instance.request": (
            f"ais.{id_version}.test.delete.account_access_consents.consentid.invalid-resource"
        ),
    }
    assert all(
        step.request.base_url_source is not None
        for step in prepared.manifest.steps
        if str(step.id) in delete_manifest_steps
    )


def test_browser_import_rejects_legacy_canonical_plan() -> None:
    legacy_plan = {
        "schemaVersion": "1.0",
        "specification": {
            "family": "OBL_DCR",
            "scheme": "open-banking-uk",
            "name": "dynamic-client-registration",
            "version": "3.4",
        },
        "securityEnvironment": {},
        "endpoints": [{"method": "POST", "path": "/register", "required": True, "locked": True}],
        "dynamicClientRegistration": {},
        "metadata": {},
    }

    response = Client().post(
        reverse("builder-import"),
        data={"plan_json": json.dumps(legacy_plan)},
    )

    assert response.status_code == 400
    assert "participant-plan" in response.content.decode()


def test_rest_accepts_dcr_registration_with_explicit_observation_mapping(
    stubbed_run_execution: StubbedRunExecution,
    tmp_path: Path,
) -> None:
    for name in ("transport.crt", "transport.key", "signing.key", "ssa.jwt"):
        (tmp_path / name).touch()
    raw_plan = json.loads(
        (
            REPO_ROOT / "tests" / "fixtures" / "configuration_contracts" / "dcr" / "v3_4" / "participant-plan.json"
        ).read_text(encoding="utf-8")
    )
    raw_plan["selectedCapabilityIds"] = ["dcr.v34.capability.registration"]
    raw_plan["executionConfiguration"] = {
        "compatibilityRuntimeInputs": {},
        "dynamicClientRegistration": {
            "registrationAudience": "aspsp123",
            "softwareStatementAssertionPath": str(tmp_path / "ssa.jwt"),
        },
        "metadata": {},
        "securityEnvironment": {
            "clientAuthMethod": "private_key_jwt",
            "clientAuthSigningAlgorithm": "PS256",
            "discoveryUrl": "https://as.example.com/.well-known/openid-configuration",
            "mtls": {
                "certificatePath": str(tmp_path / "transport.crt"),
                "privateKeyPath": str(tmp_path / "transport.key"),
            },
            "signingKeyId": "kid-123",
            "signingPrivateKeyPath": str(tmp_path / "signing.key"),
        },
    }

    response = Client().post("/api/runs/", data=json.dumps(raw_plan), content_type="application/json")

    assert response.status_code == 201
    launch = stubbed_run_execution.wait_for_launch()
    prepared = launch.args[2]
    assert isinstance(prepared, PreparedExecutionManifest)
    assert prepared.result_traceability is not None
    assert {str(step.id) for step in prepared.manifest.steps} == {
        "dcr.v34.test.registration.empty-issuer.instance.request",
        "dcr.v34.test.registration.expired.instance.request",
        "dcr.v34.test.registration.invalid-auth-method.instance.request",
        "dcr.v34.test.registration.invalid-issuer.instance.request",
        "dcr.v34.test.registration.overlong-issuer.instance.request",
        "dcr.v34.test.registration.positive.instance.request",
    }
