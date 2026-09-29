"""Component test for the DCR browser builder flow through Django."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from django.test import Client

from conformance.api.builder_draft_store import SessionBuilderDraftStore
from conformance.api.builder_wizard import catalogue_scope_hierarchy
from conformance.catalogue import PlanDocumentBoundary

pytestmark = pytest.mark.component


@pytest.mark.django_db
@patch("conformance.api.ui_views._fetch_discovery_metadata")
def test_dcr_browser_flow_reviews_and_exports_canonical_plan(
    mock_fetch_discovery: Mock,
    tmp_path: Path,
) -> None:
    """DCR browser routing persists direct scope and exports references without secret contents."""
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
    assert "Required and locked" in scope_page.content.decode()

    boundary = PlanDocumentBoundary("open-banking-uk", "dynamic-client-registration", "3.4")
    get_endpoint = next(
        endpoint for endpoint in catalogue_scope_hierarchy(boundary).direct_endpoints if endpoint.method == "GET"
    )
    scope_saved = client.post(selected["Location"], data={"endpoints": [get_endpoint.id]})
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
    assert "OBL_DCR" in review_page.content.decode()

    draft_id = reviewed["Location"].split("/")[2]
    exported = client.get(f"/builder/{draft_id}/export.json")
    assert exported.status_code == 200
    plan = exported.json()
    assert plan["specification"]["family"] == "OBL_DCR"
    assert [endpoint["method"] for endpoint in plan["endpoints"]] == ["POST", "GET"]
    assert plan["dynamicClientRegistration"]["softwareStatementAssertionPath"] == str(tmp_path / "ssa.jwt")
    assert plan["dynamicClientRegistration"]["registrationAudience"] == "aspsp123"
    assert plan["securityEnvironment"]["signingPrivateKeyPath"] == str(tmp_path / "signing.key")
    assert "resourceGroups" not in plan
    assert "businessTestData" not in plan
    assert "token" not in {endpoint["path"] for endpoint in plan["endpoints"]}
    assert "***" not in json.dumps(plan)
    imported = client.post("/builder/import/", data={"plan_json": json.dumps(plan)})
    assert imported.status_code == 302
    assert imported["Location"].endswith("/review/")

    store = SessionBuilderDraftStore(client.session)
    draft = store.get(draft_id)
    assert draft is not None
    assert draft.dynamic_client_registration["softwareStatementAssertionPath"] == str(tmp_path / "ssa.jwt")
