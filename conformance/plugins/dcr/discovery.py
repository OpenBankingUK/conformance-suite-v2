"""OIDC discovery fetch and validation for DCR conformance scenarios.

Fetches the ASPSP's OIDC discovery document from
``<issuer>/.well-known/openid-configuration`` and validates that the required
fields for DCR and FAPI 1 Advanced are present.

FAPI 1 Advanced requires one of ``tls_client_auth`` or ``private_key_jwt``
in ``token_endpoint_auth_methods_supported``.  This module selects the
preferred method (``tls_client_auth`` > ``private_key_jwt``) and raises
:class:`DcrDiscoveryError` if neither is supported.
"""

from __future__ import annotations

import logging

import httpx

from conformance.dcr.transport import DcrTokenEndpointAuthMethod
from conformance.http import JsonHttpClientError, send_json
from conformance.json_types import JsonObject, JsonValue

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Required discovery fields per DCR + FAPI 1 Advanced
# ---------------------------------------------------------------------------

_REQUIRED_DISCOVERY_FIELDS: tuple[str, ...] = (
    "issuer",
    "registration_endpoint",
    "token_endpoint",
    "jwks_uri",
)
"""Minimum set of discovery fields required by the DCR plugin.

These fields are mandated by OpenID Connect Discovery 1.0 and are required
for DCR registration (``registration_endpoint``), token grants
(``token_endpoint``), and future signature verification (``jwks_uri``).
"""

_FAPI_REQUIRED_AUTH_METHODS: frozenset[str] = frozenset(
    {
        "tls_client_auth",
        "private_key_jwt",
    }
)
"""FAPI 1 Advanced-compatible token-endpoint client authentication methods.

The ASPSP must advertise at least one of these.  Other methods (e.g.
``client_secret_post``, ``none``) are not compatible with FAPI 1 Advanced
and are rejected.
"""

# ---------------------------------------------------------------------------
# Exception
# ---------------------------------------------------------------------------


class DcrDiscoveryError(RuntimeError):
    """Raised when OIDC discovery fetch or validation fails.

    Wraps :class:`RuntimeError` so callers can catch either the specific error
    or the generic base class without losing context.
    """


# ---------------------------------------------------------------------------
# Discovery result
# ---------------------------------------------------------------------------


class DcrDiscoveryResult:
    """Validated OIDC discovery document for DCR scenario execution.

    Attributes:
        issuer: Canonical issuer identifier from the discovery document.
        registration_endpoint: URL for POST /register (and GET/PUT/DELETE).
        token_endpoint: URL for the token endpoint.
        jwks_uri: URL of the ASPSP's public JWKS.
        token_endpoint_auth_methods_supported: List of advertised
            token-endpoint auth methods.
        selected_auth_method: FAPI-compatible auth method chosen for this
            run (``"tls_client_auth"`` or ``"private_key_jwt"``).
        response_types_supported: List of response types advertised by the
            ASPSP.  Defaults to ``["code"]`` when absent.
        grant_types_supported: List of grant types advertised by the ASPSP.
            Defaults to ``["authorization_code"]`` when absent.
        raw: Full raw discovery document for additional field access.
    """

    issuer: str
    registration_endpoint: str
    token_endpoint: str
    jwks_uri: str
    token_endpoint_auth_methods_supported: list[str]
    selected_auth_method: DcrTokenEndpointAuthMethod
    response_types_supported: list[str]
    grant_types_supported: list[str]
    raw: JsonObject

    def __init__(
        self,
        *,
        issuer: str,
        registration_endpoint: str,
        token_endpoint: str,
        jwks_uri: str,
        token_endpoint_auth_methods_supported: list[str],
        selected_auth_method: DcrTokenEndpointAuthMethod,
        response_types_supported: list[str],
        grant_types_supported: list[str],
        raw: JsonObject,
    ) -> None:
        """Initialise a validated DCR discovery result.

        Args:
            issuer: Canonical issuer identifier.
            registration_endpoint: Registration endpoint URL.
            token_endpoint: Token endpoint URL.
            jwks_uri: JWKS URI.
            token_endpoint_auth_methods_supported: Advertised auth methods.
            selected_auth_method: Chosen FAPI-compatible auth method.
            response_types_supported: Advertised response types.
            grant_types_supported: Advertised grant types.
            raw: Full raw discovery document.
        """
        self.issuer = issuer
        self.registration_endpoint = registration_endpoint
        self.token_endpoint = token_endpoint
        self.jwks_uri = jwks_uri
        self.token_endpoint_auth_methods_supported = token_endpoint_auth_methods_supported
        self.selected_auth_method = selected_auth_method
        self.response_types_supported = response_types_supported
        self.grant_types_supported = grant_types_supported
        self.raw = raw


