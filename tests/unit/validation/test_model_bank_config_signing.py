"""Model-bank config parsing of the FAPI signing section and token-endpoint auth method."""

from pathlib import Path

import pytest

from conformance.json_types import JsonValue
from conformance.model_bank_config import (
    ConfigError,
    FapiSigningConfig,
    parse_model_bank_config,
)

pytestmark = pytest.mark.unit


def _write_signing_material(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Write signing certificate fixtures under a certificate root.

    Args:
        tmp_path: Temporary directory used to host fixture files.

    Returns:
        A tuple of ``(certificate_root, certificate_path, private_key_path)``.
    """
    certificate_root = tmp_path / "certs"
    certificate_root.mkdir()
    certificate_path = certificate_root / "signing.crt"
    private_key_path = certificate_root / "signing.key"
    certificate_path.write_text("certificate", encoding="utf-8")
    private_key_path.write_text("private-key", encoding="utf-8")
    return certificate_root, certificate_path, private_key_path


def test_parse_model_bank_config_accepts_fapi_signing_section(tmp_path: Path) -> None:
    """A valid ``fapiSigning`` object is parsed into a dedicated config model."""
    _, certificate_path, private_key_path = _write_signing_material(tmp_path)

    config = parse_model_bank_config(
        {
            "environment": "sandbox",
            "discoveryUrl": "https://auth.example.com/.well-known/openid-configuration",
            "fapiSigning": {
                "signingCertificatePath": str(certificate_path),
                "signingPrivateKeyPath": str(private_key_path),
                "kid": "signing-key-001",
                "clientAssertionIssuer": "client-issuer",
                "clientAssertionSubject": "client-subject",
                "tokenEndpointAuthMethod": "private_key_jwt",
            },
        },
        base_dir=tmp_path,
    )
    assert config.fapi_signing == FapiSigningConfig(
        signing_certificate_path=certificate_path,
        signing_private_key_path=private_key_path,
        key_id="signing-key-001",
        client_assertion_issuer="client-issuer",
        client_assertion_subject="client-subject",
        token_endpoint_auth_method="private_key_jwt",  # noqa: S106 - auth-method enum fixture, not a secret
    )


def test_parse_model_bank_config_keeps_fapi_signing_optional(tmp_path: Path) -> None:
    """Config without ``fapiSigning`` must keep the dedicated signing model absent."""
    config = parse_model_bank_config(
        {
            "environment": "sandbox",
            "discoveryUrl": "https://auth.example.com/.well-known/openid-configuration",
        },
        base_dir=tmp_path,
    )

    assert config.fapi_signing is None


def test_parse_model_bank_config_accepts_missing_signing_files_until_runtime(tmp_path: Path) -> None:
    """Signing file existence must be deferred until runtime credential loading."""
    certificate_root = tmp_path / "certs"
    certificate_root.mkdir()

    config = parse_model_bank_config(
        {
            "environment": "sandbox",
            "discoveryUrl": "https://auth.example.com/.well-known/openid-configuration",
            "fapiSigning": {
                "signingCertificatePath": str(certificate_root / "missing.crt"),
                "signingPrivateKeyPath": str(certificate_root / "missing.key"),  # pragma: allowlist secret
                "kid": "signing-key-001",
                "clientAssertionIssuer": "client-issuer",
                "clientAssertionSubject": "client-subject",
                "tokenEndpointAuthMethod": "private_key_jwt",
            },
        },
        base_dir=tmp_path,
    )
    assert config.fapi_signing is not None
    assert config.fapi_signing is not None
    assert config.fapi_signing.signing_certificate_path == certificate_root / "missing.crt"
    assert config.fapi_signing.signing_private_key_path == certificate_root / "missing.key"


def test_parse_model_bank_config_rejects_non_object_fapi_signing(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="fapiSigning must be a JSON object"):
        parse_model_bank_config(
            {
                "environment": "sandbox",
                "discoveryUrl": "https://auth.example.com/.well-known/openid-configuration",
                "fapiSigning": "private_key_jwt",
            },
            base_dir=tmp_path,
        )


def test_parse_model_bank_config_rejects_unknown_fapi_signing_field(tmp_path: Path) -> None:
    certificate_root, certificate_path, private_key_path = _write_signing_material(tmp_path)

    with pytest.raises(ConfigError, match=r"Unknown fapiSigning field\(s\): certificatePathRoot, jwk"):
        parse_model_bank_config(
            {
                "environment": "sandbox",
                "discoveryUrl": "https://auth.example.com/.well-known/openid-configuration",
                "fapiSigning": {
                    "certificatePathRoot": certificate_root.name,
                    "signingCertificatePath": str(certificate_path),
                    "signingPrivateKeyPath": str(private_key_path),
                    "kid": "signing-key-001",
                    "clientAssertionIssuer": "client-issuer",
                    "clientAssertionSubject": "client-subject",
                    "tokenEndpointAuthMethod": "private_key_jwt",
                    "jwk": "should-not-be-here",
                },
            },
            base_dir=tmp_path,
        )


def test_parse_model_bank_config_requires_signing_cert_and_key_together(tmp_path: Path) -> None:
    certificate_root, certificate_path, _ = _write_signing_material(tmp_path)

    with pytest.raises(
        ConfigError,
        match="fapiSigning.signingCertificatePath and fapiSigning.signingPrivateKeyPath must be supplied together",
    ):
        parse_model_bank_config(
            {
                "environment": "sandbox",
                "discoveryUrl": "https://auth.example.com/.well-known/openid-configuration",
                "fapiSigning": {
                    "signingCertificatePath": str(certificate_path),
                    "kid": "signing-key-001",
                    "clientAssertionIssuer": "client-issuer",
                    "clientAssertionSubject": "client-subject",
                    "tokenEndpointAuthMethod": "private_key_jwt",
                },
            },
            base_dir=tmp_path,
        )


def test_parse_model_bank_config_rejects_relative_fapi_signing_path(tmp_path: Path) -> None:
    certificate_root, _, private_key_path = _write_signing_material(tmp_path)

    with pytest.raises(ConfigError, match="signingCertificatePath must be an absolute file path"):
        parse_model_bank_config(
            {
                "environment": "sandbox",
                "discoveryUrl": "https://auth.example.com/.well-known/openid-configuration",
                "fapiSigning": {
                    "signingCertificatePath": "outside.crt",
                    "signingPrivateKeyPath": str(private_key_path),
                    "kid": "signing-key-001",
                    "clientAssertionIssuer": "client-issuer",
                    "clientAssertionSubject": "client-subject",
                    "tokenEndpointAuthMethod": "private_key_jwt",
                },
            },
            base_dir=tmp_path,
        )


def test_parse_model_bank_config_rejects_unknown_token_endpoint_auth_method(tmp_path: Path) -> None:
    _, certificate_path, private_key_path = _write_signing_material(tmp_path)

    with pytest.raises(
        ConfigError,
        match="fapiSigning.tokenEndpointAuthMethod must be one of: private_key_jwt, tls_client_auth",
    ):
        parse_model_bank_config(
            {
                "environment": "sandbox",
                "discoveryUrl": "https://auth.example.com/.well-known/openid-configuration",
                "fapiSigning": {
                    "signingCertificatePath": str(certificate_path),
                    "signingPrivateKeyPath": str(private_key_path),
                    "kid": "signing-key-001",
                    "clientAssertionIssuer": "client-issuer",
                    "clientAssertionSubject": "client-subject",
                    "tokenEndpointAuthMethod": "client_secret_basic",
                },
            },
            base_dir=tmp_path,
        )


@pytest.mark.parametrize(
    ("field_name", "value", "expected_message"),
    [
        ("kid", "", "fapiSigning.kid must be a non-empty string"),
        (
            "clientAssertionIssuer",
            "   ",
            "fapiSigning.clientAssertionIssuer must be a non-empty string",
        ),
        (
            "clientAssertionSubject",
            42,
            "fapiSigning.clientAssertionSubject must be a non-empty string",
        ),
    ],
)
def test_parse_model_bank_config_rejects_malformed_fapi_signing_strings(
    field_name: str,
    value: JsonValue,
    expected_message: str,
    tmp_path: Path,
) -> None:
    certificate_root, certificate_path, private_key_path = _write_signing_material(tmp_path)

    fapi_signing: dict[str, JsonValue] = {
        "signingCertificatePath": str(certificate_path),
        "signingPrivateKeyPath": str(private_key_path),
        "kid": "signing-key-001",
        "clientAssertionIssuer": "client-issuer",
        "clientAssertionSubject": "client-subject",
        "tokenEndpointAuthMethod": "private_key_jwt",
    }
    fapi_signing[field_name] = value

    with pytest.raises(ConfigError, match=expected_message):
        parse_model_bank_config(
            {
                "environment": "sandbox",
                "discoveryUrl": "https://auth.example.com/.well-known/openid-configuration",
                "fapiSigning": fapi_signing,
            },
            base_dir=tmp_path,
        )
