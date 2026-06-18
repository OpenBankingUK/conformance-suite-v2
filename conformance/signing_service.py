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
from joserfc.jwk import RSAKey
from joserfc.jws import JWSRegistry
from joserfc.registry import HeaderParameter
from joserfc.util import json_b64decode, urlsafe_b64encode

from conformance.json_types import JsonValue
from conformance.model_bank_config import FapiSigningConfig
from conformance.signing_credentials import SigningCredentials

_DEFAULT_JWT_LIFETIME = timedelta(minutes=5)
"""Default lifetime for signed FAPI JWTs emitted by the tool."""

_OB_JWS_HEADER_REGISTRY = JWSRegistry(
    header_registry={
        "http://openbanking.org.uk/iat": HeaderParameter("Open Banking issued-at timestamp (epoch seconds)", "int"),
        "http://openbanking.org.uk/iss": HeaderParameter("Open Banking signing issuer identifier", "str"),
        "http://openbanking.org.uk/tan": HeaderParameter("Open Banking trust-anchor domain", "str"),
    },
    algorithms=["PS256"],
)
"""JWS registry extended with the Open Banking mandatory detached-JWS header parameters.

Used when ``signatureIssuer`` and ``signatureTrustAnchor`` are configured so that
joserfc accepts ``http://openbanking.org.uk/iat``, ``iss``, and ``tan`` in ``crit``.
"""


class JwtSigningError(ValueError):
    """Raised when the signing service cannot build a valid JWT."""


