"""Default TLS trust anchors bundled with the conformance tool."""

from __future__ import annotations

import ssl
from pathlib import Path

_CERTIFICATE_DIR = Path(__file__).resolve().parent / "certificates"
"""Directory containing public CA certificates bundled with the tool."""

_OPEN_BANKING_CA_FILENAMES: tuple[str, ...] = (
    "openbanking-pre-production-issuing-ca.pem",
    "openbanking-pre-production-root-ca.pem",
    "openbanking-production-issuing-ca.pem",
    "openbanking-production-root-ca.pem",
)
"""Public Open Banking CA certificates inherited from the previous FCS."""


def bundled_open_banking_ca_paths() -> tuple[Path, ...]:
    """Return bundled Open Banking CA certificate file paths.

    Returns:
        Absolute paths to the public Open Banking root and issuing CA PEM files
        shipped with the conformance tool.
    """
    return tuple(_CERTIFICATE_DIR / filename for filename in _OPEN_BANKING_CA_FILENAMES)


def build_default_tls_context(*, extra_ca_bundle_path: Path | None = None) -> ssl.SSLContext:
    """Build a verified TLS context with default Open Banking trust anchors.

    Args:
        extra_ca_bundle_path: Optional participant-supplied PEM CA bundle to
            append after the bundled Open Banking trust anchors.

    Returns:
        TLS context that verifies server certificates using the system roots,
        bundled Open Banking CAs, and any participant-supplied CA bundle.

    Raises:
        ValueError: If a bundled or participant-supplied CA bundle cannot be
            loaded into the TLS context.
    """
    context = ssl.create_default_context()
    for ca_path in bundled_open_banking_ca_paths():
        try:
            context.load_verify_locations(cafile=str(ca_path))
        except ssl.SSLError as error:
            raise ValueError(f"Unable to load bundled Open Banking CA certificate from {ca_path}: {error}") from error

    if extra_ca_bundle_path is not None:
        try:
            context.load_verify_locations(cafile=str(extra_ca_bundle_path))
        except ssl.SSLError as error:
            raise ValueError(f"Unable to load TLS CA bundle from {extra_ca_bundle_path}: {error}") from error

    return context
