"""Runtime loading for FAPI signing key and certificate material.

Config parsing stores either a validated path reference or inline PEM material
supplied through the browser wizard, alongside non-secret JOSE metadata. This
loader performs the actual read, PEM parsing, and RSA key-pair validation
immediately before signing work begins, so path-referenced secrets stay on disk
until execution time.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from joserfc import jwk
from joserfc.errors import InvalidKeyTypeError

from conformance.credentials import CredentialError, CredentialMaterial, credential_bytes
from conformance.model_bank_config import FapiSigningConfig


class SigningCredentialError(ValueError):
    """Raised when runtime signing credentials cannot be read or validated."""


@dataclass(frozen=True)
class SigningCredentials:
    """In-memory FAPI signing material loaded at execution time.

    Attributes:
        signing_certificate_pem: Raw PEM-encoded X.509 certificate bytes.
        signing_private_key_pem: Raw PEM-encoded private-key bytes.
    """

    signing_certificate_pem: bytes
    signing_private_key_pem: bytes


class _ComparableJwk(Protocol):
    """Minimal JWK surface needed for RSA key-pair comparison.

    The public ``joserfc.jwk.import_key`` API returns algorithm-specific JWK
    instances. This loader only relies on ``as_dict`` for comparing the public
    key parameters of the certificate and private key, so a narrow protocol
    keeps the implementation typed without importing private library classes.
    """

    def as_dict(self, private: bool = False, **params: object) -> Mapping[str, object]:
        """Return the JWK as a JSON-serializable dictionary.

        Args:
            private: Whether private-key members should be included.
            **params: Additional implementation-specific export parameters.

        Returns:
            JWK members as a dictionary with JSON-compatible values.
        """


def load_signing_credentials(signing_config: FapiSigningConfig) -> SigningCredentials:
    """Load and validate signing credential files for runtime JOSE use.

    Args:
        signing_config: Non-secret FAPI signing config containing the resolved
            certificate and private-key credentials.

    Returns:
        In-memory PEM bytes for the signing certificate and private key.

    Raises:
        SigningCredentialError: If a credential cannot be read, the PEM content
            is malformed, or the certificate/public key does not match the
            configured private key.
    """
    if signing_config.signing_certificate is None or signing_config.signing_private_key is None:
        raise SigningCredentialError("FAPI signing requires a signing certificate and private key")
    certificate_pem = _read_pem_bytes(
        signing_config.signing_certificate,
        label="fapiSigning signing certificate",
    )
    private_key_pem = _read_pem_bytes(
        signing_config.signing_private_key,
        label="fapiSigning signing private key",
    )

    certificate_public_key = _load_certificate_public_key(certificate_pem)
    signing_private_key = _load_private_key(private_key_pem)

    if signing_private_key.as_dict(private=False) != certificate_public_key.as_dict(private=False):
        raise SigningCredentialError(
            "fapiSigning signing certificate and private key must form a matching RSA key pair"
        )

    return SigningCredentials(
        signing_certificate_pem=certificate_pem,
        signing_private_key_pem=private_key_pem,
    )


def _read_pem_bytes(material: CredentialMaterial, *, label: str) -> bytes:
    """Read one credential without exposing its contents in errors.

    Args:
        material: Credential supplied as a path reference or inline PEM.
        label: Human-readable config field name for error reporting.

    Returns:
        Raw credential bytes.

    Raises:
        SigningCredentialError: If a path-referenced credential cannot be read.
    """
    try:
        return credential_bytes(material, label=label)
    except CredentialError as error:
        raise SigningCredentialError(str(error)) from error


def _load_certificate_public_key(certificate_pem: bytes) -> _ComparableJwk:
    """Parse a PEM certificate into an RSA public JWK.

    Args:
        certificate_pem: PEM-encoded X.509 certificate bytes.

    Returns:
        Parsed RSA JWK for the certificate public key.

    Raises:
        SigningCredentialError: If the bytes are not a valid PEM certificate.
    """
    if b"CERTIFICATE" not in certificate_pem:
        raise SigningCredentialError("fapiSigning signing certificate must contain a valid PEM certificate")
    try:
        return jwk.import_key(certificate_pem, key_type="RSA")
    except (InvalidKeyTypeError, TypeError, ValueError) as error:
        raise SigningCredentialError("fapiSigning signing certificate must contain a valid PEM certificate") from error


def _load_private_key(private_key_pem: bytes) -> _ComparableJwk:
    """Parse a PEM private key into an RSA signing JWK.

    Args:
        private_key_pem: PEM-encoded private-key bytes.

    Returns:
        Parsed RSA JWK for the private signing key.

    Raises:
        SigningCredentialError: If the bytes are not a valid PEM private key.
    """
    try:
        private_key = jwk.import_key(private_key_pem, key_type="RSA")
        private_key.as_dict(private=True)
    except (InvalidKeyTypeError, TypeError, ValueError) as error:
        raise SigningCredentialError("fapiSigning signing private key must contain a valid PEM private key") from error
    return private_key
