"""Component coverage for participant-plan browser and REST surfaces."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from django.test import Client
from django.urls import reverse

from conformance.api.run_store import run_store
from conformance.configuration_contracts import PreparedExecutionManifest
from tests.support.paths import REPO_ROOT
from tests.support.run_execution import StubbedRunExecution

pytestmark = pytest.mark.component

_SURFACE_PLAN_PATH = (
    REPO_ROOT / "tests" / "fixtures" / "configuration_contracts" / "pis" / "v4_0_1" / "participant-plan.surface.json"
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
) -> None:
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
    assert "Requirements scope and capabilities" in scope_content
    assert "Payment Initiation" in scope_content
    assert "GET /open-banking" not in scope_content

    response = client.post(
        response["Location"],
        data={
            "requirements_scope": "pis",
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
    assert "resourceGroups" not in exported_plan
    assert "endpoints" not in exported_plan

    launch_response = client.post(reverse("builder-launch", kwargs={"draft_id": draft_id}))
    assert launch_response.status_code == 302
    launch = stubbed_run_execution.wait_for_launch()
    prepared_manifest = launch.kwargs["prepared_execution_manifest"]
    assert isinstance(prepared_manifest, PreparedExecutionManifest)
    assert prepared_manifest.manifest is not None


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
    launch = stubbed_run_execution.wait_for_launch()
    assert isinstance(launch.kwargs["prepared_execution_manifest"], PreparedExecutionManifest)


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
    raw_plan["suiteReleaseId"] = "obl.open-banking-mvp.catalogue-release"
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
    prepared = launch.kwargs["prepared_execution_manifest"]
    assert isinstance(prepared, PreparedExecutionManifest)
    assert prepared.result_traceability is not None
    assert prepared.result_traceability.result_observation_id_by_manifest_step_id == {
        "dcr.v34.test.registration.empty-issuer.instance.request": "DCR-004-C03-S02",
        "dcr.v34.test.registration.expired.instance.request": "DCR-004-C01-S02",
        "dcr.v34.test.registration.invalid-auth-method.instance.request": "DCR-004-C05-S02",
        "dcr.v34.test.registration.invalid-issuer.instance.request": "DCR-004-C02-S02",
        "dcr.v34.test.registration.overlong-issuer.instance.request": "DCR-004-C04-S02",
        "dcr.v34.test.registration.positive.instance.request": "DCR-002-C01-S02",
    }