class DetachedJwsVerificationError(ValueError):
    """Raised when a detached JWS cannot be verified."""


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
        nonce: OIDC nonce value bound to the authorisation request.
        openbanking_intent_id: Optional consent identifier copied into the
            Open Banking ``claims.id_token.openbanking_intent_id`` claim.
    """

    issuer: str
    audience: str
    client_id: str
    redirect_uri: str
    response_type: str
    scope: str
    state: str
    nonce: str
    openbanking_intent_id: str | None = None


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
        nonce = _require_non_empty_string(request_object.nonce, label="request_object.nonce")
        openbanking_intent_id = (
            _require_non_empty_string(
                request_object.openbanking_intent_id,
                label="request_object.openbanking_intent_id",
            )
            if request_object.openbanking_intent_id is not None
            else None
        )

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
            "nonce": nonce,
            "iat": int(issued_at.timestamp()),
            "nbf": int(issued_at.timestamp()),
            "exp": int(expires_at.timestamp()),
            "jti": jwt_id,
        }
        if openbanking_intent_id is not None:
            claims["claims"] = {
                "id_token": {
                    "openbanking_intent_id": {
                        "essential": True,
                        "value": openbanking_intent_id,
                    }
                }
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

        When the signing configuration includes both ``signature_issuer`` and
        ``signature_trust_anchor``, the protected JOSE header is extended with
        the Open Banking mandatory claims required by the Read/Write Data API
        Specification:

        - ``http://openbanking.org.uk/iat`` — signing timestamp in epoch seconds.
        - ``http://openbanking.org.uk/iss`` — configured signature issuer.
        - ``http://openbanking.org.uk/tan`` — configured trust-anchor domain.

        All three OB claims are also added to the ``crit`` list when present,
        as required by the OB JWS specification. For Read/Write v3.1.4 and
        later, including v4.x PIS/AIS, Open Banking signs the base64url-encoded
        payload and omits the older RFC 7797 ``b64=false`` header.

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
            signature_issuer=self.signing_config.signature_issuer,
            signature_trust_anchor=self.signing_config.signature_trust_anchor,
        )

    def sign_detached_json_payload_omit_b64_claim(self, payload: bytes) -> str:
        """Sign JSON request bytes as a compact JWS without the OB ``b64``/``crit`` claims.

        Produces a compact JWS whose protected header carries ``alg`` and ``kid``
        but intentionally omits the ``b64`` and ``crit`` claims required by the
        Open Banking detached-JWS specification (RFC 7797 §3 / OB Read/Write Data
        API Specification §7). The resulting token is syntactically valid JWT but
        semantically incompatible with OB detached JWS — sending it as the
        ``x-jws-signature`` header should trigger a 400 response with an OB
        ``UK.OBIE.Signature.Missing`` or ``UK.OBIE.Signature.Malformed`` error code.

        This implements the ``omit-jwt-claim`` signing negative case for the
        ``OB-400-DOP-100110`` test.

        Args:
            payload: Exact JSON bytes that will be transmitted on the wire.

        Returns:
            Compact JWS (not detached) without ``b64`` or ``crit`` header claims,
            suitable for use as a malformed ``x-jws-signature`` header value.

        Raises:
            JwtSigningError: If the payload bytes cannot be signed with the
                configured RSA private key.
        """
        return _sign_ps256_compact_jws_no_b64(
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


def verify_ps256_detached_jws(*, signature: str, payload: bytes, jwks: JsonValue) -> None:
    """Verify a detached PS256 JWS against a JWKS and exact payload bytes.

    Args:
        signature: Detached compact JWS value from the ``x-jws-signature`` header.
        payload: Exact HTTP response body bytes covered by the detached JWS.
        jwks: JWKS JSON object containing candidate ASPSP public keys.

    Raises:
        DetachedJwsVerificationError: If the signature, payload, or JWKS is
            missing, malformed, unsupported, or fails cryptographic verification.
    """
    stripped_signature = signature.strip()
    if not stripped_signature:
        raise DetachedJwsVerificationError("response signature header is empty")
    protected_header = _protected_header_from_detached_jws(stripped_signature)
    algorithm = protected_header.get("alg")
    if algorithm != "PS256":
        raise DetachedJwsVerificationError("response signature alg must be PS256")
    key_id = protected_header.get("kid")
    if not isinstance(key_id, str) or not key_id:
        raise DetachedJwsVerificationError("response signature kid is missing")
    verification_key = _public_jwk_from_jwks(jwks, key_id=key_id)
    attached_jws = _attach_detached_payload(stripped_signature, payload)
    try:
        jws.deserialize_compact(attached_jws, verification_key, algorithms=["PS256"], registry=_OB_JWS_HEADER_REGISTRY)
    except (JoseError, TypeError, ValueError) as error:
        raise DetachedJwsVerificationError("response x-jws-signature verification failed") from error


def _protected_header_from_detached_jws(signature: str) -> dict[str, object]:
    """Decode a compact JWS protected header without verifying the signature.

    Args:
        signature: Detached compact JWS to inspect.

    Returns:
        Protected JOSE header object.

    Raises:
        DetachedJwsVerificationError: If the compact JWS or protected header is malformed.
    """
    parts = signature.split(".")
    if len(parts) != 3 or parts[0] == "" or parts[2] == "":
        raise DetachedJwsVerificationError("response signature must be a compact detached JWS")
    if parts[1] != "":
        raise DetachedJwsVerificationError("response signature must use detached compact JWS serialization")
    try:
        header = json_b64decode(parts[0].encode("ascii"))
    except (JoseError, ValueError, TypeError) as error:
        raise DetachedJwsVerificationError("response signature protected header is malformed") from error
    if not isinstance(header, dict):
        raise DetachedJwsVerificationError("response signature protected header must be a JSON object")
    return dict(header)


def _public_jwk_from_jwks(jwks: JsonValue, *, key_id: str) -> RSAKey:
    """Select and import one RSA public key from a JWKS by ``kid``.

    Args:
        jwks: JWKS JSON object containing a ``keys`` array.
        key_id: JOSE key identifier from the protected JWS header.

    Returns:
        Imported joserfc key object suitable for verification.

    Raises:
        DetachedJwsVerificationError: If no matching RSA public key is available.
    """
    if not isinstance(jwks, dict):
        raise DetachedJwsVerificationError("JWKS response must be a JSON object")
    raw_keys = jwks.get("keys")
    if not isinstance(raw_keys, list):
        raise DetachedJwsVerificationError("JWKS response must contain a keys array")
    for raw_key in raw_keys:
        if not isinstance(raw_key, dict) or raw_key.get("kid") != key_id:
            continue
        key_data: dict[str, str | list[str]] = {}
        for member_name, member_value in raw_key.items():
            if isinstance(member_value, str):
                key_data[member_name] = member_value
                continue
            if isinstance(member_value, list):
                string_values = [item for item in member_value if isinstance(item, str)]
                if len(string_values) == len(member_value):
                    key_data[member_name] = string_values
        try:
            return jwk.import_key(key_data, key_type="RSA")
        except (InvalidKeyTypeError, JoseError, TypeError, ValueError) as error:
            raise DetachedJwsVerificationError(f"JWKS key {key_id!r} is not a usable RSA public key") from error
    raise DetachedJwsVerificationError(f"JWKS response does not contain key id {key_id!r}")


def _attach_detached_payload(signature: str, payload: bytes) -> str:
    """Reattach payload bytes to a compact detached JWS for verification.

    Args:
        signature: Detached compact JWS with an empty payload segment.
        payload: Exact payload bytes covered by the signature.

    Returns:
        Compact JWS with the payload segment restored.

    Raises:
        DetachedJwsVerificationError: If the signature shape is invalid.
    """
    parts = signature.split(".")
    if len(parts) != 3:
        raise DetachedJwsVerificationError("response signature must be a compact JWS")
    return ".".join((parts[0], urlsafe_b64encode(payload).decode("ascii"), parts[2]))


def _sign_ps256_detached_jws(
    private_key_pem: bytes,
    *,
    key_id: str,
    payload: bytes,
    signature_issuer: str | None = None,
    signature_trust_anchor: str | None = None,
) -> str:
    """Sign one detached PS256 JWS over exact request-body bytes.

    When both ``signature_issuer`` and ``signature_trust_anchor`` are supplied,
    the Open Banking mandatory JOSE protected-header claims are included and
    added to ``crit`` as required by the OB Read/Write Data API Specification:

    - ``http://openbanking.org.uk/iat`` — current UTC timestamp in epoch seconds.
    - ``http://openbanking.org.uk/iss`` — the supplied ``signature_issuer``.
    - ``http://openbanking.org.uk/tan`` — the supplied ``signature_trust_anchor``.

    The payload is base64url-encoded before signing, matching Read/Write
    v3.1.4 and newer detached-JWS behaviour. The serialized compact JWS still
    has its payload segment detached before transport.

    Args:
        private_key_pem: PEM-encoded RSA private key bytes.
        key_id: JOSE ``kid`` header value to emit.
        payload: Exact bytes that must be covered by the detached signature.
        signature_issuer: Optional Open Banking ``iss`` header claim. Must be
            supplied together with ``signature_trust_anchor``.
        signature_trust_anchor: Optional Open Banking ``tan`` header claim.
            Must be supplied together with ``signature_issuer``.

    Returns:
        Detached compact JWS with an empty payload segment.

    Raises:
        JwtSigningError: If the PEM bytes cannot be imported or the JOSE
            library rejects the signing request.
    """
    try:
        signing_key = jwk.import_key(private_key_pem, key_type="RSA")
        protected_header: dict[str, object] = {
            "alg": "PS256",
            "kid": key_id,
            "typ": "JOSE",
            "cty": "application/json",
        }
        jws_registry: JWSRegistry | None = None
        if signature_issuer is not None and signature_trust_anchor is not None:
            ob_iat = int(datetime.now(UTC).timestamp())
            protected_header["http://openbanking.org.uk/iat"] = ob_iat
            protected_header["http://openbanking.org.uk/iss"] = signature_issuer
            protected_header["http://openbanking.org.uk/tan"] = signature_trust_anchor
            protected_header["crit"] = [
                "http://openbanking.org.uk/iat",
                "http://openbanking.org.uk/iss",
                "http://openbanking.org.uk/tan",
            ]
            jws_registry = _OB_JWS_HEADER_REGISTRY
        compact_jws = jws.serialize_compact(
            protected_header,
            payload,
            signing_key,
            algorithms=["PS256"],
            registry=jws_registry,
        )
        return jws.detach_content(compact_jws)
    except (InvalidKeyTypeError, JoseError, TypeError, ValueError) as error:
        raise JwtSigningError("Unable to sign PS256 detached JWS with configured FAPI signing key") from error


def _sign_ps256_compact_jws_no_b64(
    private_key_pem: bytes,
    *,
    key_id: str,
    payload: bytes,
) -> str:
    """Sign one compact PS256 JWS without the Open Banking ``b64``/``crit`` claims.

    Intentionally produces a compact (attached, not detached) JWS whose protected
    header contains only ``alg`` and ``kid``.  The ``b64`` and ``crit`` JOSE header
    claims required by the Open Banking detached-JWS specification (RFC 7797 §3) are
    deliberately omitted so that the resulting token is structurally incompatible with
    OB requirements.  Servers should reject the token with a 400 and an OB
    ``UK.OBIE.Signature.Missing`` or ``UK.OBIE.Signature.Malformed`` error code.

    Args:
        private_key_pem: PEM-encoded RSA private key bytes.
        key_id: JOSE ``kid`` header value to emit.
        payload: JSON bytes to embed as the JWS payload.

    Returns:
        Compact JWS string (``header.payload.signature``) without ``b64``/``crit``
        JOSE header claims.

    Raises:
        JwtSigningError: If the PEM bytes cannot be imported or the JOSE
            library rejects the signing request.
    """
    try:
        signing_key = jwk.import_key(private_key_pem, key_type="RSA")
        protected_header: dict[str, object] = {
            "alg": "PS256",
            "kid": key_id,
        }
        return jws.serialize_compact(
            protected_header,
            payload,
            signing_key,
            algorithms=["PS256"],
        )
    except (InvalidKeyTypeError, JoseError, TypeError, ValueError) as error:
        raise JwtSigningError(
            "Unable to sign PS256 compact JWS (omit-b64-claim) with configured FAPI signing key"
        ) from error
