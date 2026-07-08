"""Unit tests for DCR credential paths, loader, and validation."""

from __future__ import annotations

from pathlib import Path

import pytest

from conformance.dcr.credentials import (
    DcrCredentialError,
    DcrCredentialPaths,
    DcrCredentials,
    load_dcr_credentials,
    validate_dcr_credential_paths,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_paths(tmp_path: Path, *, include_ca_bundle: bool = False) -> DcrCredentialPaths:
    """Write stub credential files and return a DcrCredentialPaths referencing them."""
    root = tmp_path / "creds"
    root.mkdir()

    (root / "ssa.jwt").write_bytes(b"stub-ssa-jwt")
    (root / "signing.key").write_bytes(b"stub-signing-key")
    (root / "signing.crt").write_bytes(b"stub-signing-cert")
    (root / "transport.crt").write_bytes(b"stub-transport-cert")
    (root / "transport.key").write_bytes(b"stub-transport-key")

    ca_bundle_path: Path | None = None
    if include_ca_bundle:
        (root / "ca.pem").write_bytes(b"stub-ca-bundle")
        ca_bundle_path = root / "ca.pem"

    return DcrCredentialPaths(
        credential_path_root=root,
        ssa_path=root / "ssa.jwt",
        signing_private_key_path=root / "signing.key",
        signing_certificate_path=root / "signing.crt",
        transport_certificate_path=root / "transport.crt",
        transport_private_key_path=root / "transport.key",
        ca_bundle_path=ca_bundle_path,
    )


# ---------------------------------------------------------------------------
# DcrCredentialPaths construction
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_credential_paths_is_frozen(tmp_path: Path) -> None:
    paths = _make_paths(tmp_path)
    with pytest.raises(Exception):
        paths.ssa_path = tmp_path  # type: ignore[misc]


# ---------------------------------------------------------------------------
# validate_dcr_credential_paths — happy paths
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_validate_accepts_paths_under_root(tmp_path: Path) -> None:
    paths = _make_paths(tmp_path)
    # Should not raise
    validate_dcr_credential_paths(paths)


@pytest.mark.unit
def test_validate_accepts_ca_bundle_under_root(tmp_path: Path) -> None:
    paths = _make_paths(tmp_path, include_ca_bundle=True)
    validate_dcr_credential_paths(paths)


# ---------------------------------------------------------------------------
# validate_dcr_credential_paths — escape detection
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_validate_raises_when_ssa_escapes_root(tmp_path: Path) -> None:
    paths = _make_paths(tmp_path)
    outside_file = tmp_path / "outside.jwt"
    outside_file.write_bytes(b"x")
    bad_paths = DcrCredentialPaths(
        credential_path_root=paths.credential_path_root,
        ssa_path=outside_file,
        signing_private_key_path=paths.signing_private_key_path,
        signing_certificate_path=paths.signing_certificate_path,
        transport_certificate_path=paths.transport_certificate_path,
        transport_private_key_path=paths.transport_private_key_path,
    )
    with pytest.raises(DcrCredentialError, match="ssa_path"):
        validate_dcr_credential_paths(bad_paths)


@pytest.mark.unit
def test_validate_raises_when_transport_key_escapes_root(tmp_path: Path) -> None:
    paths = _make_paths(tmp_path)
    outside_key = tmp_path / "outside.key"
    outside_key.write_bytes(b"x")
    bad_paths = DcrCredentialPaths(
        credential_path_root=paths.credential_path_root,
        ssa_path=paths.ssa_path,
        signing_private_key_path=paths.signing_private_key_path,
        signing_certificate_path=paths.signing_certificate_path,
        transport_certificate_path=paths.transport_certificate_path,
        transport_private_key_path=outside_key,
    )
    with pytest.raises(DcrCredentialError, match="transport_private_key_path"):
        validate_dcr_credential_paths(bad_paths)


@pytest.mark.unit
def test_validate_raises_when_ca_bundle_escapes_root(tmp_path: Path) -> None:
    paths = _make_paths(tmp_path)
    outside_ca = tmp_path / "outside-ca.pem"
    outside_ca.write_bytes(b"x")
    bad_paths = DcrCredentialPaths(
        credential_path_root=paths.credential_path_root,
        ssa_path=paths.ssa_path,
        signing_private_key_path=paths.signing_private_key_path,
        signing_certificate_path=paths.signing_certificate_path,
        transport_certificate_path=paths.transport_certificate_path,
        transport_private_key_path=paths.transport_private_key_path,
        ca_bundle_path=outside_ca,
    )
    with pytest.raises(DcrCredentialError, match="ca_bundle_path"):
        validate_dcr_credential_paths(bad_paths)


# ---------------------------------------------------------------------------
# load_dcr_credentials — happy paths
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_load_credentials_without_ca_bundle(tmp_path: Path) -> None:
    paths = _make_paths(tmp_path)
    creds = load_dcr_credentials(paths)
    assert creds.ssa_jwt == b"stub-ssa-jwt"
    assert creds.signing_private_key_pem == b"stub-signing-key"
    assert creds.signing_certificate_pem == b"stub-signing-cert"
    assert creds.transport_certificate_pem == b"stub-transport-cert"
    assert creds.transport_private_key_pem == b"stub-transport-key"
    assert creds.ca_bundle_pem is None


@pytest.mark.unit
def test_load_credentials_with_ca_bundle(tmp_path: Path) -> None:
    paths = _make_paths(tmp_path, include_ca_bundle=True)
    creds = load_dcr_credentials(paths)
    assert creds.ca_bundle_pem == b"stub-ca-bundle"


@pytest.mark.unit
def test_load_credentials_returns_frozen_dataclass(tmp_path: Path) -> None:
    paths = _make_paths(tmp_path)
    creds = load_dcr_credentials(paths)
    with pytest.raises(Exception):
        creds.ssa_jwt = b"mutated"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# load_dcr_credentials — error paths
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_load_raises_when_file_missing(tmp_path: Path) -> None:
    paths = _make_paths(tmp_path)
    missing = DcrCredentialPaths(
        credential_path_root=paths.credential_path_root,
        ssa_path=paths.credential_path_root / "nonexistent.jwt",
        signing_private_key_path=paths.signing_private_key_path,
        signing_certificate_path=paths.signing_certificate_path,
        transport_certificate_path=paths.transport_certificate_path,
        transport_private_key_path=paths.transport_private_key_path,
    )
    with pytest.raises(DcrCredentialError, match="ssa_path"):
        load_dcr_credentials(missing)


@pytest.mark.unit
def test_load_raises_when_path_escapes_root(tmp_path: Path) -> None:
    paths = _make_paths(tmp_path)
    outside = tmp_path / "escape.jwt"
    outside.write_bytes(b"x")
    bad_paths = DcrCredentialPaths(
        credential_path_root=paths.credential_path_root,
        ssa_path=outside,
        signing_private_key_path=paths.signing_private_key_path,
        signing_certificate_path=paths.signing_certificate_path,
        transport_certificate_path=paths.transport_certificate_path,
        transport_private_key_path=paths.transport_private_key_path,
    )
    with pytest.raises(DcrCredentialError, match="credential_path_root"):
        load_dcr_credentials(bad_paths)


# ---------------------------------------------------------------------------
# DcrCredentials is frozen
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_dcr_credentials_is_frozen() -> None:
    creds = DcrCredentials(
        ssa_jwt=b"ssa",
        signing_private_key_pem=b"key",
        signing_certificate_pem=b"cert",
        transport_certificate_pem=b"tcert",
        transport_private_key_pem=b"tkey",
    )
    with pytest.raises(Exception):
        creds.ssa_jwt = b"mutated"  # type: ignore[misc]
