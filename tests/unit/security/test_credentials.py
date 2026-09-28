"""Unit tests for path-or-inline credential material."""

from __future__ import annotations

import ssl
from pathlib import Path
from unittest.mock import patch

import pytest

from conformance.credentials import (
    MAX_INLINE_CREDENTIAL_BYTES,
    CredentialError,
    CredentialKind,
    apply_ca_bundle,
    apply_client_certificate,
    credential_bytes,
    credential_from_inline,
    credential_from_path,
    credential_text,
    describe_credential,
    parse_credential_material,
    scrub_pem,
    validate_inline_material,
)
from tests.support.executor_signing import write_signing_pair

pytestmark = pytest.mark.unit


def test_credential_requires_exactly_one_source() -> None:
    """Credential material must carry a path or inline text, never both or neither."""
    with pytest.raises(CredentialError):
        parse_credential_material(
            {"signingCertificatePath": "/certs/a.pem", "signingCertificatePem": "-----BEGIN CERTIFICATE-----\n"},
            path_key="signingCertificatePath",
            inline_key="signingCertificatePem",
            location="fapiSigning",
        )
    assert (
        parse_credential_material(
            {},
            path_key="signingCertificatePath",
            inline_key="signingCertificatePem",
            location="fapiSigning",
        )
        is None
    )


def test_inline_credential_repr_and_str_redact_material() -> None:
    """Inline material must never leak through ``repr`` or string interpolation."""
    armour = "-----BEGIN PRIVATE KEY-----"  # pragma: allowlist secret - PEM armour marker, not a key
    material = credential_from_inline(f"{armour}\nsecret\n-----END PRIVATE KEY-----\n")
    assert "secret" not in repr(material)
    assert "secret" not in str(material)
    assert "secret" not in f"{material}"
    assert "redacted" in repr(material)


def test_inline_credential_rejects_oversized_material() -> None:
    """Pasted material above the ceiling is rejected rather than stored."""
    with pytest.raises(CredentialError):
        credential_from_inline("-----BEGIN CERTIFICATE-----\n" + ("A" * (MAX_INLINE_CREDENTIAL_BYTES + 1)))


def test_credential_bytes_and_text_round_trip_for_both_sources(tmp_path: Path) -> None:
    """Inline and path-backed credentials expose identical bytes and text."""
    certificate_path, _ = write_signing_pair(tmp_path, stem="round-trip")
    pem = certificate_path.read_text(encoding="utf-8")

    from_path = credential_from_path(certificate_path)
    from_inline = credential_from_inline(pem)

    assert credential_bytes(from_path, label="signing certificate") == certificate_path.read_bytes()
    assert credential_bytes(from_inline, label="signing certificate") == pem.encode("utf-8")
    assert credential_text(from_inline, label="signing certificate") == pem.strip()
    assert credential_text(from_path, label="signing certificate") == pem.strip()


def test_credential_bytes_reports_a_missing_file_without_naming_material(tmp_path: Path) -> None:
    """A missing path reference fails with a participant-facing label."""
    material = credential_from_path(tmp_path / "absent.pem")
    with pytest.raises(CredentialError) as failure:
        credential_bytes(material, label="signing certificate")
    assert "signing certificate" in str(failure.value)


def test_validate_inline_material_checks_the_expected_artefact(tmp_path: Path) -> None:
    """Pasted text is validated against the artefact the field expects."""
    _, private_key_path = write_signing_pair(tmp_path, stem="inline-wrong-kind")
    cases: tuple[tuple[str, CredentialKind], ...] = (
        ("not a pem", "certificate"),
        ("not a pem", "private_key"),
        ("", "certificate"),
        (private_key_path.read_text(encoding="utf-8"), "certificate"),
    )
    for text, kind in cases:
        with pytest.raises(CredentialError) as failure:
            validate_inline_material(text, kind=kind)
        assert "BEGIN" not in str(failure.value) or "block" in str(failure.value)


def test_validate_inline_material_accepts_real_pem(tmp_path: Path) -> None:
    """Real certificate and key PEM text passes inline validation."""
    certificate_path, private_key_path = write_signing_pair(tmp_path, stem="inline-valid")
    certificate_pem = certificate_path.read_text(encoding="utf-8")
    private_key_pem = private_key_path.read_text(encoding="utf-8")
    assert validate_inline_material(certificate_pem, kind="certificate") == certificate_pem
    assert validate_inline_material(private_key_pem, kind="private_key") == private_key_pem


