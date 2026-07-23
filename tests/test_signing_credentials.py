"""Unit tests for runtime FAPI signing credential loading."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from conformance.model_bank_config import FapiSigningConfig
from conformance.signing_credentials import SigningCredentialError, load_signing_credentials


def _write_signing_pair(certificate_root: Path, *, stem: str) -> tuple[Path, Path]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, stem)])
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(UTC) - timedelta(minutes=1))
        .not_valid_after(datetime.now(UTC) + timedelta(days=1))
        .sign(private_key, hashes.SHA256())
    )

    certificate_path = certificate_root / f"{stem}.crt"
    private_key_path = certificate_root / f"{stem}.key"
    certificate_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    private_key_path.write_bytes(
        private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    return certificate_path, private_key_path


def _build_signing_config(
    certificate_root: Path,
    *,
    certificate_path: Path,
    private_key_path: Path,
) -> FapiSigningConfig:
    return FapiSigningConfig(
        certificate_path_root=certificate_root,
        signing_certificate_path=certificate_path,
        signing_private_key_path=private_key_path,
        key_id="signing-key-001",
        request_object_issuer="request-object-issuer",
        private_key_jwt_issuer="client-issuer",  # pragma: allowlist secret
        private_key_jwt_subject="client-subject",  # pragma: allowlist secret
        token_endpoint_auth_method="private_key_jwt",  # noqa: S106 - auth-method enum fixture, not a secret
    )


@pytest.mark.unit
def test_load_signing_credentials_reads_matching_pem_pair(tmp_path: Path) -> None:
    certificate_root = tmp_path / "certs"
    certificate_root.mkdir()
    certificate_path, private_key_path = _write_signing_pair(certificate_root, stem="signing")

    credentials = load_signing_credentials(
        _build_signing_config(
            certificate_root,
            certificate_path=certificate_path,
            private_key_path=private_key_path,
        )
    )

    assert b"BEGIN CERTIFICATE" in credentials.signing_certificate_pem
    assert b"BEGIN " + b"PRIVATE KEY" in credentials.signing_private_key_pem


@pytest.mark.unit
def test_load_signing_credentials_rejects_missing_files(tmp_path: Path) -> None:
    certificate_root = tmp_path / "certs"
    certificate_root.mkdir()

    with pytest.raises(SigningCredentialError, match="Unable to read fapiSigning.signingCertificatePath from disk"):
        load_signing_credentials(
            _build_signing_config(
                certificate_root,
                certificate_path=certificate_root / "missing.crt",
                private_key_path=certificate_root / "missing.key",
            )
        )


@pytest.mark.unit
def test_load_signing_credentials_rejects_relative_paths(tmp_path: Path) -> None:
    """Runtime signing loader requires exact absolute credential paths."""
    certificate_root = tmp_path / "certs"
    certificate_root.mkdir()

    with pytest.raises(
        SigningCredentialError,
        match="fapiSigning.signingCertificatePath must be an absolute file path",
    ):
        load_signing_credentials(
            _build_signing_config(
                certificate_root,
                certificate_path=Path("relative.crt"),
                private_key_path=certificate_root / "missing.key",
            )
        )


@pytest.mark.unit
def test_load_signing_credentials_rejects_invalid_certificate_pem_without_echoing_contents(tmp_path: Path) -> None:
    certificate_root = tmp_path / "certs"
    certificate_root.mkdir()
    certificate_path = certificate_root / "signing.crt"
    private_key_path = certificate_root / "signing.key"
    certificate_marker = "CERT_MARKER_FOR_MASKING_TEST"
    key_marker = "KEY_MARKER_FOR_MASKING_TEST"
    certificate_path.write_text(f"not-a-certificate {certificate_marker}", encoding="utf-8")
    private_key_header = "-----BEGIN " + "PRIVATE KEY-----"
    private_key_footer = "-----END " + "PRIVATE KEY-----"
    private_key_path.write_text(
        f"{private_key_header}\n{key_marker}\n{private_key_footer}",
        encoding="utf-8",
    )

    with pytest.raises(SigningCredentialError) as error_info:
        load_signing_credentials(
            _build_signing_config(
                certificate_root,
                certificate_path=certificate_path,
                private_key_path=private_key_path,
            )
        )

    assert str(error_info.value) == "fapiSigning.signingCertificatePath must contain a valid PEM certificate"
    assert certificate_marker not in str(error_info.value)
    assert key_marker not in str(error_info.value)


@pytest.mark.unit
def test_load_signing_credentials_rejects_invalid_private_key_pem_without_echoing_contents(tmp_path: Path) -> None:
    certificate_root = tmp_path / "certs"
    certificate_root.mkdir()
    certificate_path, _ = _write_signing_pair(certificate_root, stem="signing")
    private_key_path = certificate_root / "signing.key"
    key_marker = "KEY_MARKER_FOR_MASKING_TEST"
    private_key_path.write_text(f"not-a-private-key {key_marker}", encoding="utf-8")

    with pytest.raises(SigningCredentialError) as error_info:
        load_signing_credentials(
            _build_signing_config(
                certificate_root,
                certificate_path=certificate_path,
                private_key_path=private_key_path,
            )
        )

    assert str(error_info.value) == "fapiSigning.signingPrivateKeyPath must contain a valid PEM private key"
    assert key_marker not in str(error_info.value)


@pytest.mark.unit
def test_load_signing_credentials_rejects_mismatched_certificate_and_private_key(tmp_path: Path) -> None:
    certificate_root = tmp_path / "certs"
    certificate_root.mkdir()
    certificate_path, _ = _write_signing_pair(certificate_root, stem="certificate")
    _, private_key_path = _write_signing_pair(certificate_root, stem="private-key")

    with pytest.raises(
        SigningCredentialError,
        match="fapiSigning signing certificate and private key must form a matching RSA key pair",
    ):
        load_signing_credentials(
            _build_signing_config(
                certificate_root,
                certificate_path=certificate_path,
                private_key_path=private_key_path,
            )
        )
