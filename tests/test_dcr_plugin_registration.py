"""Unit tests for conformance.plugins.dcr.registration module."""

from __future__ import annotations

import base64
import json
from unittest.mock import MagicMock, patch

import pytest

from conformance.dcr.credentials import DcrCredentials
from conformance.plugins.dcr.registration import (
    DcrRegistrationError,
    DcrRegistrationJwtInput,
    build_negative_registration_jwt_expired_ssa,
    build_negative_registration_jwt_invalid_auth_method,
    build_negative_registration_jwt_wrong_issuer,
    build_negative_registration_jwt_wrong_response_type,
    build_registration_jwt,
    derive_kid,
    parse_ssa_claims,
)

# ---------------------------------------------------------------------------
# Test RSA key material (2048-bit, for testing only — NOT real credentials)
# ---------------------------------------------------------------------------

_TEST_RSA_PRIVATE_KEY_PEM = b"""-----BEGIN RSA PRIVATE KEY-----  # pragma: allowlist secret
MIIEowIBAAKCAQEA2a2rwplBQLF29amygykEMmYz0+Kcj3bKBp29P2rFj7Mg2bIM
N3AHENqEMf+bRMnFuHgZFRbNE9bELTcIDgSVSQIX+pBRdPPBPTXRGGmr4siySwwT
8eaG3VLEEDh7wHnHCZ1GMHPjF3zqgcgKPt6d7jHsHfR+Bwv+vHYMWnv8vy7BJ4cE
WFxwnqc5s2QFqEzVBhqhDOaRSHlJBJv0NW7KOhxjYXmSPWVLMYNB42+DGkH8YVID
x3FcKWFVbXrWD1gE/ZjCrPEW5Kh4jFMfNhFh+fI7KqiYAWHECjm2ANDr0pJNIwPb
2kCrBhxzjTVYm4YBWTxNZ4MMYjbVRHjXXXXXXwIDAQABAoIBAH9jJh/PrlFWmBTT
6sTNmMGpThICDxGxFSz6YX5TJFhN03B8KWD4FAaZJUhMJqbJDiRvI8HLH0Hf3W3G
qR6FiL9jJRtf/smJY2RNi/pEbJrDsw0gI4xFcNkv8tE1h2hDi7yqDI5AWXF0XDt6
s5CiZe9yxXMjbp+AMKXjMHFKdmjkDvlQBGlSsGGiVV+gGFZTp5F3KPDkFT5oI7TA
UVAQkmgdx7TDCrCz0bqKFpZFpuRHfX0zLuqVAj3FKT9G5nGCm1Kt8z0pAkjDNb9U
QOy8G5MiPU0u8f5qBklX2RTLRP/pJJeE/0H6qGHLGJGqvNJiKjJBqEUXAXsX5Jol
6bVhprECgYEA7wg5tZ9NwjsPD5lq/VGizXiflxRGdP3kfI8FTZ4ikXKHfpOp4F8U
IgvtFG9nG18N7hk5sLTjl7RoH2eC1p2AqR5DcXBL5+MZi0Y38h2BDuVAE5U/sWMK
5i8A6Z/JXJoSt6HGhKANpHKWLHtbJb6fHVtJXnuHQBLM4LVzMPkCgYEA6R4+BQJo
kRhLN/y7ZKA7eQxK3NrKs2G5I4MLh1GlR/KJi/Y2DxPnF4FGCCbZQ7TiR2rCqZN0
VBvgm/APCVtDYsAYENNVGFRNyHsD3kHN38zD0TBgL/kniXS5pN/3IfxAUNWDSPpU
ZdIEZq5hqRp8xH5S+UhEsVAn/EUlzP9GOzECgYEAm/1iw9iZQi5UwrSU8MYqAoA9
OfN2qvTJ8RG8+/hFH3UE/1tNiJe0kRUm4Q1LXqP0EEbblFpS3DnW6y7l3vS5q1wM
wr5N8D3F1k/JXPZ3F4MhPQv0fxJl4g5cYsQl8H9+p1MAe7g+0eKGSlqJECrLFp9W
Jf5HNTBJ+r8kUj4oTSkCgYEAu+c6M2CJiGbSnDTHQ6UMt7bExaZ5eH4yrCUL3LhD
OdlDAKmqGvnL/kOoB5kGHMrSF9kMnBiR4eVZOoCZPgNTjTPb4S2e0r4z05aKlXXk
/kN47A7BH0/4G3L8Sn3w1CsaX5JAZhCPDFCG/87bfVxlbFnAjL7xc82R0vhC+rEC
gYDE3lj7ELR42nWtIxjJXvnQAZ73r+2BO2Frc8Q1rSZJsXHJi5GMZ/QiC8MWKAlU
xAr0BW5mhj1XNIvJEBFEOq6b3ZTv9yxLsIE3QMPA3aO8NVF8A7L0H2M+WCn5a9G1
q0oZCPJaBCQi1kqvRXGTK+w1qT7LJj5k7C5MqOCEtA==
-----END RSA PRIVATE KEY-----"""
"""2048-bit RSA private key for unit tests — not real credentials."""

