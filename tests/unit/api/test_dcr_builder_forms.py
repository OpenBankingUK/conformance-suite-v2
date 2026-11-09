"""Unit tests for the browser builder forms that capture DCR scope and credentials."""

from __future__ import annotations

from pathlib import Path

import pytest

from conformance.api.builder_wizard import ScopeSelectionForm, SecurityConfigForm, catalogue_scope_hierarchy
from conformance.catalogue import PlanDocumentBoundary

pytestmark = pytest.mark.unit


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


def _dcr_security_form(tmp_path: Path, redirect_uris: str) -> SecurityConfigForm:
    """Build a DCR security form that is valid apart from the supplied redirect overrides."""
    return SecurityConfigForm(
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
            "dcr_redirect_uris_override": redirect_uris,
        },
        dcr_mode=True,
    )


@pytest.mark.parametrize(
    "redirect_uris",
    [
        "https://127.0.0.1:8443/conformancesuite/callback",
        "https://[::1]:8443/conformancesuite/callback",
        "https://0.0.0.0:8443/conformancesuite/callback",
        "https://127.0.0.1:8443/conformancesuite/callback\nhttps://tpp.example.com/callback",
    ],
)
def test_dcr_redirect_override_accepts_supported_callbacks(tmp_path: Path, redirect_uris: str) -> None:
    """The DCR override form applies the callback policy, not the outbound IP-literal restriction."""
    form = _dcr_security_form(tmp_path, redirect_uris)

    assert form.is_valid(), form.errors.as_json()
    assert form.dynamic_client_registration is not None
    assert form.dynamic_client_registration["redirectUrisOverride"] == redirect_uris.split("\n")


@pytest.mark.parametrize(
    "redirect_uri",
    [
        "https://localhost:8443/callback",
        "https://tpp.example.com/callback#fragment",
        "https://0.0.0.0:9443/callback",
        "http://127.0.0.1:8443/callback",
    ],
)
def test_dcr_redirect_override_rejects_unsupported_callbacks(tmp_path: Path, redirect_uri: str) -> None:
    form = _dcr_security_form(tmp_path, redirect_uri)

    assert form.is_valid() is False
    assert list(form.errors) == ["dcr_redirect_uris_override"]
