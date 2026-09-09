"""Unit tests for typed Open Banking DCR plan configuration parsing."""

from __future__ import annotations

from pathlib import Path

import pytest

from conformance.json_types import JsonValue
from conformance.model_bank_config import ConfigError
from conformance.plan_configuration import parse_dcr_plan_configuration, validate_dcr_file_references

pytestmark = pytest.mark.unit


def _shared_security(root: Path) -> dict[str, JsonValue]:
    """Build complete shared DCR security configuration.

    Args:
        root: Absolute root used for credential references.

    Returns:
        Canonical shared security object.
    """

    return {
        "discoveryUrl": "https://aspsp.example.com/.well-known/openid-configuration",
        "clientAuthMethod": "private_key_jwt",
        "clientAuthSigningAlgorithm": "PS256",
        "signingPrivateKeyPath": str(root / "signing.key"),
        "signingKeyId": "kid-123",
        "mtls": {
            "enabled": True,
            "certificatePath": str(root / "transport.crt"),
            "privateKeyPath": str(root / "transport.key"),
            "caBundlePath": str(root / "ca.pem"),
        },
    }


def _dcr_config(root: Path) -> dict[str, JsonValue]:
    """Build complete DCR-only configuration with optional overrides.

    Args:
        root: Absolute root used for credential references.

    Returns:
        Canonical DCR-only object.
    """

    return {
        "softwareStatementAssertionPath": str(root / "ssa.jwt"),
        "registrationAudience": "aspsp123",
        "registrationIssuerOverride": "software-id",
        "redirectUrisOverride": ["https://tpp.example.com/callback"],
        "signingCertificatePath": str(root / "signing.crt"),
        "transportCertificateSubjectDnOverride": "CN=transport,O=Example",
        "useNumericOidSubjectDn": True,
        "disableKeepAlive": False,
    }


def test_typed_dcr_config_reuses_shared_security_and_metadata(tmp_path: Path) -> None:
    """Typed DCR config exposes shared security and narrow DCR-only values."""
    parsed = parse_dcr_plan_configuration(
        _shared_security(tmp_path),
        _dcr_config(tmp_path),
        {"aspspName": "Bank", "brandName": "Retail", "environmentName": "Sandbox"},
    )

    assert parsed.shared.discovery_url == "https://aspsp.example.com/.well-known/openid-configuration"
    assert parsed.shared.mtls.client_certificate_path == tmp_path / "transport.crt"
    assert parsed.shared.signing.private_key_path == tmp_path / "signing.key"
    assert parsed.shared.metadata.brand_name == "Retail"
    assert parsed.dynamic_client_registration.redirect_uris_override == ("https://tpp.example.com/callback",)
    assert parsed.dynamic_client_registration.use_numeric_oid_subject_dn is True


@pytest.mark.parametrize(
    ("section", "key", "message"),
    [
        ("security", "discoveryUrl", "discoveryUrl is required"),
        ("security", "signingPrivateKeyPath", "signingPrivateKeyPath is required"),
        ("security", "signingKeyId", "signingKeyId is required"),
        ("mtls", "certificatePath", "certificatePath and privateKeyPath must be supplied together"),
        ("dcr", "softwareStatementAssertionPath", "softwareStatementAssertionPath is required"),
        ("dcr", "registrationAudience", "registrationAudience must be"),
    ],
)
def test_typed_dcr_config_rejects_missing_required_references(
    tmp_path: Path,
    section: str,
    key: str,
    message: str,
) -> None:
    """Each required DCR discovery or credential reference blocks validation."""
    security = _shared_security(tmp_path)
    dcr = _dcr_config(tmp_path)
    if section == "security":
        security.pop(key)
    elif section == "mtls":
        mtls = security["mtls"]
        assert isinstance(mtls, dict)
        mtls.pop(key)
    else:
        dcr.pop(key)

    with pytest.raises(ConfigError, match=message):
        parse_dcr_plan_configuration(security, dcr, {})


def test_typed_dcr_config_rejects_issuer_url_registration_audience(tmp_path: Path) -> None:
    """Runtime configuration enforces the Open Banking Base62 audience."""
    dcr = _dcr_config(tmp_path)
    dcr["registrationAudience"] = "https://aspsp.example.com/register"

    with pytest.raises(ConfigError, match="Base62 ASPSP identifier"):
        parse_dcr_plan_configuration(_shared_security(tmp_path), dcr, {})


def test_dcr_runtime_file_validation_rejects_nonexistent_references(tmp_path: Path) -> None:
    """Execution validation fails rather than fabricating missing credential data."""
    parsed = parse_dcr_plan_configuration(_shared_security(tmp_path), _dcr_config(tmp_path), {})

    with pytest.raises(ConfigError, match="must reference an existing file"):
        validate_dcr_file_references(parsed)