# ---------------------------------------------------------------------------
# Public fetch function
# ---------------------------------------------------------------------------


def fetch_discovery(client: httpx.Client, issuer_url: str) -> DcrDiscoveryResult:
    """Fetch and validate the OIDC discovery document for a given issuer.

    Constructs the discovery URL as ``<issuer>/.well-known/openid-configuration``
    (trailing slashes on ``issuer_url`` are stripped before appending the
    path), fetches the document, and validates that required fields are
    present and FAPI-compatible auth methods are advertised.

    Args:
        client: Preconfigured mTLS HTTP client.
        issuer_url: The ASPSP's issuer URL (e.g. ``"https://as.example.com"``).

    Returns:
        A validated :class:`DcrDiscoveryResult`.

    Raises:
        DcrDiscoveryError: If the discovery URL cannot be fetched, the
            response is not a JSON object, required fields are missing, or
            no FAPI-compatible auth method is advertised.
    """
    discovery_url = issuer_url.rstrip("/") + "/.well-known/openid-configuration"
    logger.debug("DCR discovery: fetching %s", discovery_url)

    try:
        response = send_json(client, "GET", discovery_url)
    except JsonHttpClientError as exc:
        raise DcrDiscoveryError(f"Failed to fetch OIDC discovery from {discovery_url}: {exc}") from exc

    doc = response.body
    _validate_required_fields(doc, discovery_url)

    issuer = str(doc["issuer"])
    registration_endpoint = str(doc["registration_endpoint"])
    token_endpoint = str(doc["token_endpoint"])
    jwks_uri = str(doc["jwks_uri"])

    auth_methods = _extract_string_list(doc, "token_endpoint_auth_methods_supported")
    selected_auth_method = _select_auth_method(auth_methods, discovery_url=discovery_url)

    response_types = _extract_string_list(doc, "response_types_supported") or ["code"]
    grant_types = _extract_string_list(doc, "grant_types_supported") or ["authorization_code"]

    logger.info(
        "DCR discovery: registration_endpoint=%s selected_auth_method=%s",
        registration_endpoint,
        selected_auth_method,
    )

    return DcrDiscoveryResult(
        issuer=issuer,
        registration_endpoint=registration_endpoint,
        token_endpoint=token_endpoint,
        jwks_uri=jwks_uri,
        token_endpoint_auth_methods_supported=auth_methods,
        selected_auth_method=selected_auth_method,
        response_types_supported=response_types,
        grant_types_supported=grant_types,
        raw=dict(doc),
    )


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _validate_required_fields(doc: JsonObject, discovery_url: str) -> None:
    """Assert that required OIDC discovery fields are present.

    Args:
        doc: Parsed discovery JSON object.
        discovery_url: Discovery URL used in error messages.

    Raises:
        DcrDiscoveryError: If any required field is absent from ``doc``.
    """
    missing = [f for f in _REQUIRED_DISCOVERY_FIELDS if f not in doc or not doc[f]]
    if missing:
        raise DcrDiscoveryError(f"OIDC discovery at {discovery_url} is missing required field(s): {', '.join(missing)}")


def _select_auth_method(
    methods: list[str],
    *,
    discovery_url: str,
) -> DcrTokenEndpointAuthMethod:
    """Select the preferred FAPI-compatible token-endpoint auth method.

    Prefers ``tls_client_auth`` when advertised; falls back to
    ``private_key_jwt``.  Raises if neither is supported, as FAPI 1 Advanced
    requires one of these two methods.

    Args:
        methods: List of auth methods from ``token_endpoint_auth_methods_supported``.
        discovery_url: Discovery URL used in error messages.

    Returns:
        The selected :data:`~conformance.dcr.transport.DcrTokenEndpointAuthMethod`.

    Raises:
        DcrDiscoveryError: If neither ``tls_client_auth`` nor
            ``private_key_jwt`` is in ``methods``.
    """
    if "tls_client_auth" in methods:
        return "tls_client_auth"
    if "private_key_jwt" in methods:
        return "private_key_jwt"
    raise DcrDiscoveryError(
        f"OIDC discovery at {discovery_url} does not advertise any FAPI 1 "
        f"Advanced-compatible token-endpoint auth method "
        f"(tls_client_auth or private_key_jwt). "
        f"Advertised methods: {methods}"
    )


def _extract_string_list(doc: JsonObject, key: str) -> list[str]:
    """Extract a JSON array of strings from a discovery document field.

    Returns an empty list when the field is absent or is not a JSON array.
    Non-string elements are silently excluded.

    Args:
        doc: Parsed discovery JSON object.
        key: Field name to extract.

    Returns:
        List of string values from the field, or an empty list.
    """
    raw: JsonValue = doc.get(key)
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, str)]