_TEST_CERT_PEM = b"""-----BEGIN CERTIFICATE-----
MIICpDCCAYwCCQDU+pQ4pHgSpDANBgkqhkiG9w0BAQsFADAUMRIwEAYDVQQDDAls
b2NhbGhvc3QwHhcNMjQwMTAxMDAwMDAwWhcNMjUwMTAxMDAwMDAwWjAUMRIwEAYD
VQQDDAlsb2NhbGhvc3QwggEiMA0GCSqGSIb3DQEBAQUAA4IBDwAwggEKAoIBAQDZ
ravCmUFAsXb1qbKDKQQyZjPT4pyPdsoGnb0/asWPsyDZsgw3cAcQ2oQx/5tEycW4
eBkVFs0T1sQtNwgOBJVJAhf6kFF088E9NdEYaaviyLJLDBPx5obdUsQQOHvAeccJ
nUYwc+MXfOqByAo+3p3uMewd9H4HC/68dgxae/y/LsEnhwRYXHCepzmzZAWoTNUG
GqEM5pFIeUkEm/Q1bso6HGNheZI9ZUsxg0Hjb4MaQfxhUgPHcVwpYVVteta WWWW
cdjQ6DAGcMNAIDAQABMA0GCSqGSIb3DQEBCwUAA4IBAQCqlEBgbN3F2B5q1jKp8m
XKnA7bJkF7gRHF5V3INuQpHr2wIXDK5qmR4l9PfF3nL8wGJh2MpQtTz8S0NNRN
-----END CERTIFICATE-----"""
"""Placeholder PEM certificate for unit tests."""


def _make_credentials() -> DcrCredentials:
    """Build a mock DcrCredentials object with test key material."""
    return DcrCredentials(
        ssa_jwt=_make_minimal_ssa_bytes(),
        signing_private_key_pem=_TEST_RSA_PRIVATE_KEY_PEM,
        signing_certificate_pem=_TEST_CERT_PEM,
        transport_certificate_pem=_TEST_CERT_PEM,
        transport_private_key_pem=_TEST_RSA_PRIVATE_KEY_PEM,
    )


def _make_jwt_input(**overrides: object) -> DcrRegistrationJwtInput:
    """Build a minimal DcrRegistrationJwtInput for tests."""
    defaults: dict[str, object] = {
        "issuer": "test-software-001",
        "audience": "https://as.example.com",
        "redirect_uris": ["https://tpp.example.com/callback"],
        "token_endpoint_auth_method": "tls_client_auth",
        "grant_types": ["authorization_code", "client_credentials"],
        "response_types": ["code"],
        "software_statement": "header.payload.signature",
    }
    defaults.update(overrides)
    return DcrRegistrationJwtInput(**defaults)  # type: ignore[arg-type]


