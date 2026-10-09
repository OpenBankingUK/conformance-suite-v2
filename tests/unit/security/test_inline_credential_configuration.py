"""Unit tests for inline PEM credentials across config, schema, and execution."""

from __future__ import annotations

from pathlib import Path

import pytest

from conformance.credentials import credential_from_inline
from conformance.dcr_execution import certificate_subject_dn
from conformance.model_bank_config import ConfigError, parse_model_bank_config
from conformance.plan_configuration import parse_dcr_plan_configuration, validate_dcr_file_references
from conformance.signing_credentials import load_signing_credentials
from conformance.test_plan_validation import validate_test_plan_for_load
from tests.support.executor_signing import write_signing_pair

pytestmark = pytest.mark.unit


def _signing_pem(tmp_path: Path, *, stem: str) -> tuple[str, str]:
    """Generate a real signing keypair as PEM text.

    Args:
        tmp_path: Temporary directory used to hold the generated files.
        stem: File-stem prefix for the generated pair.

    Returns:
        Tuple of ``(certificate_pem, private_key_pem)``.
    """
    certificate_path, private_key_path = write_signing_pair(tmp_path, stem=stem)
    return (
        certificate_path.read_text(encoding="utf-8"),
        private_key_path.read_text(encoding="utf-8"),
    )


def test_model_bank_config_accepts_inline_signing_and_tls_material(tmp_path: Path) -> None:
    """Inline PEM siblings configure signing and TLS without any file reference."""
    certificate_pem, private_key_pem = _signing_pem(tmp_path, stem="inline-config")

    config = parse_model_bank_config(
        {
            "environment": "sandbox",
            "discoveryUrl": "https://auth.example.com/.well-known/openid-configuration",
            "fapiSigning": {
                "signingCertificatePem": certificate_pem,
                "signingPrivateKeyPem": private_key_pem,
                "kid": "signing-key-001",
                "clientAssertionIssuer": "client-issuer",
                "clientAssertionSubject": "client-subject",
                "tokenEndpointAuthMethod": "private_key_jwt",
            },
            "tls": {
                "caBundlePem": certificate_pem,
                "clientCertificatePem": certificate_pem,
                "clientPrivateKeyPem": private_key_pem,
            },
        },
        base_dir=tmp_path,
    )

    assert config.fapi_signing is not None
    assert config.fapi_signing.signing_private_key == credential_from_inline(private_key_pem)
    assert config.fapi_signing.signing_certificate is not None
    assert config.fapi_signing.signing_certificate.path is None
    assert config.tls.client_certificate == credential_from_inline(certificate_pem)
    assert config.tls.ca_bundle == credential_from_inline(certificate_pem)


def test_model_bank_config_rejects_a_credential_supplied_twice(tmp_path: Path) -> None:
    """A credential may be a path or inline material, never both."""
    certificate_path, private_key_path = write_signing_pair(tmp_path, stem="duplicate")

    with pytest.raises(ConfigError) as failure:
        parse_model_bank_config(
            {
                "environment": "sandbox",
                "discoveryUrl": "https://auth.example.com/.well-known/openid-configuration",
                "tls": {
                    "clientCertificatePath": str(certificate_path),
                    "clientCertificatePem": certificate_path.read_text(encoding="utf-8"),
                    "clientPrivateKeyPath": str(private_key_path),
                },
            },
            base_dir=tmp_path,
        )

    assert "clientCertificate" in str(failure.value)


def test_signing_credentials_load_from_inline_material(tmp_path: Path) -> None:
    """Signing credentials load from pasted PEM exactly as from a file path."""
    certificate_pem, private_key_pem = _signing_pem(tmp_path, stem="inline-signing")
    config = parse_model_bank_config(
        {
            "environment": "sandbox",
            "discoveryUrl": "https://auth.example.com/.well-known/openid-configuration",
            "fapiSigning": {
                "signingCertificatePem": certificate_pem,
                "signingPrivateKeyPem": private_key_pem,
                "kid": "signing-key-001",
                "clientAssertionIssuer": "client-issuer",
                "clientAssertionSubject": "client-subject",
                "tokenEndpointAuthMethod": "private_key_jwt",
            },
        },
        base_dir=tmp_path,
    )
    assert config.fapi_signing is not None

    credentials = load_signing_credentials(config.fapi_signing)

    assert credentials.signing_certificate_pem == certificate_pem.encode("utf-8")
    assert credentials.signing_private_key_pem == private_key_pem.encode("utf-8")


