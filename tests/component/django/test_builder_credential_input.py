"""Component tests for supplying wizard credentials by paste and upload."""

from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from django.test import Client

from conformance.api.builder_draft_store import SessionBuilderDraftStore
from conformance.api.builder_wizard import catalogue_scope_hierarchy
from conformance.catalogue import PlanDocumentBoundary
from conformance.credentials import MAX_INLINE_CREDENTIAL_BYTES
from tests.support.executor_signing import write_signing_pair

pytestmark = pytest.mark.component


def _dcr_draft_security_url(client: Client) -> str:
    """Advance a new DCR draft to its security step.

    Args:
        client: Django test client that owns the builder session.

    Returns:
        URL of the security configuration step for the new draft.
    """
    created = client.post("/builder/new/")
    selected = client.post(
        created["Location"],
        data={
            "scheme": "open-banking-uk",
            "specification": "dynamic-client-registration",
            "version": "3.4",
        },
    )
    boundary = PlanDocumentBoundary("open-banking-uk", "dynamic-client-registration", "3.4")
    get_endpoint = next(
        endpoint for endpoint in catalogue_scope_hierarchy(boundary).direct_endpoints if endpoint.method == "GET"
    )
    scope_saved = client.post(selected["Location"], data={"endpoints": [get_endpoint.id]})
    discovery_saved = client.post(
        scope_saved["Location"],
        data={"discovery_url": "https://aspsp.example.com/.well-known/openid-configuration"},
    )
    return str(discovery_saved["Location"])


def _dcr_form_data(**overrides: object) -> dict[str, object]:
    """Build DCR security-step form data carrying no credentials.

    Args:
        overrides: Credential and override fields to merge into the submission.

    Returns:
        Form data for the DCR security step.
    """
    data: dict[str, object] = {
        "signing_kid": "kid-123",
        "signing_token_endpoint_auth_method": "private_key_jwt",
        "signing_client_auth_algorithm": "PS256",
        "dcr_registration_audience": "aspsp123",
        "dcr_execution_mode": "certification",
        "metadata_brand_name": "Retail",
    }
    data.update(overrides)
    return data


@pytest.mark.django_db
@patch("conformance.api.ui_views._fetch_discovery_metadata")
def test_security_step_offers_paste_and_upload_for_every_credential(
    mock_fetch_discovery: Mock,
) -> None:
    """The security page exposes path, paste, and upload inputs for credentials."""
    mock_fetch_discovery.return_value = {"token_endpoint_auth_methods_supported": ["private_key_jwt"]}
    client = Client()
    page = client.get(_dcr_draft_security_url(client))

    content = page.content.decode("utf-8")
    assert page.status_code == 200
    assert 'enctype="multipart/form-data"' in content
    for field in (
        "signing_private_key",
        "tls_client_certificate",
        "tls_client_private_key",
        "dcr_software_statement_assertion",
    ):
        assert f'name="{field}_path"' in content
        assert f'name="{field}_pem"' in content
        assert f'name="{field}_file"' in content
        assert f'name="{field}_source"' in content


@pytest.mark.django_db
@patch("conformance.api.ui_views._fetch_discovery_metadata")
def test_pasted_and_uploaded_credentials_are_stored_inline_and_never_re_rendered(
    mock_fetch_discovery: Mock,
    tmp_path: Path,
) -> None:
    """Pasted and uploaded credentials persist inline without reaching the page again."""
    mock_fetch_discovery.return_value = {"token_endpoint_auth_methods_supported": ["private_key_jwt"]}
    certificate_path, private_key_path = write_signing_pair(tmp_path, stem="paste")
    certificate_pem = certificate_path.read_text(encoding="utf-8")
    private_key_pem = private_key_path.read_text(encoding="utf-8")
    assertion = "header.payload.signature"
    client = Client()
    security_url = _dcr_draft_security_url(client)

    saved = client.post(
        security_url,
        data=_dcr_form_data(
            signing_private_key_pem=private_key_pem,
            tls_client_certificate_pem=certificate_pem,
            tls_client_private_key_file=BytesIO(private_key_pem.encode("utf-8")),
            dcr_software_statement_assertion_pem=assertion,
        ),
    )
    assert saved.status_code == 302, saved.content.decode("utf-8")

    draft_id = str(saved["Location"]).split("/")[2]
    draft = SessionBuilderDraftStore(client.session).get(draft_id)
    assert draft is not None
    assert draft.security_environment["signingPrivateKeyPem"] == private_key_pem
    assert "signingPrivateKeyPath" not in draft.security_environment
    mtls = draft.security_environment["mtls"]
    assert isinstance(mtls, dict)
    assert mtls["certificatePem"] == certificate_pem
    assert mtls["privateKeyPem"] == private_key_pem
    assert draft.dynamic_client_registration["softwareStatementAssertion"] == assertion

    revisited = client.get(security_url).content.decode("utf-8")
    assert "BEGIN PRIVATE KEY" not in revisited  # pragma: allowlist secret - PEM armour marker
    assert "BEGIN CERTIFICATE" not in revisited
    assert assertion not in revisited
    assert revisited.count("Configured") >= 4