def _make_minimal_ssa_bytes() -> bytes:
    """Build minimal SSA JWT bytes for testing claim parsing."""
    claims = {
        "iss": "openbanking.org.uk",
        "sub": "test-software-001",
        "software_id": "test-software-001",
        "software_redirect_uris": ["https://tpp.example.com/callback"],
    }
    header = base64.urlsafe_b64encode(b'{"alg":"PS256"}').rstrip(b"=")
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=")
    return header + b"." + payload + b".fakesignature"


@pytest.mark.unit
class TestDeriveKid:
    """Verify derive_kid produces a stable deterministic identifier."""

    def test_returns_16_char_hex_string(self) -> None:
        """derive_kid returns exactly 16 hexadecimal characters."""
        kid = derive_kid(_TEST_CERT_PEM)
        assert len(kid) == 16  # noqa: PLR2004
        assert all(c in "0123456789abcdef" for c in kid)

    def test_is_deterministic(self) -> None:
        """Same certificate bytes always produce the same kid."""
        assert derive_kid(_TEST_CERT_PEM) == derive_kid(_TEST_CERT_PEM)

    def test_differs_for_different_certs(self) -> None:
        """Different certificate bytes produce different kid values."""
        cert2 = _TEST_CERT_PEM + b" "
        assert derive_kid(_TEST_CERT_PEM) != derive_kid(cert2)


@pytest.mark.unit
class TestParseSsaClaims:
    """Verify parse_ssa_claims decodes JWT payload without signature verification."""

    def test_extracts_software_id(self) -> None:
        """software_id is extracted from the SSA payload."""
        claims = parse_ssa_claims(_make_minimal_ssa_bytes())
        assert claims.get("software_id") == "test-software-001"

    def test_extracts_redirect_uris(self) -> None:
        """software_redirect_uris is extracted from the SSA payload."""
        claims = parse_ssa_claims(_make_minimal_ssa_bytes())
        assert claims.get("software_redirect_uris") == ["https://tpp.example.com/callback"]

    def test_raises_for_invalid_jwt(self) -> None:
        """DcrRegistrationError is raised for bytes that are not a valid compact JWT."""
        with pytest.raises(DcrRegistrationError, match="SSA does not appear to be a compact JWT"):
            parse_ssa_claims(b"not-a-jwt")

    def test_raises_for_non_json_payload(self) -> None:
        """DcrRegistrationError is raised when the JWT payload is not JSON."""
        invalid = b"header." + base64.urlsafe_b64encode(b"not-json").rstrip(b"=") + b".sig"
        with pytest.raises(DcrRegistrationError):
            parse_ssa_claims(invalid)


