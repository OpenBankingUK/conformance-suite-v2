"""DCR registration JWT builder for Open Banking UK Dynamic Client Registration.

Builds ``application/jose`` registration JWTs per the OBIE DCR specification.
The registration JWT is a compact JWT signed with the participant's FAPI
signing key (PS256), carrying the Software Statement Assertion (SSA) in the
``software_statement`` claim.

This module also provides helpers for:

- Parsing SSA claims (without signature verification — the ASPSP verifies
  the SSA against OB Directory's JWKS).
- Deriving a stable ``kid`` from the signing certificate for use in JWT
  protected headers.
- Building fake expired SSAs for DCR-005 negative-test scenarios.

References:
- OBIE DCR Specification v3.2 / v3.3 / v3.4
- RFC 7591 — OAuth 2.0 Dynamic Client Registration Protocol
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from joserfc import jwk, jwt
from joserfc.errors import InvalidKeyTypeError, JoseError

from conformance.dcr.credentials import DcrCredentials
from conformance.json_types import JsonValue

logger = logging.getLogger(__name__)

_REGISTRATION_JWT_LIFETIME = timedelta(minutes=5)
"""Lifetime for DCR registration JWTs before the ``exp`` claim fires."""

_FAKE_SSA_LIFETIME = timedelta(seconds=-1)
"""Negative lifetime ensures the fake SSA exp is already in the past."""

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class DcrRegistrationError(ValueError):
    """Raised when a DCR registration JWT cannot be built or when SSA parsing fails.

    Wraps :class:`ValueError` so callers can catch either the specific error
    or the generic base class.
    """


# ---------------------------------------------------------------------------
# Input types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DcrRegistrationJwtInput:
    """Values required to build a DCR registration JWT.

    Attributes:
        issuer: ``iss`` claim — the ``software_id`` extracted from the SSA.
        audience: ``aud`` claim — the ASPSP issuer URI from OIDC discovery.
        redirect_uris: List of redirect URIs from the SSA.
        token_endpoint_auth_method: Client auth method for the token endpoint.
        grant_types: OAuth grant types to request.
        response_types: OAuth response types to request.
        software_statement: The raw SSA JWT string.
        request_object_signing_alg: ``request_object_signing_alg`` claim,
            defaults to ``"PS256"``.
    """

    issuer: str
    audience: str
    redirect_uris: list[str]
    token_endpoint_auth_method: str
    grant_types: list[str]
    response_types: list[str]
    software_statement: str
    request_object_signing_alg: str = field(default="PS256")


# ---------------------------------------------------------------------------
# Public functions
# ---------------------------------------------------------------------------


def build_registration_jwt(
    jwt_input: DcrRegistrationJwtInput,
    credentials: DcrCredentials,
) -> str:
    """Build a signed DCR registration JWT (``application/jose``).

    The JWT carries all required DCR claims and is signed with the
    participant's FAPI signing private key using PS256.

    Args:
        jwt_input: Resolved claim values for the registration JWT.
        credentials: Runtime credential material providing the signing key.

    Returns:
        Compact serialised JWT string suitable for use as the
        ``application/jose`` request body of POST /register.

    Raises:
        DcrRegistrationError: If the signing private key cannot be imported
            or the JOSE library rejects the signing request.
    """
    kid = derive_kid(credentials.signing_certificate_pem)
    now = datetime.now(UTC)
    exp = now + _REGISTRATION_JWT_LIFETIME

    claims: dict[str, object] = {
        "iss": jwt_input.issuer,
        "iat": int(now.timestamp()),
        "exp": int(exp.timestamp()),
        "aud": jwt_input.audience,
        "jti": uuid4().hex,
        "redirect_uris": jwt_input.redirect_uris,
        "token_endpoint_auth_method": jwt_input.token_endpoint_auth_method,
        "grant_types": jwt_input.grant_types,
        "response_types": jwt_input.response_types,
        "request_object_signing_alg": jwt_input.request_object_signing_alg,
        "software_statement": jwt_input.software_statement,
    }

    return _sign_ps256_jwt(
        credentials.signing_private_key_pem,
        kid=kid,
        claims=claims,
    )


def build_negative_registration_jwt_wrong_issuer(
    jwt_input: DcrRegistrationJwtInput,
    credentials: DcrCredentials,
) -> str:
    """Build a registration JWT with a deliberately wrong ``iss`` claim (DCR-007).

    The ``iss`` is replaced with a sentinel value that will not match any
    known software statement, causing a compliant ASPSP to return 4xx.

    Args:
        jwt_input: Base claim values; ``issuer`` will be overridden.
        credentials: Runtime credential material for signing.

    Returns:
        Compact serialised JWT with an invalid ``iss`` value.

    Raises:
        DcrRegistrationError: If the signing private key cannot be imported.
    """
    invalid_input = DcrRegistrationJwtInput(
        issuer="invalid-issuer-dcr-test-ob-conformance",
        audience=jwt_input.audience,
        redirect_uris=jwt_input.redirect_uris,
        token_endpoint_auth_method=jwt_input.token_endpoint_auth_method,
        grant_types=jwt_input.grant_types,
        response_types=jwt_input.response_types,
        software_statement=jwt_input.software_statement,
        request_object_signing_alg=jwt_input.request_object_signing_alg,
    )
    return build_registration_jwt(invalid_input, credentials)


def build_negative_registration_jwt_invalid_auth_method(
    jwt_input: DcrRegistrationJwtInput,
    credentials: DcrCredentials,
) -> str:
    """Build a registration JWT with a non-FAPI token-endpoint auth method (DCR-008).

    Sets ``token_endpoint_auth_method`` to ``"client_secret_post"`` which is
    not compatible with FAPI 1 Advanced and should be rejected by a compliant
    ASPSP.

    Args:
        jwt_input: Base claim values; auth method will be overridden.
        credentials: Runtime credential material for signing.

    Returns:
        Compact serialised JWT with ``token_endpoint_auth_method="client_secret_post"``.

    Raises:
        DcrRegistrationError: If the signing private key cannot be imported.
    """
    invalid_input = DcrRegistrationJwtInput(
        issuer=jwt_input.issuer,
        audience=jwt_input.audience,
        redirect_uris=jwt_input.redirect_uris,
        token_endpoint_auth_method="client_secret_post",  # noqa: S106 — intentional non-FAPI value for negative DCR-008 test
        grant_types=jwt_input.grant_types,
        response_types=jwt_input.response_types,
        software_statement=jwt_input.software_statement,
        request_object_signing_alg=jwt_input.request_object_signing_alg,
    )
    return build_registration_jwt(invalid_input, credentials)


def build_negative_registration_jwt_wrong_response_type(
    jwt_input: DcrRegistrationJwtInput,
    credentials: DcrCredentials,
) -> str:
    """Build a registration JWT with a wrong ``response_types`` claim (DCR-009).

    Requests ``["token", "id_token"]`` instead of ``["code"]``.  The absence
    of ``"code"`` violates the DCR specification requirement for authorisation
    code flow and should be rejected by a compliant ASPSP.

    Args:
        jwt_input: Base claim values; ``response_types`` will be overridden.
        credentials: Runtime credential material for signing.

    Returns:
        Compact serialised JWT with ``response_types=["token", "id_token"]``.

    Raises:
        DcrRegistrationError: If the signing private key cannot be imported.
    """
    invalid_input = DcrRegistrationJwtInput(
        issuer=jwt_input.issuer,
        audience=jwt_input.audience,
        redirect_uris=jwt_input.redirect_uris,
        token_endpoint_auth_method=jwt_input.token_endpoint_auth_method,
        grant_types=jwt_input.grant_types,
        response_types=["token", "id_token"],
        software_statement=jwt_input.software_statement,
        request_object_signing_alg=jwt_input.request_object_signing_alg,
    )
    return build_registration_jwt(invalid_input, credentials)


def build_negative_registration_jwt_expired_ssa(
    jwt_input: DcrRegistrationJwtInput,
    credentials: DcrCredentials,
    *,
    software_id: str,
) -> str:
    """Build a registration JWT containing a fake expired SSA (DCR-005).

    Constructs a minimal self-signed SSA JWT with ``exp`` in the past and
    includes it as the ``software_statement`` in the registration JWT.  A
    compliant ASPSP must reject this with a 4xx error — either because the
    SSA is expired or because its signature does not verify against OB
    Directory's JWKS.

    Args:
        jwt_input: Base claim values; ``software_statement`` will be replaced.
        credentials: Runtime credential material for signing.
        software_id: The ``software_id`` value to embed in the fake SSA.

    Returns:
        Compact serialised JWT with a fake expired SSA.

    Raises:
        DcrRegistrationError: If the signing private key cannot be imported.
    """
    expired_ssa = _build_fake_expired_ssa(
        credentials=credentials,
        software_id=software_id,
        redirect_uris=jwt_input.redirect_uris,
    )
    expired_input = DcrRegistrationJwtInput(
        issuer=jwt_input.issuer,
        audience=jwt_input.audience,
        redirect_uris=jwt_input.redirect_uris,
        token_endpoint_auth_method=jwt_input.token_endpoint_auth_method,
        grant_types=jwt_input.grant_types,
        response_types=jwt_input.response_types,
        software_statement=expired_ssa,
        request_object_signing_alg=jwt_input.request_object_signing_alg,
    )
    return build_registration_jwt(expired_input, credentials)


def parse_ssa_claims(ssa_bytes: bytes) -> dict[str, JsonValue]:
    """Decode SSA JWT claims without verifying the signature.

    The SSA is a JWT issued by OB Directory.  This function reads the payload
    claims to extract ``software_id`` and ``software_redirect_uris`` for use
    in registration JWT construction.  Signature verification is intentionally
    skipped here — the ASPSP verifies the SSA against OB Directory's JWKS.

    Args:
        ssa_bytes: Raw SSA JWT bytes (UTF-8 or ASCII compact JWT).

    Returns:
        Dict of JWT payload claims decoded from the SSA.

    Raises:
        DcrRegistrationError: If the SSA cannot be decoded as a compact JWT.
    """
    try:
        token_str = ssa_bytes.decode("ascii").strip()
        parts = token_str.split(".")
        if len(parts) != 3:  # noqa: PLR2004
            raise DcrRegistrationError("SSA does not appear to be a compact JWT (expected 3 parts)")
        import base64

        padding = 4 - len(parts[1]) % 4
        padded = parts[1] + ("=" * (padding % 4))
        payload_json = base64.urlsafe_b64decode(padded)
        import json

        claims = json.loads(payload_json)
        if not isinstance(claims, dict):
            raise DcrRegistrationError("SSA JWT payload must be a JSON object")
        return {k: v for k, v in claims.items() if isinstance(k, str)}
    except (UnicodeDecodeError, ValueError) as exc:
        raise DcrRegistrationError(f"Failed to decode SSA JWT claims: {exc}") from exc


def derive_kid(signing_certificate_pem: bytes) -> str:
    """Derive a stable JWK key identifier from a signing certificate.

    Uses the first 16 hexadecimal characters of the SHA-256 digest of the
    certificate's PEM bytes as the ``kid``.  This provides a deterministic,
    consistent identifier without requiring X.509 parsing.

    Args:
        signing_certificate_pem: PEM-encoded signing certificate bytes.

    Returns:
        A 16-character lowercase hexadecimal string suitable for use as the
        JWT ``kid`` header value.
    """
    return hashlib.sha256(signing_certificate_pem).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _build_fake_expired_ssa(
    credentials: DcrCredentials,
    *,
    software_id: str,
    redirect_uris: list[str],
) -> str:
    """Build a minimal self-signed SSA JWT with ``exp`` in the past.

    This fake SSA is used only in DCR-005 negative test scenarios.  The JWT is
    signed with the participant's signing key (not OB Directory's key), so a
    strict ASPSP will reject it for signature verification failure; a lenient
    one may reject it for expiry.  Either way, a 4xx response is expected.

    Args:
        credentials: Runtime credential material providing the signing key.
        software_id: The ``software_id`` claim to embed in the fake SSA.
        redirect_uris: The ``software_redirect_uris`` claim to embed.

    Returns:
        Compact serialised fake SSA JWT.

    Raises:
        DcrRegistrationError: If the signing private key cannot be imported.
    """
    kid = derive_kid(credentials.signing_certificate_pem)
    now = datetime.now(UTC)
    # exp is 1 second after epoch — always in the past for any real run.
    claims: dict[str, object] = {
        "iss": "ob-test-directory",
        "sub": software_id,
        "software_id": software_id,
        "software_redirect_uris": redirect_uris,
        "iat": 1,
        "exp": 1,
        "nbf": 1,
        "jti": uuid4().hex,
        "org_id": "test-org",
    }
    # Suppress the unused variable warning — now is used only for documentation.
    _ = now
    return _sign_ps256_jwt(credentials.signing_private_key_pem, kid=kid, claims=claims)


def _sign_ps256_jwt(
    private_key_pem: bytes,
    *,
    kid: str,
    claims: dict[str, object],
) -> str:
    """Sign a PS256 compact JWT with the given RSA private key.

    Follows the same pattern as :func:`conformance.signing_service._sign_ps256_jwt`
    to keep JWT signing behaviour consistent across all plugin types.

    Args:
        private_key_pem: PEM-encoded RSA private key bytes.
        kid: JOSE ``kid`` header value.
        claims: JWT payload claims to serialise and sign.

    Returns:
        Compact serialised JWT string.

    Raises:
        DcrRegistrationError: If the key cannot be imported or JOSE signing fails.
    """
    try:
        signing_key = jwk.import_key(private_key_pem, key_type="RSA")
        return jwt.encode(
            {"alg": "PS256", "kid": kid, "typ": "JWT"},
            claims,
            signing_key,
            algorithms=["PS256"],
        )
    except (InvalidKeyTypeError, JoseError, TypeError, ValueError) as exc:
        raise DcrRegistrationError("Unable to sign DCR registration JWT with configured signing key") from exc
