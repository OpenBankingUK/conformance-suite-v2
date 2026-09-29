"""Runtime loading for FAPI signing key and certificate material.

This module keeps signing secrets on disk until execution time. Config parsing
stores only validated paths and non-secret JOSE metadata; this loader performs
the actual file reads, PEM parsing, and RSA key-pair validation immediately
before signing work begins.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from joserfc import jwk
from joserfc.errors import InvalidKeyTypeError

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
        signing_config: Non-secret FAPI signing config containing resolved
            certificate and private-key paths.

    Returns:
        In-memory PEM bytes for the signing certificate and private key.

    Raises:
        SigningCredentialError: If a file cannot be read, the PEM content is
            malformed, or the certificate/public key does not match the
            configured private key.
    """
    certificate_pem = _read_pem_bytes(
        signing_config.signing_certificate_path,
        label="fapiSigning.signingCertificatePath",
    )
    private_key_pem = _read_pem_bytes(
        signing_config.signing_private_key_path,
        label="fapiSigning.signingPrivateKeyPath",
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


def _read_pem_bytes(path: Path, *, label: str) -> bytes:
    """Read one PEM file from disk without exposing its contents in errors.

    Args:
        path: Credential file to read.
        label: Human-readable config field name for error reporting.

    Returns:
        Raw file bytes.

    Raises:
        SigningCredentialError: If the file cannot be read from disk.
    """
    try:
        return path.read_bytes()
    except OSError as error:
        raise SigningCredentialError(f"Unable to read {label} from disk") from error


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
        raise SigningCredentialError("fapiSigning.signingCertificatePath must contain a valid PEM certificate")
    try:
        return jwk.import_key(certificate_pem, key_type="RSA")
    except (InvalidKeyTypeError, TypeError, ValueError) as error:
        raise SigningCredentialError(
            "fapiSigning.signingCertificatePath must contain a valid PEM certificate"
        ) from error


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
        raise SigningCredentialError(
            "fapiSigning.signingPrivateKeyPath must contain a valid PEM private key"
        ) from error
    return private_key