@pytest.mark.unit
class TestRegistrationJwtStructure:
    """Verify registration JWT claim structure without network calls."""

    def test_jwt_is_three_parts(self) -> None:
        """A valid compact JWT has exactly three dot-separated parts."""
        creds = _make_credentials()
        jwt_input = _make_jwt_input()

        with (
            patch("conformance.plugins.dcr.registration.jwk") as mock_jwk,
            patch("conformance.plugins.dcr.registration.jwt") as mock_jwt,
        ):
            mock_key = MagicMock()
            mock_jwk.import_key.return_value = mock_key
            mock_jwt.encode.return_value = "header.payload.signature"

            result = build_registration_jwt(jwt_input, creds)

        assert result.count(".") == 2  # noqa: PLR2004

    def test_jwt_encode_called_with_ps256(self) -> None:
        """jwt.encode is called with PS256 algorithm."""
        creds = _make_credentials()
        jwt_input = _make_jwt_input()

        with (
            patch("conformance.plugins.dcr.registration.jwk") as mock_jwk,
            patch("conformance.plugins.dcr.registration.jwt") as mock_jwt,
        ):
            mock_key = MagicMock()
            mock_jwk.import_key.return_value = mock_key
            mock_jwt.encode.return_value = "h.p.s"

            build_registration_jwt(jwt_input, creds)

        call_kwargs = mock_jwt.encode.call_args
        assert call_kwargs[0][0]["alg"] == "PS256"

    def test_wrong_issuer_variant_overrides_iss(self) -> None:
        """build_negative_registration_jwt_wrong_issuer replaces the iss claim."""
        creds = _make_credentials()
        jwt_input = _make_jwt_input()
        captured_claims: list[dict[str, object]] = []

        with (
            patch("conformance.plugins.dcr.registration.jwk") as mock_jwk,
            patch("conformance.plugins.dcr.registration.jwt") as mock_jwt,
        ):
            mock_key = MagicMock()
            mock_jwk.import_key.return_value = mock_key

            def capture_encode(header: object, claims: dict[str, object], key: object, **kw: object) -> str:
                """Capture JWT claims for assertion."""
                captured_claims.append(dict(claims))
                return "h.p.s"

            mock_jwt.encode.side_effect = capture_encode
            build_negative_registration_jwt_wrong_issuer(jwt_input, creds)

        assert captured_claims[0]["iss"] == "invalid-issuer-dcr-test-ob-conformance"

    def test_invalid_auth_method_variant_sets_client_secret_post(self) -> None:
        """build_negative_registration_jwt_invalid_auth_method sets client_secret_post."""
        creds = _make_credentials()
        jwt_input = _make_jwt_input()
        captured_claims: list[dict[str, object]] = []

        with (
            patch("conformance.plugins.dcr.registration.jwk") as mock_jwk,
            patch("conformance.plugins.dcr.registration.jwt") as mock_jwt,
        ):
            mock_key = MagicMock()
            mock_jwk.import_key.return_value = mock_key

            def capture_encode(header: object, claims: dict[str, object], key: object, **kw: object) -> str:
                """Capture JWT claims for assertion."""
                captured_claims.append(dict(claims))
                return "h.p.s"

            mock_jwt.encode.side_effect = capture_encode
            build_negative_registration_jwt_invalid_auth_method(jwt_input, creds)

        assert captured_claims[0]["token_endpoint_auth_method"] == "client_secret_post"  # noqa: S105

    def test_wrong_response_type_variant_sets_token_id_token(self) -> None:
        """build_negative_registration_jwt_wrong_response_type sets wrong types."""
        creds = _make_credentials()
        jwt_input = _make_jwt_input()
        captured_claims: list[dict[str, object]] = []

        with (
            patch("conformance.plugins.dcr.registration.jwk") as mock_jwk,
            patch("conformance.plugins.dcr.registration.jwt") as mock_jwt,
        ):
            mock_key = MagicMock()
            mock_jwk.import_key.return_value = mock_key

            def capture_encode(header: object, claims: dict[str, object], key: object, **kw: object) -> str:
                """Capture JWT claims for assertion."""
                captured_claims.append(dict(claims))
                return "h.p.s"

            mock_jwt.encode.side_effect = capture_encode
            build_negative_registration_jwt_wrong_response_type(jwt_input, creds)

        assert "code" not in captured_claims[0]["response_types"]  # type: ignore[operator]
        assert "token" in captured_claims[0]["response_types"]  # type: ignore[operator]

    def test_expired_ssa_variant_builds_nested_ssa(self) -> None:
        """build_negative_registration_jwt_expired_ssa produces two JWT encode calls."""
        creds = _make_credentials()
        jwt_input = _make_jwt_input()
        encode_call_count: list[int] = [0]

        with (
            patch("conformance.plugins.dcr.registration.jwk") as mock_jwk,
            patch("conformance.plugins.dcr.registration.jwt") as mock_jwt,
        ):
            mock_key = MagicMock()
            mock_jwk.import_key.return_value = mock_key

            def count_encode(header: object, claims: object, key: object, **kw: object) -> str:
                """Count encode calls and return a fake JWT."""
                encode_call_count[0] += 1
                return f"h.p.s{encode_call_count[0]}"

            mock_jwt.encode.side_effect = count_encode
            build_negative_registration_jwt_expired_ssa(
                jwt_input,
                creds,
                software_id="test-software-001",
            )

        # One call for the fake SSA, one call for the outer registration JWT.
        assert encode_call_count[0] == 2  # noqa: PLR2004
