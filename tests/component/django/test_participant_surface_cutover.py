"""Component coverage for participant-plan browser and REST surfaces."""

from __future__ import annotations

import json
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