@pytest.mark.django_db
@patch("conformance.api.ui_views._fetch_discovery_metadata")
def test_stored_inline_credentials_survive_a_resubmission_that_keeps_them(
    mock_fetch_discovery: Mock,
    tmp_path: Path,
) -> None:
    """Re-saving the step without re-entering credentials keeps the stored material."""
    mock_fetch_discovery.return_value = {"token_endpoint_auth_methods_supported": ["private_key_jwt"]}
    certificate_path, private_key_path = write_signing_pair(tmp_path, stem="keep")
    certificate_pem = certificate_path.read_text(encoding="utf-8")
    private_key_pem = private_key_path.read_text(encoding="utf-8")
    client = Client()
    security_url = _dcr_draft_security_url(client)
    client.post(
        security_url,
        data=_dcr_form_data(
            signing_private_key_pem=private_key_pem,
            tls_client_certificate_pem=certificate_pem,
            tls_client_private_key_pem=private_key_pem,
            dcr_software_statement_assertion_pem="header.payload.signature",
        ),
    )

    resaved = client.post(security_url, data=_dcr_form_data(dcr_registration_audience="aspsp456"))
    assert resaved.status_code == 302, resaved.content.decode("utf-8")

    draft_id = str(resaved["Location"]).split("/")[2]
    draft = SessionBuilderDraftStore(client.session).get(draft_id)
    assert draft is not None
    assert draft.security_environment["signingPrivateKeyPem"] == private_key_pem
    assert draft.dynamic_client_registration["registrationAudience"] == "aspsp456"


@pytest.mark.django_db
@patch("conformance.api.ui_views._fetch_discovery_metadata")
def test_clearing_a_stored_credential_reports_it_as_missing(
    mock_fetch_discovery: Mock,
    tmp_path: Path,
) -> None:
    """Clearing a required credential removes it and blocks the step."""
    mock_fetch_discovery.return_value = {"token_endpoint_auth_methods_supported": ["private_key_jwt"]}
    certificate_path, private_key_path = write_signing_pair(tmp_path, stem="clear")
    certificate_pem = certificate_path.read_text(encoding="utf-8")
    private_key_pem = private_key_path.read_text(encoding="utf-8")
    client = Client()
    security_url = _dcr_draft_security_url(client)
    client.post(
        security_url,
        data=_dcr_form_data(
            signing_private_key_pem=private_key_pem,
            tls_client_certificate_pem=certificate_pem,
            tls_client_private_key_pem=private_key_pem,
            dcr_software_statement_assertion_pem="header.payload.signature",
        ),
    )

    cleared = client.post(security_url, data=_dcr_form_data(signing_private_key_action="clear"))

    assert cleared.status_code == 400
    assert "signing private key" in cleared.content.decode("utf-8").lower()


@pytest.mark.django_db
@patch("conformance.api.ui_views._fetch_discovery_metadata")
def test_pasting_the_wrong_artefact_is_rejected_without_echoing_material(
    mock_fetch_discovery: Mock,
    tmp_path: Path,
) -> None:
    """A certificate pasted into a private-key field is rejected immediately."""
    mock_fetch_discovery.return_value = {"token_endpoint_auth_methods_supported": ["private_key_jwt"]}
    certificate_path, _ = write_signing_pair(tmp_path, stem="wrong-artefact")
    certificate_pem = certificate_path.read_text(encoding="utf-8")
    client = Client()
    security_url = _dcr_draft_security_url(client)

    rejected = client.post(security_url, data=_dcr_form_data(signing_private_key_pem=certificate_pem))

    assert rejected.status_code == 400
    body = rejected.content.decode("utf-8")
    assert "BEGIN CERTIFICATE-----\nMII" not in body
    assert "private key" in body.lower()


@pytest.mark.django_db
@patch("conformance.api.ui_views._fetch_discovery_metadata")
def test_supplying_both_a_path_and_pasted_material_is_rejected(
    mock_fetch_discovery: Mock,
    tmp_path: Path,
) -> None:
    """A credential cannot be supplied twice through different inputs."""
    mock_fetch_discovery.return_value = {"token_endpoint_auth_methods_supported": ["private_key_jwt"]}
    _, private_key_path = write_signing_pair(tmp_path, stem="both")
    client = Client()
    security_url = _dcr_draft_security_url(client)

    rejected = client.post(
        security_url,
        data=_dcr_form_data(
            signing_private_key_path=str(private_key_path),
            signing_private_key_pem=private_key_path.read_text(encoding="utf-8"),
        ),
    )

    assert rejected.status_code == 400
    assert "only one" in rejected.content.decode("utf-8").lower()


