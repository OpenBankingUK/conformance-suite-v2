"""Build PS256 JWTs for FAPI request objects and client assertions.

This module keeps JOSE signing logic separate from manifest parsing and
executor wiring so future request-object and private-key JWT flows can share
one typed, testable implementation.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from joserfc import jwk, jws, jwt
from joserfc.errors import InvalidKeyTypeError, JoseError

from conformance.model_bank_config import FapiSigningConfig
from conformance.signing_credentials import SigningCredentials

_DEFAULT_JWT_LIFETIME = timedelta(minutes=5)
"""Default lifetime for signed FAPI JWTs emitted by the tool."""


class JwtSigningError(ValueError):
    """Raised when the signing service cannot build a valid JWT."""


@dataclass(frozen=True)
class RequestObjectSigningInput:
    """Runtime values needed to build one OAuth 2.0 JAR request object.

    Attributes:
        issuer: ``iss`` claim identifying the OAuth client creating the JAR.
        audience: ``aud`` claim naming the ASPSP authorisation endpoint.
        client_id: OAuth ``client_id`` claim echoed inside the request object.
        redirect_uri: Registered redirect URI for the PSU callback.
        response_type: OAuth response type requested from the ASPSP.
        scope: OAuth scope carried in the request object.
        state: Opaque state value already registered for the PSU session.
    """

    issuer: str
    audience: str
    client_id: str
    redirect_uri: str
    response_type: str
    scope: str
    state: str


@dataclass(frozen=True)
class ClientAssertionSigningInput:
    """Runtime values needed to build one token-endpoint client assertion.

    Attributes:
        audience: Token endpoint URI used as the JWT ``aud`` claim.
    """

    audience: str


@dataclass(frozen=True)
class SignedJwt:
    """Opaque signed JWT plus non-secret metadata for audit trails.

    Attributes:
        token: Compact serialized JWT ready for transport.
        key_id: JOSE ``kid`` header used while signing.
        issuer: JWT ``iss`` claim value.
        subject: JWT ``sub`` claim value when present, otherwise ``None``.
        audience: JWT ``aud`` claim value.
        issued_at: UTC timestamp encoded into the JWT ``iat`` claim.
        expires_at: UTC timestamp encoded into the JWT ``exp`` claim.
        jwt_id: JWT ``jti`` claim value.
    """

    token: str
    key_id: str
    issuer: str
    subject: str | None
    audience: str
    issued_at: datetime
    expires_at: datetime
    jwt_id: str


@dataclass(frozen=True)
class FapiSigningService:
    """Sign PS256 JWTs using validated participant FAPI signing material.

    Attributes:
        signing_config: Non-secret FAPI signing metadata parsed from the
            participant config.
        signing_credentials: Runtime-loaded PEM material for the signing key.
        clock: Injectable UTC clock used for ``iat`` and ``exp`` claims.
        jwt_id_factory: Injectable unique-token identifier factory used for
            ``jti`` claims.
    """

    signing_config: FapiSigningConfig
    signing_credentials: SigningCredentials
    clock: Callable[[], datetime] = lambda: datetime.now(UTC)
    jwt_id_factory: Callable[[], str] = lambda: uuid4().hex

    def sign_request_object(
        self,
        request_object: RequestObjectSigningInput,
        *,
        lifetime: timedelta = _DEFAULT_JWT_LIFETIME,
    ) -> SignedJwt:
        """Sign a FAPI request object for the PSU authorisation redirect.

        Args:
            request_object: Runtime values that must be embedded into the JAR
                request object claims.
            lifetime: Requested JWT validity window.

        Returns:
            Signed compact JWT and its audit-safe metadata.

        Raises:
            JwtSigningError: If required values are blank, the clock is
                invalid, the lifetime is non-positive, or the key cannot sign.
        """
        issuer = _require_non_empty_string(request_object.issuer, label="request_object.issuer")
        audience = _require_non_empty_string(request_object.audience, label="request_object.audience")
        client_id = _require_non_empty_string(request_object.client_id, label="request_object.client_id")
        redirect_uri = _require_non_empty_string(request_object.redirect_uri, label="request_object.redirect_uri")
        response_type = _require_non_empty_string(
            request_object.response_type,
            label="request_object.response_type",
        )
        scope = _require_non_empty_string(request_object.scope, label="request_object.scope")
        state = _require_non_empty_string(request_object.state, label="request_object.state")

        issued_at, expires_at = _build_token_window(clock=self.clock, lifetime=lifetime)
        jwt_id = _build_jwt_id(self.jwt_id_factory)
        claims: dict[str, object] = {
            "iss": issuer,
            "aud": audience,
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": response_type,
            "scope": scope,
            "state": state,
            "iat": int(issued_at.timestamp()),
            "exp": int(expires_at.timestamp()),
            "jti": jwt_id,
        }
        token = _sign_ps256_jwt(
            self.signing_credentials.signing_private_key_pem,
            key_id=self.signing_config.key_id,
            claims=claims,
        )
        return SignedJwt(
            token=token,
            key_id=self.signing_config.key_id,
            issuer=issuer,
            subject=None,
            audience=audience,
            issued_at=issued_at,
            expires_at=expires_at,
            jwt_id=jwt_id,
        )

    def sign_client_assertion(
        self,
        client_assertion: ClientAssertionSigningInput,
        *,
        lifetime: timedelta = _DEFAULT_JWT_LIFETIME,
    ) -> SignedJwt:
        """Sign a private-key JWT client assertion for the token endpoint.

        Args:
            client_assertion: Runtime values that must be embedded into the
                token-endpoint client assertion.
            lifetime: Requested JWT validity window.

        Returns:
            Signed compact JWT and its audit-safe metadata.

        Raises:
            JwtSigningError: If required values are blank, the clock is
                invalid, the lifetime is non-positive, or the key cannot sign.
        """
        issuer = _require_non_empty_string(
            self.signing_config.client_assertion_issuer,
            label="fapiSigning.clientAssertionIssuer",
        )
        subject = _require_non_empty_string(
            self.signing_config.client_assertion_subject,
            label="fapiSigning.clientAssertionSubject",
        )
        audience = _require_non_empty_string(client_assertion.audience, label="client_assertion.audience")

        issued_at, expires_at = _build_token_window(clock=self.clock, lifetime=lifetime)
        jwt_id = _build_jwt_id(self.jwt_id_factory)
        claims: dict[str, object] = {
            "iss": issuer,
            "sub": subject,
            "aud": audience,
            "iat": int(issued_at.timestamp()),
            "exp": int(expires_at.timestamp()),
            "jti": jwt_id,
        }
        token = _sign_ps256_jwt(
            self.signing_credentials.signing_private_key_pem,
            key_id=self.signing_config.key_id,
            claims=claims,
        )
        return SignedJwt(
            token=token,
            key_id=self.signing_config.key_id,
            issuer=issuer,
            subject=subject,
            audience=audience,
            issued_at=issued_at,
            expires_at=expires_at,
            jwt_id=jwt_id,
        )

    def sign_detached_json_payload(self, payload: bytes) -> str:
        """Sign JSON request bytes as a detached Open Banking JWS.

        Args:
            payload: Exact JSON bytes that will be transmitted on the wire.

        Returns:
            Detached compact JWS suitable for the ``x-jws-signature`` header.

        Raises:
            JwtSigningError: If the payload bytes cannot be signed with the
                configured RSA private key.
        """
        return _sign_ps256_detached_jws(
            self.signing_credentials.signing_private_key_pem,
            key_id=self.signing_config.key_id,
            payload=payload,
        )


def _build_token_window(*, clock: Callable[[], datetime], lifetime: timedelta) -> tuple[datetime, datetime]:
    """Build UTC ``iat`` and ``exp`` timestamps for a signed JWT.

    Args:
        clock: Callable returning the current time.
        lifetime: Requested validity window for the token.

    Returns:
        Tuple of ``(issued_at, expires_at)`` in UTC.

    Raises:
        JwtSigningError: If the clock returns a naive timestamp or the
            lifetime is not strictly positive.
    """
    lifetime_seconds = int(lifetime.total_seconds())
    if lifetime_seconds <= 0:
        raise JwtSigningError("JWT lifetime must be greater than zero seconds")

    issued_at = clock()
    if issued_at.tzinfo is None or issued_at.utcoffset() is None:
        raise JwtSigningError("Signing clock must return a timezone-aware datetime")

    issued_at_utc = issued_at.astimezone(UTC)
    expires_at = issued_at_utc + timedelta(seconds=lifetime_seconds)
    return issued_at_utc, expires_at


def _build_jwt_id(jwt_id_factory: Callable[[], str]) -> str:
    """Build and validate one JWT identifier.

    Args:
        jwt_id_factory: Callable producing a candidate JWT identifier.

    Returns:
        Non-empty JWT identifier.

    Raises:
        JwtSigningError: If the generated identifier is blank.
    """
    jwt_id = jwt_id_factory().strip()
    if not jwt_id:
        raise JwtSigningError("JWT ID factory must return a non-empty string")
    return jwt_id


def _require_non_empty_string(value: str, *, label: str) -> str:
    """Validate that one runtime string field is non-empty.

    Args:
        value: Candidate string value.
        label: Human-readable field name for error reporting.

    Returns:
        Stripped non-empty string.

    Raises:
        JwtSigningError: If the value is empty or whitespace-only.
    """
    stripped_value = value.strip()
    if not stripped_value:
        raise JwtSigningError(f"{label} must be a non-empty string")
    return stripped_value


def _sign_ps256_jwt(private_key_pem: bytes, *, key_id: str, claims: dict[str, object]) -> str:
    """Sign one PS256 compact JWT with the configured RSA private key.

    Args:
        private_key_pem: PEM-encoded RSA private key bytes.
        key_id: JOSE ``kid`` header value to emit.
        claims: JWT claims to serialize and sign.

    Returns:
        Compact serialized JWT.

    Raises:
        JwtSigningError: If the PEM bytes cannot be imported or the JOSE
            library rejects the signing request.
    """
    try:
        signing_key = jwk.import_key(private_key_pem, key_type="RSA")
        return jwt.encode(
            {"alg": "PS256", "kid": key_id, "typ": "JWT"},
            claims,
            signing_key,
            algorithms=["PS256"],
        )
    except (InvalidKeyTypeError, JoseError, TypeError, ValueError) as error:
        raise JwtSigningError("Unable to sign PS256 JWT with configured FAPI signing key") from error


def _sign_ps256_detached_jws(private_key_pem: bytes, *, key_id: str, payload: bytes) -> str:
    """Sign one detached PS256 JWS over exact request-body bytes.

    Args:
        private_key_pem: PEM-encoded RSA private key bytes.
        key_id: JOSE ``kid`` header value to emit.
        payload: Exact bytes that must be covered by the detached signature.

    Returns:
        Detached compact JWS with an empty payload segment.

    Raises:
        JwtSigningError: If the PEM bytes cannot be imported or the JOSE
            library rejects the signing request.
    """
    try:
        signing_key = jwk.import_key(private_key_pem, key_type="RSA")
        compact_jws = jws.serialize_compact(
            {"alg": "PS256", "kid": key_id, "b64": False, "crit": ["b64"]},
            payload,
            signing_key,
            algorithms=["PS256"],
        )
        return jws.detach_content(compact_jws)
    except (InvalidKeyTypeError, JoseError, TypeError, ValueError) as error:
        raise JwtSigningError("Unable to sign PS256 detached JWS with configured FAPI signing key") from error
