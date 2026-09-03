"""Focused tests for Open Banking DCR configuration and builder surfaces."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from django.test import Client

from conformance.api.builder_draft_store import SessionBuilderDraftStore
from conformance.api.builder_wizard import (
    ScopeSelectionForm,
    SecurityConfigForm,
    catalogue_scope_hierarchy,
)
from conformance.catalogue import PlanDocumentBoundary
from conformance.json_types import JsonValue
from conformance.model_bank_config import ConfigError
from conformance.plan_configuration import parse_dcr_plan_configuration, validate_dcr_file_references


def _shared_security(root: Path) -> dict[str, JsonValue]:
    """Build complete shared DCR security configuration.

    Args:
        root: Absolute root used for credential references.

    Returns:
        Canonical shared security object.
    """

    return {
        "discoveryUrl": "https://aspsp.example.com/.well-known/openid-configuration",
        "clientAuthMethod": "private_key_jwt",
        "clientAuthSigningAlgorithm": "PS256",
        "signingPrivateKeyPath": str(root / "signing.key"),
        "signingKeyId": "kid-123",
        "mtls": {
            "enabled": True,
            "certificatePath": str(root / "transport.crt"),
            "privateKeyPath": str(root / "transport.key"),
            "caBundlePath": str(root / "ca.pem"),
        },
    }


def _dcr_config(root: Path) -> dict[str, JsonValue]:
    """Build complete DCR-only configuration with optional overrides.

    Args:
        root: Absolute root used for credential references.

    Returns:
        Canonical DCR-only object.
    """

    return {
        "softwareStatementAssertionPath": str(root / "ssa.jwt"),
        "registrationAudience": "aspsp123",
        "registrationIssuerOverride": "software-id",
        "redirectUrisOverride": ["https://tpp.example.com/callback"],
        "signingCertificatePath": str(root / "signing.crt"),
        "transportCertificateSubjectDnOverride": "CN=transport,O=Example",
        "useNumericOidSubjectDn": True,
        "disableKeepAlive": False,
    }


@pytest.mark.unit
def test_typed_dcr_config_reuses_shared_security_and_metadata(tmp_path: Path) -> None:
    """Typed DCR config exposes shared security and narrow DCR-only values."""
    parsed = parse_dcr_plan_configuration(
        _shared_security(tmp_path),
        _dcr_config(tmp_path),
        {"aspspName": "Bank", "brandName": "Retail", "environmentName": "Sandbox"},
    )

    assert parsed.shared.discovery_url == "https://aspsp.example.com/.well-known/openid-configuration"
    assert parsed.shared.mtls.client_certificate_path == tmp_path / "transport.crt"
    assert parsed.shared.signing.private_key_path == tmp_path / "signing.key"
    assert parsed.shared.metadata.brand_name == "Retail"
    assert parsed.dynamic_client_registration.redirect_uris_override == ("https://tpp.example.com/callback",)
    assert parsed.dynamic_client_registration.use_numeric_oid_subject_dn is True


@pytest.mark.unit
@pytest.mark.parametrize(
    ("section", "key", "message"),
    [
        ("security", "discoveryUrl", "discoveryUrl is required"),
        ("security", "signingPrivateKeyPath", "signingPrivateKeyPath is required"),
        ("security", "signingKeyId", "signingKeyId is required"),
        ("mtls", "certificatePath", "certificatePath and privateKeyPath must be supplied together"),
        ("dcr", "softwareStatementAssertionPath", "softwareStatementAssertionPath is required"),
        ("dcr", "registrationAudience", "registrationAudience must be"),
    ],
)
def test_typed_dcr_config_rejects_missing_required_references(
    tmp_path: Path,
    section: str,
    key: str,
    message: str,
) -> None:
    """Each required DCR discovery or credential reference blocks validation."""
    security = _shared_security(tmp_path)
    dcr = _dcr_config(tmp_path)
    if section == "security":
        security.pop(key)
    elif section == "mtls":
        mtls = security["mtls"]
        assert isinstance(mtls, dict)
        mtls.pop(key)
    else:
        dcr.pop(key)

    with pytest.raises(ConfigError, match=message):
        parse_dcr_plan_configuration(security, dcr, {})


@pytest.mark.unit
def test_typed_dcr_config_rejects_issuer_url_registration_audience(tmp_path: Path) -> None:
    """Runtime configuration enforces the Open Banking Base62 audience."""
    dcr = _dcr_config(tmp_path)
    dcr["registrationAudience"] = "https://aspsp.example.com/register"

    with pytest.raises(ConfigError, match="Base62 ASPSP identifier"):
        parse_dcr_plan_configuration(_shared_security(tmp_path), dcr, {})


@pytest.mark.unit
def test_dcr_runtime_file_validation_rejects_nonexistent_references(tmp_path: Path) -> None:
    """Execution validation fails rather than fabricating missing credential data."""
    parsed = parse_dcr_plan_configuration(_shared_security(tmp_path), _dcr_config(tmp_path), {})

    with pytest.raises(ConfigError, match="must reference an existing file"):
        validate_dcr_file_references(parsed)


@pytest.mark.unit
def test_direct_endpoint_form_locks_post_and_excludes_token() -> None:
    """Direct DCR scope always selects POST and offers only management operations."""
    boundary = PlanDocumentBoundary("open-banking-uk", "dynamic-client-registration", "3.4")
    hierarchy = catalogue_scope_hierarchy(boundary)
    get_endpoint = next(endpoint for endpoint in hierarchy.direct_endpoints if endpoint.method == "GET")
    form = ScopeSelectionForm(data={"endpoints": [get_endpoint.id]}, boundary=boundary)

    assert form.is_valid(), form.errors.as_json()
    selected = {endpoint.method for endpoint in hierarchy.direct_endpoints if endpoint.id in form.selected_endpoint_ids}
    assert selected == {"POST", "GET"}
    assert all(endpoint.path != "/token" for endpoint in hierarchy.direct_endpoints)


@pytest.mark.unit
def test_dcr_security_form_requires_shared_credentials_and_serialises_overrides(tmp_path: Path) -> None:
    """DCR security form blocks missing credentials and emits canonical sections."""
    missing = SecurityConfigForm(data={}, dcr_mode=True)
    assert missing.is_valid() is False
    assert "dcr_software_statement_assertion_path" in missing.errors
    assert "dcr_execution_mode" in missing.errors
    assert "tls_client_certificate_path" in missing.errors

    form = SecurityConfigForm(
        data={
            "signing_private_key_path": str(tmp_path / "signing.key"),
            "signing_kid": "kid-123",
            "signing_token_endpoint_auth_method": "client_secret_basic",
            "signing_client_auth_algorithm": "PS256",
            "tls_client_certificate_path": str(tmp_path / "transport.crt"),
            "tls_client_private_key_path": str(tmp_path / "transport.key"),
            "dcr_software_statement_assertion_path": str(tmp_path / "ssa.jwt"),
            "dcr_registration_audience": "aspsp123",
            "dcr_execution_mode": "certification",
            "dcr_redirect_uris_override": "https://tpp.example.com/one\nhttps://tpp.example.com/two",
            "dcr_use_numeric_oid_subject_dn": "on",
            "metadata_brand_name": "Retail",
        },
        dcr_mode=True,
    )

    assert form.is_valid(), form.errors.as_json()
    assert form.security_environment is not None
    assert form.security_environment["clientAuthMethod"] == "client_secret_basic"
    assert form.dynamic_client_registration is not None
    assert form.dynamic_client_registration["redirectUrisOverride"] == [
        "https://tpp.example.com/one",
        "https://tpp.example.com/two",
    ]
    assert form.metadata == {"brandName": "Retail"}


@pytest.mark.integration
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
