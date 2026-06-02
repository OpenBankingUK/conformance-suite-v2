"""Helpers for OAuth 2.0 PSU authorisation manifest steps."""

from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from conformance.context import ResponseRecord
from conformance.json_types import JsonObject


def build_authorization_url(
    *,
    endpoint: str,
    client_id: str,
    redirect_uri: str,
    response_type: str,
    scope: str,
    state: str,
    request_object: str | None = None,
) -> str:
    """Build an OAuth 2.0 authorisation URL with encoded query parameters.

    Existing query parameters on the authorisation endpoint are preserved and
    the PSU step parameters are appended using :func:`urllib.parse.urlencode`
    so reserved characters in FAPI hybrid-flow values (for example the space
    in ``"code id_token"``) are encoded by the standard library.

    Args:
        endpoint: Authorisation endpoint URL, already resolved from the manifest.
        client_id: OAuth 2.0 client identifier.
        redirect_uri: Registered redirect URI to receive the ASPSP callback.
        response_type: OAuth 2.0 ``response_type`` value.
        scope: OAuth 2.0 ``scope`` value.
        state: Opaque state value registered in the auth-session store.
        request_object: Optional JAR request object JWT, sent as the
            ``request`` query parameter when present.

    Returns:
        Complete authorisation URL ready to surface to the participant or
        issue in headless mode.
    """
    parts = urlsplit(endpoint)
    query_items = parse_qsl(parts.query, keep_blank_values=True)
    query_items.extend(
        [
            ("client_id", client_id),
            ("redirect_uri", redirect_uri),
            ("response_type", response_type),
            ("scope", scope),
            ("state", state),
        ]
    )
    if request_object is not None:
        query_items.append(("request", request_object))
    return urlunsplit(parts._replace(query=urlencode(query_items)))


def synthesize_psu_response(*, code: str, state: str) -> ResponseRecord:
    """Create a synthetic response record for a captured PSU authorisation code.

    No JSON HTTP response is fetched in manual mode; nevertheless downstream
    manifest steps need the captured ``code`` to be addressable through the
    normal ``${steps.<id>.response.body.code}`` placeholder grammar. This
    helper builds that context record while keeping result-file evidence
    summary-only for passing PSU steps.

    Args:
        code: Authorization code captured from the ASPSP redirect.
        state: State value correlated with the captured code.

    Returns:
        Synthetic :class:`ResponseRecord` whose body contains ``code`` and
        ``state`` fields for standard placeholder resolution.
    """
    body: JsonObject = {"code": code, "state": state}
    return ResponseRecord(status_code=200, body=body)


def redirect_matches_registered_uri(*, location: str, redirect_uri: str) -> bool:
    """Return whether an ASPSP redirect targets the configured callback URI.

    Query strings and fragments are intentionally ignored because the ASPSP
    appends OAuth 2.0 response parameters there. Scheme, effective host/port,
    and path must match the manifest's ``redirectUri`` after hostname casing
    is normalised by :func:`urllib.parse.urlsplit`.

    Args:
        location: Redirect URL received in the headless authorisation response.
        redirect_uri: Manifest-configured callback URI for this PSU step.

    Returns:
        ``True`` when the redirect target matches the configured URI target;
        otherwise ``False``.
    """
    location_parts = urlsplit(location)
    redirect_parts = urlsplit(redirect_uri)
    return (
        location_parts.scheme == redirect_parts.scheme
        and location_parts.hostname == redirect_parts.hostname
        and _effective_port(location_parts.scheme, location_parts.port)
        == _effective_port(redirect_parts.scheme, redirect_parts.port)
        and location_parts.path == redirect_parts.path
    )


def _effective_port(scheme: str, parsed_port: int | None) -> int | None:
    """Return the explicit or default port for a parsed URI.

    Args:
        scheme: URI scheme parsed from the URL.
        parsed_port: Explicit port parsed from the URL, or ``None`` when the
            URL omitted a port.

    Returns:
        The explicit port when present, the default port for HTTP(S) schemes,
        or ``None`` when no default is known.
    """
    if parsed_port is not None:
        return parsed_port
    if scheme == "https":
        return 443
    if scheme == "http":
        return 80
    return None


def extract_redirect_parameters(location: str) -> dict[str, str]:
    """Extract query parameters from an ASPSP redirect URL.

    OAuth 2.0 authorisation responses carry ``state`` and either ``code`` or
    ``error`` in the redirect query string. Duplicate keys are collapsed using
    the last value so callers get a simple mapping for validation.

    Args:
        location: Redirect URL received in the headless authorisation response.

    Returns:
        Query parameter mapping with blank values preserved.
    """
    return dict(parse_qsl(urlsplit(location).query, keep_blank_values=True))
