"""DCR credential paths and runtime credential loader.

DCR requires several file-backed secrets and certificates:

- A Software Statement Assertion (SSA) JWT — provided by the Open Banking
  Directory and used in the registration request.
- A signing private key and its associated X.509 certificate — used to sign
  registration JWTs and (for ``private_key_jwt``) token-endpoint assertions.
- A transport (mTLS) client certificate and private key — used for mutual TLS
  on all DCR and token-endpoint HTTP connections.
- An optional CA bundle — used to verify the ASPSP's server certificate.

All paths must resolve under a single validated root directory.  The root
containment check (using :meth:`pathlib.Path.resolve`) is the same pattern
used by :mod:`conformance.signing_credentials` for FAPI signing material.

:class:`DcrCredentialPaths` stores the validated, resolved paths.
:func:`load_dcr_credentials` reads the files and returns in-memory
:class:`DcrCredentials` immediately before a DCR run begins.  Credentials are
never stored in placeholders, manifest values, API responses, or result JSON.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


class DcrCredentialError(ValueError):
    """Raised when DCR credential paths or files cannot be validated or read.

    Wraps :class:`ValueError` so callers can catch either the specific error
    or the generic base class.
    """


@dataclass(frozen=True)
class DcrCredentialPaths:
    """Validated file-backed paths for DCR credential material.

    All paths must resolve under :attr:`credential_path_root`.  The root
    containment check is performed at construction time by
    :func:`validate_dcr_credential_paths`.

    Attributes:
        credential_path_root: Trusted root directory under which all
            credential paths must resolve.
        ssa_path: Path to the Software Statement Assertion JWT file.
        signing_private_key_path: Path to the PEM-encoded RSA private key
            used for signing DCR registration JWTs and (for
            ``private_key_jwt``) token-endpoint client assertions.
        signing_certificate_path: Path to the PEM-encoded X.509 certificate
            paired with :attr:`signing_private_key_path`.
        transport_certificate_path: Path to the PEM-encoded mTLS client
            certificate used on all DCR HTTP connections.
        transport_private_key_path: Path to the PEM-encoded private key
            paired with :attr:`transport_certificate_path`.
        ca_bundle_path: Optional path to a PEM CA bundle used to verify the
            ASPSP's server certificate.  When ``None`` the system default CA
            store is used.
    """

    credential_path_root: Path
    ssa_path: Path
    signing_private_key_path: Path
    signing_certificate_path: Path
    transport_certificate_path: Path
    transport_private_key_path: Path
    ca_bundle_path: Path | None = None


@dataclass(frozen=True)
class DcrCredentials:
    """In-memory DCR credential material loaded at execution time.

    These values are read from disk by :func:`load_dcr_credentials`
    immediately before a DCR run begins.  They must never be persisted,
    logged, or included in API responses.

    Attributes:
        ssa_jwt: Raw Software Statement Assertion JWT bytes.
        signing_private_key_pem: PEM-encoded RSA private key bytes for
            registration JWT signing and (for ``private_key_jwt``)
            token-endpoint assertions.
        signing_certificate_pem: PEM-encoded X.509 certificate bytes paired
            with :attr:`signing_private_key_pem`.
        transport_certificate_pem: PEM-encoded mTLS client certificate bytes.
        transport_private_key_pem: PEM-encoded private key bytes paired with
            :attr:`transport_certificate_pem`.
        ca_bundle_pem: Optional PEM CA bundle bytes for ASPSP server
            certificate verification.  ``None`` when no CA bundle was
            configured.
    """

    ssa_jwt: bytes
    signing_private_key_pem: bytes
    signing_certificate_pem: bytes
    transport_certificate_pem: bytes
    transport_private_key_pem: bytes
    ca_bundle_pem: bytes | None = None


def validate_dcr_credential_paths(paths: DcrCredentialPaths) -> None:
    """Validate that all credential paths resolve under the configured root.

    Checks each configured credential path (except ``ca_bundle_path`` when
    absent) against ``credential_path_root`` using resolved-path containment.
    The function is a no-op when all paths are valid.

    Args:
        paths: The :class:`DcrCredentialPaths` to validate.

    Raises:
        DcrCredentialError: If any credential path resolves outside
            ``credential_path_root``.
    """
    root = paths.credential_path_root.resolve()
    _check_path(paths.ssa_path, root=root, label="ssa_path")
    _check_path(paths.signing_private_key_path, root=root, label="signing_private_key_path")
    _check_path(paths.signing_certificate_path, root=root, label="signing_certificate_path")
    _check_path(paths.transport_certificate_path, root=root, label="transport_certificate_path")
    _check_path(paths.transport_private_key_path, root=root, label="transport_private_key_path")
    if paths.ca_bundle_path is not None:
        _check_path(paths.ca_bundle_path, root=root, label="ca_bundle_path")


def load_dcr_credentials(paths: DcrCredentialPaths) -> DcrCredentials:
    """Load DCR credential files from disk for immediate runtime use.

    Reads each credential file into memory and returns a :class:`DcrCredentials`
    instance.  Path containment validation is performed before any file read.
    File-read errors use a label-only message so path details are not exposed
    in exception messages that may surface in API error responses.

    Args:
        paths: The :class:`DcrCredentialPaths` describing which files to read.

    Returns:
        In-memory :class:`DcrCredentials` ready for DCR execution.

    Raises:
        DcrCredentialError: If any path escapes ``credential_path_root``,
            or if any file cannot be read from disk.
    """
    validate_dcr_credential_paths(paths)

    ssa_jwt = _read_bytes(paths.ssa_path, label="ssa_path")
    signing_private_key_pem = _read_bytes(paths.signing_private_key_path, label="signing_private_key_path")
    signing_certificate_pem = _read_bytes(paths.signing_certificate_path, label="signing_certificate_path")
    transport_certificate_pem = _read_bytes(paths.transport_certificate_path, label="transport_certificate_path")
    transport_private_key_pem = _read_bytes(paths.transport_private_key_path, label="transport_private_key_path")

    ca_bundle_pem: bytes | None = None
    if paths.ca_bundle_path is not None:
        ca_bundle_pem = _read_bytes(paths.ca_bundle_path, label="ca_bundle_path")

    return DcrCredentials(
        ssa_jwt=ssa_jwt,
        signing_private_key_pem=signing_private_key_pem,
        signing_certificate_pem=signing_certificate_pem,
        transport_certificate_pem=transport_certificate_pem,
        transport_private_key_pem=transport_private_key_pem,
        ca_bundle_pem=ca_bundle_pem,
    )


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _check_path(path: Path, *, root: Path, label: str) -> None:
    """Assert that a resolved path resides under a trusted root directory.

    Args:
        path: The credential path to validate.
        root: The trusted root directory (already resolved).
        label: Human-readable field name used in the error message.

    Raises:
        DcrCredentialError: If ``path`` resolves outside ``root``.
    """
    resolved = path.resolve()
    if resolved != root and root not in resolved.parents:
        raise DcrCredentialError(f"DCR credential {label!r} must resolve inside credential_path_root")


def _read_bytes(path: Path, *, label: str) -> bytes:
    """Read a file from disk without exposing its path in error messages.

    Args:
        path: The file to read.
        label: Human-readable field name used in the error message.

    Returns:
        The raw file bytes.

    Raises:
        DcrCredentialError: If the file cannot be read from disk.
    """
    try:
        return path.read_bytes()
    except OSError as error:
        raise DcrCredentialError(f"Unable to read DCR credential {label!r} from disk") from error
