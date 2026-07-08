"""Token-endpoint grant helpers for DCR conformance scenarios.

Implements the two FAPI 1 Advanced-compatible client authentication methods
for the client credentials grant:

- ``tls_client_auth`` — presents the mTLS client certificate, sends
  ``client_id`` in the request body.
- ``private_key_jwt`` — builds and signs a client assertion JWT (PS256),
  sends it as ``client_assertion`` in the request body.

Both methods POST to the token endpoint with ``grant_type=client_credentials``
and return a :class:`~conformance.plugins.dcr.client_state.DcrTokenResponse`
with the ``access_token`` masked.

References:
- RFC 8705 — OAuth 2.0 Mutual-TLS Client Authentication
- RFC 7523 — JSON Web Token Profile for OAuth 2.0 Client Authentication
- FAPI 1.0 Advanced Security Profile
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx

from conformance.dcr.credentials import DcrCredentials
from conformance.dcr.transport import DcrTokenEndpointAuthMethod
from conformance.http import JsonHttpClientError, send_json
from conformance.json_types import JsonObject
from conformance.plugins.dcr.client_state import DcrClientState, DcrTokenResponse, parse_token_response
from conformance.plugins.dcr.registration import DcrRegistrationError, _sign_ps256_jwt, derive_kid

logger = logging.getLogger(__name__)

_CLIENT_ASSERTION_LIFETIME = timedelta(minutes=5)
"""Lifetime for private_key_jwt client assertion JWTs."""

# ---------------------------------------------------------------------------
# Exception
# ---------------------------------------------------------------------------


class DcrTokenError(RuntimeError):
    """Raised when a token-endpoint request fails or the response is invalid.

    Attributes:
        status_code: HTTP status code from the token endpoint, or ``None``
            when no response was received (e.g. connection failure).
    """

    status_code: int | None

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        """Initialise with a message and optional HTTP status code.

        Args:
            message: Human-readable failure description.
            status_code: HTTP status code from the token endpoint when available.
        """
        super().__init__(message)
        self.status_code = status_code


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def request_token(
    client: httpx.Client,
    token_endpoint: str,
    client_state: DcrClientState,
    credentials: DcrCredentials,
    auth_method: DcrTokenEndpointAuthMethod,
    *,
    scope: str = "openid",
) -> DcrTokenResponse:
    """Request a client credentials access token using the configured auth method.

    Dispatches to :func:`_tls_client_auth_grant` or
    :func:`_private_key_jwt_grant` based on ``auth_method``.

    Args:
        client: Preconfigured mTLS HTTP client.
        token_endpoint: Full URL of the ASPSP token endpoint.
        client_state: Registered client state containing ``client_id``.
        credentials: Runtime credential material for ``private_key_jwt`` signing.
        auth_method: Selected FAPI-compatible authentication method.
        scope: OAuth scope to request (defaults to ``"openid"``).

    Returns:
        A :class:`~conformance.plugins.dcr.client_state.DcrTokenResponse`
        with the access token masked.

    Raises:
        DcrTokenError: If the token request fails or the response cannot be
            parsed.
    """
    if auth_method == "tls_client_auth":
        return _tls_client_auth_grant(client, token_endpoint, client_state, scope=scope)
    return _private_key_jwt_grant(client, token_endpoint, client_state, credentials, scope=scope)


def request_token_wrong_client_id(
    client: httpx.Client,
    token_endpoint: str,
    *,
    fake_client_id: str,
    scope: str = "openid",
) -> tuple[int, JsonObject]:
    """Attempt a token request with a wrong/random client ID (DCR-011).

    Sends a ``client_credentials`` grant with a fake ``client_id``.  The
    caller should assert the ASPSP returns 4xx.

    Args:
        client: Preconfigured mTLS HTTP client.
        token_endpoint: Full URL of the ASPSP token endpoint.
        fake_client_id: A random client ID that is not registered.
        scope: OAuth scope to request.

    Returns:
        Tuple of ``(status_code, masked_response_body)``.

    Raises:
        DcrTokenError: If a network error prevents any response.
    """
    form: dict[str, str] = {
        "grant_type": "client_credentials",
        "client_id": fake_client_id,
        "scope": scope,
    }
    return _send_token_form(client, token_endpoint, form)


# ---------------------------------------------------------------------------
# Private grant implementations
# ---------------------------------------------------------------------------


def _tls_client_auth_grant(
    client: httpx.Client,
    token_endpoint: str,
    client_state: DcrClientState,
    *,
    scope: str,
) -> DcrTokenResponse:
    """Execute a client credentials grant using mutual-TLS client authentication.

    Per RFC 8705, the ``client_id`` is sent in the form body.  The mTLS
    client certificate presented by ``client`` acts as the client credential.

    Args:
        client: Preconfigured mTLS HTTP client carrying the client certificate.
        token_endpoint: Token endpoint URL.
        client_state: Registered client state supplying ``client_id``.
        scope: OAuth scope to request.

    Returns:
        A masked :class:`~conformance.plugins.dcr.client_state.DcrTokenResponse`.

    Raises:
        DcrTokenError: If the request fails or the response cannot be parsed.
    """
    form: dict[str, str] = {
        "grant_type": "client_credentials",
        "client_id": client_state.client_id,
        "scope": scope,
    }
    status_code, body = _send_token_form(client, token_endpoint, form)
    return _parse_successful_token_response(body, status_code, token_endpoint)


def _private_key_jwt_grant(
    client: httpx.Client,
    token_endpoint: str,
    client_state: DcrClientState,
    credentials: DcrCredentials,
    *,
    scope: str,
) -> DcrTokenResponse:
    """Execute a client credentials grant using a private_key_jwt client assertion.

    Per RFC 7523, builds a signed PS256 JWT assertion and sends it as the
    ``client_assertion`` in the request body.

    Args:
        client: Preconfigured mTLS HTTP client.
        token_endpoint: Token endpoint URL.
        client_state: Registered client state supplying ``client_id``.
        credentials: Runtime credential material for building the JWT assertion.
        scope: OAuth scope to request.

    Returns:
        A masked :class:`~conformance.plugins.dcr.client_state.DcrTokenResponse`.

    Raises:
        DcrTokenError: If the assertion JWT cannot be signed or the request fails.
    """
    try:
        assertion = _build_client_assertion(
            token_endpoint=token_endpoint,
            client_id=client_state.client_id,
            credentials=credentials,
        )
    except DcrRegistrationError as exc:
        raise DcrTokenError(f"Failed to build private_key_jwt assertion: {exc}") from exc

    form: dict[str, str] = {
        "grant_type": "client_credentials",
        "client_id": client_state.client_id,
        "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
        "client_assertion": assertion,
        "scope": scope,
    }
    status_code, body = _send_token_form(client, token_endpoint, form)
    return _parse_successful_token_response(body, status_code, token_endpoint)


def _build_client_assertion(
    *,
    token_endpoint: str,
    client_id: str,
    credentials: DcrCredentials,
) -> str:
    """Build a PS256-signed private_key_jwt client assertion JWT.

    Per RFC 7523 §3, the assertion is a JWT with ``iss=sub=client_id``,
    ``aud=token_endpoint``, ``jti``, ``iat``, and ``exp``.

    Args:
        token_endpoint: Token endpoint URI used as the JWT ``aud`` claim.
        client_id: OAuth ``client_id`` used as both ``iss`` and ``sub``.
        credentials: Runtime credential material providing the signing key.

    Returns:
        Compact serialised PS256 JWT assertion string.

    Raises:
        DcrRegistrationError: If the signing private key cannot be imported.
    """
    kid = derive_kid(credentials.signing_certificate_pem)
    now = datetime.now(UTC)
    exp = now + _CLIENT_ASSERTION_LIFETIME
    claims: dict[str, object] = {
        "iss": client_id,
        "sub": client_id,
        "aud": token_endpoint,
        "jti": uuid4().hex,
        "iat": int(now.timestamp()),
        "exp": int(exp.timestamp()),
    }
    return _sign_ps256_jwt(credentials.signing_private_key_pem, kid=kid, claims=claims)


def _send_token_form(
    client: httpx.Client,
    token_endpoint: str,
    form: dict[str, str],
) -> tuple[int, JsonObject]:
    """POST a form body to the token endpoint and return status + body.

    Handles both successful (2xx) and error (4xx/5xx) JSON responses.
    :class:`~conformance.http.JsonHttpClientError` is re-raised as
    :class:`DcrTokenError` when no HTTP response was received.

    Args:
        client: Preconfigured mTLS HTTP client.
        token_endpoint: Full token endpoint URL.
        form: Form fields to send as ``application/x-www-form-urlencoded``.

    Returns:
        Tuple of ``(status_code, response_body_dict)``.

    Raises:
        DcrTokenError: If a network error prevents any response.
    """
    try:
        response = send_json(client, "POST", token_endpoint, form_body=form)
        return response.status_code, dict(response.body)
    except JsonHttpClientError as exc:
        if exc.status_code is not None:
            # Got a response but it wasn't JSON — treat as an empty body.
            logger.debug("Token endpoint returned non-JSON %s body", exc.status_code)
            return exc.status_code, {}
        raise DcrTokenError(f"Token endpoint request to {token_endpoint} failed: {exc}") from exc


def _parse_successful_token_response(
    body: JsonObject,
    status_code: int,
    token_endpoint: str,
) -> DcrTokenResponse:
    """Parse a successful (2xx) token response into a :class:`DcrTokenResponse`.

    Args:
        body: Parsed JSON response body.
        status_code: HTTP status code from the token endpoint.
        token_endpoint: URL used in error messages.

    Returns:
        A masked :class:`~conformance.plugins.dcr.client_state.DcrTokenResponse`.

    Raises:
        DcrTokenError: If the response is not 2xx or ``token_type`` is missing.
    """
    if status_code < 200 or status_code >= 300:  # noqa: PLR2004
        error_code = body.get("error", "unknown")
        raise DcrTokenError(
            f"Token endpoint {token_endpoint} returned {status_code}: {error_code}",
            status_code=status_code,
        )
    try:
        return parse_token_response(body)
    except ValueError as exc:
        raise DcrTokenError(
            f"Token endpoint {token_endpoint} returned an invalid token response: {exc}",
            status_code=status_code,
        ) from exc