@pytest.mark.django_db
@patch("conformance.api.ui_views._fetch_discovery_metadata")
def test_inline_credentials_are_stripped_from_a_safe_plan_export(
    mock_fetch_discovery: Mock,
    tmp_path: Path,
) -> None:
    """Exported plans must never carry inline credential material."""
    mock_fetch_discovery.return_value = {"token_endpoint_auth_methods_supported": ["private_key_jwt"]}
    certificate_path, private_key_path = write_signing_pair(tmp_path, stem="export")
    certificate_pem = certificate_path.read_text(encoding="utf-8")
    private_key_pem = private_key_path.read_text(encoding="utf-8")
    client = Client()
    security_url = _dcr_draft_security_url(client)
    saved = client.post(
        security_url,
        data=_dcr_form_data(
            signing_private_key_pem=private_key_pem,
            tls_client_certificate_pem=certificate_pem,
            tls_client_private_key_pem=private_key_pem,
            dcr_software_statement_assertion_pem="header.payload.signature",
        ),
    )
    draft_id = str(saved["Location"]).split("/")[2]

    exported = client.get(f"/builder/{draft_id}/export.json")

    assert exported.status_code == 200
    body = json.dumps(exported.json())
    assert "BEGIN PRIVATE KEY" not in body  # pragma: allowlist secret - PEM armour marker
    assert "BEGIN CERTIFICATE" not in body
    assert "header.payload.signature" not in body


@pytest.mark.django_db
@patch("conformance.api.ui_views._fetch_discovery_metadata")
def test_an_oversized_upload_is_rejected(mock_fetch_discovery: Mock) -> None:
    """Uploads above the inline ceiling are refused before any material is stored."""
    mock_fetch_discovery.return_value = {"token_endpoint_auth_methods_supported": ["private_key_jwt"]}
    client = Client()
    security_url = _dcr_draft_security_url(client)
    oversized = BytesIO(b"-----BEGIN PRIVATE KEY-----\n" + b"A" * (MAX_INLINE_CREDENTIAL_BYTES + 1))
    oversized.name = "huge.key"

    rejected = client.post(security_url, data=_dcr_form_data(signing_private_key_file=oversized))

    assert rejected.status_code == 400
    assert str(MAX_INLINE_CREDENTIAL_BYTES) in rejected.content.decode("utf-8")


@pytest.mark.django_db
@patch("conformance.api.ui_views._fetch_discovery_metadata")
def test_a_binary_upload_is_rejected_as_not_utf8(mock_fetch_discovery: Mock) -> None:
    """A DER or otherwise binary credential file is refused with clear guidance."""
    mock_fetch_discovery.return_value = {"token_endpoint_auth_methods_supported": ["private_key_jwt"]}
    client = Client()
    security_url = _dcr_draft_security_url(client)
    binary = BytesIO(b"\x30\x82\x01\x0a\x02\x82\x01\x01\x00\xff\xfe")
    binary.name = "transport.der"

    rejected = client.post(security_url, data=_dcr_form_data(tls_client_certificate_file=binary))

    assert rejected.status_code == 400
    assert "UTF-8" in rejected.content.decode("utf-8")


@pytest.mark.django_db
@patch("conformance.api.ui_views._fetch_discovery_metadata")
def test_a_relative_credential_path_is_rejected(mock_fetch_discovery: Mock) -> None:
    """Credential paths must be absolute so container mounts resolve predictably."""
    mock_fetch_discovery.return_value = {"token_endpoint_auth_methods_supported": ["private_key_jwt"]}
    client = Client()
    security_url = _dcr_draft_security_url(client)

    rejected = client.post(security_url, data=_dcr_form_data(signing_private_key_path="certs/signing.key"))

    assert rejected.status_code == 400
    assert "absolute file path" in rejected.content.decode("utf-8").lower()


@pytest.mark.django_db
@patch("conformance.api.ui_views._fetch_discovery_metadata")
def test_a_stored_inline_ca_bundle_is_summarised_without_material(
    mock_fetch_discovery: Mock,
    tmp_path: Path,
) -> None:
    """Revisiting the page summarises a stored CA bundle instead of echoing it."""
    mock_fetch_discovery.return_value = {"token_endpoint_auth_methods_supported": ["private_key_jwt"]}
    certificate_path, private_key_path = write_signing_pair(tmp_path, stem="ca-descriptor")
    certificate_pem = certificate_path.read_text(encoding="utf-8")
    private_key_pem = private_key_path.read_text(encoding="utf-8")
    client = Client()
    security_url = _dcr_draft_security_url(client)
    client.post(
        security_url,
        data=_dcr_form_data(
            signing_private_key_pem=private_key_pem,
            tls_client_certificate_pem=certificate_pem,
            tls_client_private_key_pem=private_key_pem,
            tls_ca_bundle_pem=certificate_pem,
            dcr_software_statement_assertion_pem="header.payload.signature",
        ),
    )

    revisited = client.get(security_url).content.decode("utf-8")

    assert "Pasted CA bundle (1 certificate)" in revisited
    assert "BEGIN CERTIFICATE" not in revisited
