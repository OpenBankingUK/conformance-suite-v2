"""Component test for the DCR browser builder flow through Django."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from django.test import Client

from conformance.api.builder_draft_store import SessionBuilderDraftStore

pytestmark = pytest.mark.component


@pytest.mark.django_db
@patch("conformance.api.ui_views._fetch_discovery_metadata")
def test_dcr_browser_flow_reviews_and_exports_participant_plan(
    mock_fetch_discovery: Mock,
    tmp_path: Path,
) -> None:
    """DCR browser routing selects catalogue capabilities and exports one participant plan."""
    mock_fetch_discovery.return_value = {
        "token_endpoint_auth_methods_supported": ["private_key_jwt"],
        "token_endpoint_auth_signing_alg_values_supported": ["PS256"],
    }
    for name in ("transport.crt", "transport.key", "ca.pem", "signing.key", "ssa.jwt"):
        (tmp_path / name).touch()
    client = Client()
    created = client.post("/builder/new/")
    selected = client.post(
        created["Location"],
        data={
            "scheme": "open-banking-uk",
            "specification": "dynamic-client-registration",
            "version": "3.4",
        },
    )
    assert selected["Location"].endswith("/scope/")
    scope_page = client.get(selected["Location"])
    assert "Dynamic client retrieval" in scope_page.content.decode()
    scope_saved = client.post(
        selected["Location"],
        data={
            "test_scope": "dcr",
            "capabilities": ["dcr.v34.capability.retrieval"],
        },
    )
    discovery_saved = client.post(
        scope_saved["Location"],
        data={"discovery_url": "https://aspsp.example.com/.well-known/openid-configuration"},
    )
    reviewed = client.post(
        discovery_saved["Location"],
        data={
            "signing_private_key_path": str(tmp_path / "signing.key"),
            "signing_kid": "kid-123",
            "signing_token_endpoint_auth_method": "private_key_jwt",
            "signing_client_auth_algorithm": "PS256",
            "tls_ca_bundle_path": str(tmp_path / "ca.pem"),
            "tls_client_certificate_path": str(tmp_path / "transport.crt"),
            "tls_client_private_key_path": str(tmp_path / "transport.key"),
            "dcr_software_statement_assertion_path": str(tmp_path / "ssa.jwt"),
            "dcr_registration_audience": "aspsp123",
            "dcr_execution_mode": "certification",
            "metadata_brand_name": "Retail",
        },
    )
    assert reviewed["Location"].endswith("/review/")
    review_page = client.get(reviewed["Location"])
    assert review_page.status_code == 200
    assert "dcr.v34.test.retrieval.positive.instance" in review_page.content.decode()

    draft_id = reviewed["Location"].split("/")[2]
    exported = client.get(f"/builder/{draft_id}/export.json")
    assert exported.status_code == 200
    plan = exported.json()
    assert plan["documentType"] == "participant-plan"
    assert plan["specification"]["testScope"] == "dcr"
    assert plan["selectedCapabilityIds"] == ["dcr.v34.capability.retrieval"]
    execution = plan["executionConfiguration"]
    assert execution["dynamicClientRegistration"]["softwareStatementAssertionPath"] == str(tmp_path / "ssa.jwt")
    assert execution["dynamicClientRegistration"]["registrationAudience"] == "aspsp123"
    assert execution["securityEnvironment"]["signingPrivateKeyPath"] == str(tmp_path / "signing.key")
    assert "resourceGroups" not in plan
    assert "endpoints" not in plan
    assert "***" not in json.dumps(plan)
    imported = client.post("/builder/import/", data={"plan_json": json.dumps(plan)})
    assert imported.status_code == 302
    assert imported["Location"].endswith("/review/")

    store = SessionBuilderDraftStore(client.session)
    draft = store.get(draft_id)
    assert draft is not None
    assert draft.dynamic_client_registration["softwareStatementAssertionPath"] == str(tmp_path / "ssa.jwt")
