"""Signing material builders shared by the executor signing and PSU test modules.

The builders produce real RSA keypairs and FAPI signing configuration so tests
exercise genuine detached JWS (``x-jws-signature``), ``private_key_jwt`` client
assertion, and Open Banking response-signature behaviour rather than a stub.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from joserfc import jwk, jws
from joserfc.jws import JWSRegistry
from joserfc.registry import HeaderParameter

from conformance.json_types import JsonObject
from conformance.model_bank_config import FapiSigningConfig


def write_signing_pair(certificate_root: Path, *, stem: str) -> tuple[Path, Path]:
    """Write a temporary RSA signing keypair for executor tests.

    Args:
        certificate_root: Directory that will receive the generated PEM files.
        stem: File-stem prefix used for the certificate and key filenames.

    Returns:
        Tuple of ``(certificate_path, private_key_path)``.
    """
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


def executor_signing_config(tmp_path: Path) -> FapiSigningConfig:
    """Build a valid FAPI signing config for executor tests.

    Args:
        tmp_path: Pytest temporary directory used to hold generated PEM files.

    Returns:
        Parsed FAPI signing configuration pointing at the generated keypair.
    """
    certificate_root = tmp_path / "certs"
    certificate_root.mkdir()
    certificate_path, private_key_path = write_signing_pair(certificate_root, stem="executor-signing")
    return FapiSigningConfig(
        signing_certificate_path=certificate_path,
        signing_private_key_path=private_key_path,
        key_id="executor-signing-key",
        client_assertion_issuer="client-issuer",
        client_assertion_subject="client-subject",
        token_endpoint_auth_method="private_key_jwt",  # noqa: S106 - enum fixture, not a secret
    )


def invalid_executor_signing_config(tmp_path: Path) -> FapiSigningConfig:
    """Build a signing config whose PEM files fail runtime credential loading.

    Args:
        tmp_path: Pytest temporary directory used to hold invalid PEM files.

    Returns:
        Parsed FAPI signing configuration pointing at invalid PEM content.
    """
    certificate_root = tmp_path / "invalid-certs"
    certificate_root.mkdir()
    certificate_path = certificate_root / "invalid-signing.crt"
    private_key_path = certificate_root / "invalid-signing.key"
    certificate_path.write_bytes(b"invalid certificate data")
    private_key_path.write_bytes(b"invalid private key data")
    return FapiSigningConfig(
        signing_certificate_path=certificate_path,
        signing_private_key_path=private_key_path,
        key_id="invalid-executor-signing-key",
        client_assertion_issuer="client-issuer",
        client_assertion_subject="client-subject",
        token_endpoint_auth_method="private_key_jwt",  # noqa: S106 - enum fixture, not a secret
    )


def response_signature_registry() -> JWSRegistry:
    """Return a JWS registry that accepts Open Banking protected headers.

    Returns:
        Registry configured for PS256 response-signature tests.
    """
    headers = {
        **JWSRegistry.default_header_registry,
        "http://openbanking.org.uk/iat": HeaderParameter("Open Banking issued-at header", "int"),
        "http://openbanking.org.uk/iss": HeaderParameter("Open Banking issuer header", "str"),
        "http://openbanking.org.uk/tan": HeaderParameter("Open Banking trust-anchor header", "str"),
    }
    return JWSRegistry(header_registry=headers, algorithms=["PS256"])


def signed_response_header(payload: bytes) -> tuple[str, JsonObject]:
    """Return a valid detached response signature and matching JWKS.

    Args:
        payload: Exact response bytes to sign.

    Returns:
        Tuple of ``x-jws-signature`` header and JWKS document.
    """
    signing_key = jwk.generate_key("RSA", 2048, private=True, auto_kid=False)
    public_key = signing_key.as_dict(is_private=False)
    public_key["kid"] = "response-key"
    protected = {
        "alg": "PS256",
        "kid": "response-key",
        "b64": False,
        "crit": [
            "b64",
            "http://openbanking.org.uk/iat",
            "http://openbanking.org.uk/iss",
            "http://openbanking.org.uk/tan",
        ],
        "http://openbanking.org.uk/iat": 1_774_120_000,
        "http://openbanking.org.uk/iss": "0015800001041RHAAY",
        "http://openbanking.org.uk/tan": "openbanking.org.uk",
    }
    compact_jws = jws.serialize_compact(
        protected,
        payload,
        signing_key,
        algorithms=["PS256"],
        registry=response_signature_registry(),
    )
    return jws.detach_content(compact_jws), cast(JsonObject, {"keys": [public_key]})