def test_dcr_configuration_accepts_inline_credentials_and_skips_file_checks(tmp_path: Path) -> None:
    """Inline DCR credentials parse and are deliberately not filesystem-checked."""
    certificate_pem, private_key_pem = _signing_pem(tmp_path, stem="inline-dcr")

    config = parse_dcr_plan_configuration(
        {
            "discoveryUrl": "https://aspsp.example.com/.well-known/openid-configuration",
            "signingPrivateKeyPem": private_key_pem,
            "signingKeyId": "kid-123",
            "clientAuthMethod": "private_key_jwt",
            "mtls": {
                "enabled": True,
                "certificatePem": certificate_pem,
                "privateKeyPem": private_key_pem,
            },
        },
        {
            "softwareStatementAssertion": "header.payload.signature",
            "registrationAudience": "aspsp123",
        },
        {},
    )

    assert config.shared.signing.private_key == credential_from_inline(private_key_pem)
    assert config.dynamic_client_registration.software_statement_assertion is not None
    assert config.dynamic_client_registration.software_statement_assertion.inline == "header.payload.signature"
    validate_dcr_file_references(config)


def test_subject_dn_is_derived_from_an_inline_certificate(tmp_path: Path) -> None:
    """Subject-DN derivation reads inline certificate material directly."""
    certificate_pem, _ = _signing_pem(tmp_path, stem="inline-subject-dn")

    subject_dn = certificate_subject_dn(
        credential_from_inline(certificate_pem),
        override=None,
        numeric_oids=False,
    )

    assert "CN=inline-subject-dn" in subject_dn


def _plan_document(security_environment: dict[str, object]) -> dict[str, object]:
    """Build a minimal canonical DCR plan document for schema validation.

    Args:
        security_environment: Canonical ``securityEnvironment`` object to embed.

    Returns:
        Canonical plan document ready for schema validation.
    """
    return {
        "schemaVersion": "1.0",
        "specification": {
            "family": "OBL_DCR",
            "scheme": "open-banking-uk",
            "name": "dynamic-client-registration",
            "version": "3.4",
        },
        "executionMode": "development",
        "securityEnvironment": security_environment,
        "endpoints": [
            {
                "method": "POST",
                "path": "/register",
                "operationId": "RegisterClient",
                "required": True,
                "locked": True,
            }
        ],
        "dynamicClientRegistration": {
            "softwareStatementAssertion": "header.payload.signature",
            "registrationAudience": "aspsp123",
        },
        "metadata": {"aspspName": "Example Bank"},
    }


def test_plan_schema_accepts_inline_credentials(tmp_path: Path) -> None:
    """The canonical schema accepts inline PEM alongside the path form."""
    certificate_pem, private_key_pem = _signing_pem(tmp_path, stem="schema-inline")

    document = _plan_document(
        {
            "discoveryUrl": "https://aspsp.example.com/.well-known/openid-configuration",
            "signingPrivateKeyPem": private_key_pem,
            "signingKeyId": "kid-123",
            "clientAuthMethod": "private_key_jwt",
            "mtls": {"enabled": True, "certificatePem": certificate_pem, "privateKeyPem": private_key_pem},
        }
    )

    result = validate_test_plan_for_load(document)

    assert result.valid, result.summary_message


def test_plan_schema_rejects_a_credential_supplied_as_both_path_and_pem(tmp_path: Path) -> None:
    """The canonical schema rejects a path and inline value for one credential."""
    certificate_pem, private_key_pem = _signing_pem(tmp_path, stem="schema-both")

    document = _plan_document(
        {
            "discoveryUrl": "https://aspsp.example.com/.well-known/openid-configuration",
            "signingPrivateKeyPath": "/certs/signing.key",
            "signingPrivateKeyPem": private_key_pem,
            "signingKeyId": "kid-123",
            "clientAuthMethod": "private_key_jwt",
            "mtls": {"enabled": True, "certificatePem": certificate_pem, "privateKeyPem": private_key_pem},
        }
    )

    result = validate_test_plan_for_load(document)

    assert not result.valid