def test_inline_client_certificate_loads_and_removes_its_temporary_file(tmp_path: Path) -> None:
    """Inline mTLS material loads through a short-lived file that is then removed."""
    certificate_path, private_key_path = write_signing_pair(tmp_path, stem="inline-mtls")
    certificate_pem = certificate_path.read_text(encoding="utf-8")
    context = ssl.create_default_context()
    observed: list[str] = []
    real_load = context.load_cert_chain

    def _recording_load(certfile: str, keyfile: str | None = None, password: object = None) -> None:
        observed.append(certfile)
        assert Path(certfile).read_text(encoding="utf-8") == certificate_pem
        real_load(certfile, keyfile)

    with patch.object(context, "load_cert_chain", _recording_load):
        apply_client_certificate(
            context,
            certificate=credential_from_inline(certificate_pem),
            private_key=credential_from_inline(private_key_path.read_text(encoding="utf-8")),
        )

    assert observed, "expected the certificate chain to be loaded"
    assert not Path(observed[0]).exists()


def test_inline_materialisation_removes_the_temporary_file_on_failure(tmp_path: Path) -> None:
    """A failed certificate load must not leave inline material on disk."""
    certificate_path, private_key_path = write_signing_pair(tmp_path, stem="inline-failure")
    context = ssl.create_default_context()
    recorded: list[str] = []

    def _failing_load(certfile: str, keyfile: str | None = None) -> None:
        recorded.append(certfile)
        raise ssl.SSLError("injected load failure")

    with (
        patch.object(context, "load_cert_chain", _failing_load),
        pytest.raises(CredentialError),
    ):
        apply_client_certificate(
            context,
            certificate=credential_from_inline(certificate_path.read_text(encoding="utf-8")),
            private_key=credential_from_inline(private_key_path.read_text(encoding="utf-8")),
        )

    assert recorded, "expected the certificate load to be attempted"
    assert not Path(recorded[0]).exists()


def test_ca_bundle_is_loaded_as_data_when_inline_and_as_a_file_when_referenced(tmp_path: Path) -> None:
    """An inline CA bundle is loaded from memory, never written to disk."""
    certificate_path, _ = write_signing_pair(tmp_path, stem="inline-ca")
    certificate_pem = certificate_path.read_text(encoding="utf-8")
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    calls: list[dict[str, object]] = []

    def _recording_load(cafile: object = None, capath: object = None, cadata: object = None) -> None:
        calls.append({"cafile": cafile, "cadata": cadata})

    with patch.object(context, "load_verify_locations", _recording_load):
        apply_ca_bundle(context, credential_from_inline(certificate_pem))
        apply_ca_bundle(context, credential_from_path(certificate_path))

    assert calls[0]["cadata"] == certificate_pem
    assert calls[0]["cafile"] is None
    assert calls[1]["cafile"] == str(certificate_path)
    assert calls[1]["cadata"] is None


def test_ca_bundle_failures_are_reported_without_material(tmp_path: Path) -> None:
    """A malformed CA bundle fails with a message that carries no material."""
    with pytest.raises(CredentialError) as failure:
        apply_ca_bundle(
            ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT),
            credential_from_inline("-----BEGIN CERTIFICATE-----\nnot-base64\n-----END CERTIFICATE-----\n"),
        )
    assert "not-base64" not in str(failure.value)


def test_describe_credential_summarises_without_revealing_material(tmp_path: Path) -> None:
    """Descriptors shown in the wizard must not contain credential material."""
    certificate_path, private_key_path = write_signing_pair(tmp_path, stem="descriptor")
    certificate_pem = certificate_path.read_text(encoding="utf-8")
    private_key_pem = private_key_path.read_text(encoding="utf-8")

    certificate_descriptor = describe_credential(credential_from_inline(certificate_pem), kind="certificate")
    private_key_descriptor = describe_credential(credential_from_inline(private_key_pem), kind="private_key")

    assert certificate_descriptor is not None
    assert "descriptor" in certificate_descriptor
    assert "BEGIN" not in certificate_descriptor
    assert private_key_descriptor is not None
    assert "BEGIN" not in private_key_descriptor
    assert describe_credential(None, kind="certificate") is None


def test_scrub_pem_removes_material_from_free_text() -> None:
    """Free text carrying PEM blocks is redacted before it reaches a result."""
    armour = "-----BEGIN PRIVATE KEY-----"  # pragma: allowlist secret - PEM armour marker, not a key
    message = f"load failed for {armour}\nMIIsecret\n-----END PRIVATE KEY----- while connecting"
    scrubbed = scrub_pem(message)
    assert "MIIsecret" not in scrubbed
    assert "load failed for" in scrubbed
    assert "while connecting" in scrubbed


def test_scrub_pem_leaves_ordinary_text_unchanged() -> None:
    """Text with no PEM block is returned unchanged."""
    assert scrub_pem("connection refused") == "connection refused"
