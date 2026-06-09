"""Unit tests for the pure FAPI JWT signing service."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from joserfc import jwk, jws, jwt

from conformance.model_bank_config import FapiSigningConfig
from conformance.signing_credentials import SigningCredentials, load_signing_credentials
from conformance.signing_service import (
    ClientAssertionSigningInput,
    FapiSigningService,
    JwtSigningError,
    RequestObjectSigningInput,
)


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
        client_assertion_issuer="client-issuer",
        client_assertion_subject="client-subject",
        token_endpoint_auth_method="private_key_jwt",  # noqa: S106 - auth-method enum fixture, not a secret
    )


def _decode_signed_token(token: str, *, certificate_pem: bytes) -> tuple[dict[str, object], dict[str, object]]:
    public_key = jwk.import_key(certificate_pem, key_type="RSA")
    decoded_token = jwt.decode(token, public_key, algorithms=["PS256"])
    header = decoded_token.header
    claims = decoded_token.claims
    assert isinstance(header, dict)
    assert isinstance(claims, dict)
    return header, claims


def _build_signing_service(
    config: FapiSigningConfig,
    *,
    credentials: SigningCredentials | None = None,
    now: datetime | None = None,
    jwt_id: str = "jwt-001",
) -> FapiSigningService:
    effective_credentials = credentials or load_signing_credentials(config)
    effective_now = now or datetime(2026, 6, 8, 12, 0, tzinfo=UTC)
    return FapiSigningService(
        signing_config=config,
        signing_credentials=effective_credentials,
        clock=lambda: effective_now,
        jwt_id_factory=lambda: jwt_id,
    )


@pytest.mark.unit
def test_sign_request_object_builds_ps256_jar_with_expected_claims(tmp_path: Path) -> None:
    certificate_root = tmp_path / "certs"
    certificate_root.mkdir()
    certificate_path, private_key_path = _write_signing_pair(certificate_root, stem="signing")
    config = _build_signing_config(
        certificate_root,
        certificate_path=certificate_path,
        private_key_path=private_key_path,
    )
    service = _build_signing_service(config)

    signed_jwt = service.sign_request_object(
        RequestObjectSigningInput(
            issuer="request-object-issuer",
            audience="https://auth.example.com/authorize",
            client_id="client-123",
            redirect_uri="https://rp.example.com/callback",
            response_type="code id_token",
            scope="openid accounts",
            state="state-123",
            nonce="nonce-123",
        )
    )
    header, claims = _decode_signed_token(signed_jwt.token, certificate_pem=certificate_path.read_bytes())

    assert header == {"alg": "PS256", "kid": "signing-key-001", "typ": "JWT"}
    assert claims == {
        "iss": "request-object-issuer",
        "aud": "https://auth.example.com/authorize",
        "client_id": "client-123",
        "redirect_uri": "https://rp.example.com/callback",
        "response_type": "code id_token",
        "scope": "openid accounts",
        "state": "state-123",
        "nonce": "nonce-123",
        "iat": 1_780_920_000,
        "nbf": 1_780_920_000,
        "exp": 1_780_920_300,
        "jti": "jwt-001",
    }
    assert signed_jwt.key_id == "signing-key-001"
    assert signed_jwt.issuer == "request-object-issuer"
    assert signed_jwt.subject is None
    assert signed_jwt.audience == "https://auth.example.com/authorize"
    assert signed_jwt.issued_at == datetime(2026, 6, 8, 12, 0, tzinfo=UTC)
    assert signed_jwt.expires_at == datetime(2026, 6, 8, 12, 5, tzinfo=UTC)
    assert signed_jwt.jwt_id == "jwt-001"


@pytest.mark.unit
def test_sign_client_assertion_builds_ps256_private_key_jwt_with_expected_claims(tmp_path: Path) -> None:
    certificate_root = tmp_path / "certs"
    certificate_root.mkdir()
    certificate_path, private_key_path = _write_signing_pair(certificate_root, stem="signing")
    config = _build_signing_config(
        certificate_root,
        certificate_path=certificate_path,
        private_key_path=private_key_path,
    )
    service = _build_signing_service(
        config,
        now=datetime(2026, 6, 8, 13, 15, tzinfo=UTC),
        jwt_id="assertion-jti-123",
    )

    signed_jwt = service.sign_client_assertion(ClientAssertionSigningInput(audience="https://auth.example.com/token"))
    header, claims = _decode_signed_token(signed_jwt.token, certificate_pem=certificate_path.read_bytes())

    assert header == {"alg": "PS256", "kid": "signing-key-001", "typ": "JWT"}
    assert claims == {
        "iss": "client-issuer",
        "sub": "client-subject",
        "aud": "https://auth.example.com/token",
        "iat": 1_780_924_500,
        "exp": 1_780_924_800,
        "jti": "assertion-jti-123",
    }
    assert signed_jwt.issuer == "client-issuer"
    assert signed_jwt.subject == "client-subject"
    assert signed_jwt.audience == "https://auth.example.com/token"
    assert signed_jwt.issued_at == datetime(2026, 6, 8, 13, 15, tzinfo=UTC)
    assert signed_jwt.expires_at == datetime(2026, 6, 8, 13, 20, tzinfo=UTC)


@pytest.mark.unit
def test_sign_request_object_rejects_blank_runtime_fields(tmp_path: Path) -> None:
    certificate_root = tmp_path / "certs"
    certificate_root.mkdir()
    certificate_path, private_key_path = _write_signing_pair(certificate_root, stem="signing")
    config = _build_signing_config(
        certificate_root,
        certificate_path=certificate_path,
        private_key_path=private_key_path,
    )
    service = _build_signing_service(config)

    with pytest.raises(JwtSigningError, match="request_object.audience must be a non-empty string"):
        service.sign_request_object(
            RequestObjectSigningInput(
                issuer="request-object-issuer",
                audience="   ",
                client_id="client-123",
                redirect_uri="https://rp.example.com/callback",
                response_type="code",
                scope="openid",
                state="state-123",
                nonce="nonce-123",
            )
        )


@pytest.mark.unit
def test_sign_client_assertion_rejects_non_positive_lifetime(tmp_path: Path) -> None:
    certificate_root = tmp_path / "certs"
    certificate_root.mkdir()
    certificate_path, private_key_path = _write_signing_pair(certificate_root, stem="signing")
    config = _build_signing_config(
        certificate_root,
        certificate_path=certificate_path,
        private_key_path=private_key_path,
    )
    service = _build_signing_service(config)

    with pytest.raises(JwtSigningError, match="JWT lifetime must be greater than zero seconds"):
        service.sign_client_assertion(
            ClientAssertionSigningInput(audience="https://auth.example.com/token"),
            lifetime=timedelta(0),
        )


@pytest.mark.unit
def test_signing_service_rejects_invalid_private_key_without_echoing_contents(tmp_path: Path) -> None:
    certificate_root = tmp_path / "certs"
    certificate_root.mkdir()
    certificate_path, private_key_path = _write_signing_pair(certificate_root, stem="signing")
    config = _build_signing_config(
        certificate_root,
        certificate_path=certificate_path,
        private_key_path=private_key_path,
    )
    key_marker = b"PRIVATE_KEY_MARKER_FOR_SIGNING_SERVICE"
    service = _build_signing_service(
        config,
        credentials=SigningCredentials(
            signing_certificate_pem=certificate_path.read_bytes(),
            signing_private_key_pem=b"not-a-private-key " + key_marker,
        ),
    )

    with pytest.raises(JwtSigningError) as error_info:
        service.sign_client_assertion(ClientAssertionSigningInput(audience="https://auth.example.com/token"))

    assert str(error_info.value) == "Unable to sign PS256 JWT with configured FAPI signing key"
    assert key_marker.decode("ascii") not in str(error_info.value)


@pytest.mark.unit
def test_sign_detached_json_payload_builds_detached_ps256_signature(tmp_path: Path) -> None:
    certificate_root = tmp_path / "certs"
    certificate_root.mkdir()
    certificate_path, private_key_path = _write_signing_pair(certificate_root, stem="signing")
    config = _build_signing_config(
        certificate_root,
        certificate_path=certificate_path,
        private_key_path=private_key_path,
    )
    service = _build_signing_service(config)
    payload = b'{"Data":{"Permissions":["ReadAccountsBasic"]},"Risk":{}}'

    detached_signature = service.sign_detached_json_payload(payload)
    verified = jws.deserialize_compact(
        detached_signature,
        jwk.import_key(certificate_path.read_bytes(), key_type="RSA"),
        algorithms=["PS256"],
        payload=payload,
    )

    assert detached_signature.split(".")[1] == ""
    assert verified.headers() == {"alg": "PS256", "kid": "signing-key-001", "b64": False, "crit": ["b64"]}
    assert verified.payload == payload
