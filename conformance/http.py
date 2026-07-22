"""Generic HTTP helpers for conformance engine network requests."""

from __future__ import annotations

import ssl
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import cast
from urllib.parse import parse_qsl, urlencode

import httpx

from conformance.default_trust import build_default_tls_context
from conformance.headers import FrozenHeaders, freeze_headers
from conformance.json_types import JsonObject, JsonValue
from conformance.masking import mask_form_fields

# HTTP statuses that RFC 9110 defines as carrying no message body.
# A compliant endpoint (and most reverse proxies) will return zero-length
# bodies for these, so attempting ``response.json()`` would raise
# ``ValueError`` and mask the user's status-only assertion (e.g. a manifest
# step that DELETEs a resource and asserts ``http_status: 204``). We
# normalise these to an empty JSON object so the assertion phase still
# runs; a ``json_field`` assertion against an empty object naturally fails
# with "field is missing".
_NO_CONTENT_STATUS_CODES: frozenset[int] = frozenset({204, 205, 304})
_NON_JSON_SNIPPET_LIMIT: int = 200


class JsonHttpClientError(RuntimeError):
    """Raised when a JSON HTTP request or response is invalid.

    The optional ``status_code`` attribute (set via ``__init__``) carries the
    HTTP status from the response when the failure occurred after a response
    was received (e.g. non-JSON body); it is ``None`` when no response was
    obtained (e.g. connection failure). Preserving the status here lets the
    executor populate ``StepResult.status_code`` for DL-0011 client-error
    reporting even when the body could not be parsed.

    For non-JSON responses, ``content_type`` and ``body_snippet`` may carry
    additional safe diagnostics. ``body_snippet`` is populated only when the
    body can be masked using existing masking helpers.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        content_type: str | None = None,
        body_snippet: str | None = None,
    ) -> None:
        """Initialise the error with transport and safe response diagnostics.

        Args:
            message: Human-readable failure description.
            status_code: HTTP status code from the response, if one was
                received before the failure was detected.
            content_type: Response ``Content-Type`` value when available.
            body_snippet: Masked/truncated non-JSON body preview when it can be
                safely derived.
        """
        super().__init__(message)
        self.status_code = status_code
        self.content_type = content_type
        self.body_snippet = body_snippet


def _truncate_non_json_snippet(snippet: str) -> str:
    """Truncate a non-JSON diagnostic snippet to a fixed, safe length.

    Args:
        snippet: Full candidate snippet.

    Returns:
        Original snippet when within the limit, otherwise a truncated snippet
        with an explicit suffix.
    """
    if len(snippet) <= _NON_JSON_SNIPPET_LIMIT:
        return snippet
    return f"{snippet[:_NON_JSON_SNIPPET_LIMIT]}...(truncated)"


def _safe_non_json_body_snippet(response: httpx.Response, *, content_type: str | None) -> str | None:
    """Build a safe non-JSON body preview when masking support is available.

    We only emit a body preview for form-urlencoded payloads because the
    project has explicit field-level masking for those values. Free-form text
    (HTML/plain text) is intentionally omitted to avoid leaking participant or
    credential data in error evidence.

    Args:
        response: HTTP response whose body failed JSON parsing.
        content_type: Parsed response ``Content-Type`` header value.

    Returns:
        A masked/truncated preview string, or ``None`` when no safe preview can
        be generated.
    """
    if content_type is None:
        return None
    if "application/x-www-form-urlencoded" not in content_type.lower():
        return None

    form_pairs = parse_qsl(response.text, keep_blank_values=True)
    if not form_pairs:
        return None
    masked_form_fields = mask_form_fields(dict(form_pairs))
    masked_serialized = urlencode(masked_form_fields, safe="*")
    if not masked_serialized:
        return None
    return _truncate_non_json_snippet(masked_serialized)


def _build_non_json_response_error_message(
    *,
    url: str,
    status_code: int,
    content_type: str | None,
    body_snippet: str | None,
) -> str:
    """Build a participant-facing non-JSON response diagnostic message.

    Args:
        url: Request URL used for the failed call.
        status_code: HTTP status code returned by the endpoint.
        content_type: Response ``Content-Type`` header value, if present.
        body_snippet: Safe body preview when available.

    Returns:
        A concise diagnostic string that preserves the existing "not valid
        JSON" signal and adds endpoint debugging context.
    """
    details: list[str] = [f"status {status_code}"]
    if content_type is not None:
        details.append(f"content-type {content_type}")
    if body_snippet is not None:
        details.append(f"masked body snippet: {body_snippet}")
    return f"Response from {url} was not valid JSON ({', '.join(details)})"


@dataclass(frozen=True, init=False)
class JsonHttpResponse:
    """Typed JSON response captured for result reporting.

    Attributes:
        url (str): Response URL reported by `httpx` for the returned response.
        status_code (int): HTTP status code returned by the endpoint.
        body (JsonObject): Parsed JSON object body.
        headers (FrozenHeaders): Immutable response headers with
            case-insensitive lookup.
        body_bytes: Exact response body bytes used for detached-JWS response
            signature validation.
    """

    url: str
    status_code: int
    body: JsonObject
    headers: FrozenHeaders
    body_bytes: bytes

    def __init__(
        self,
        *,
        url: str,
        status_code: int,
        body: JsonObject,
        headers: Mapping[str, str] | Iterable[tuple[str, str]] | None = None,
        body_bytes: bytes = b"",
    ) -> None:
        """Initialise a typed JSON response with frozen headers.

        Args:
            url: Response URL reported by `httpx`.
            status_code: HTTP status code returned by the endpoint.
            body: Parsed JSON object body.
            headers: Source response headers copied into an immutable,
                case-insensitive mapping.
            body_bytes: Exact response body bytes.
        """
        object.__setattr__(self, "url", url)
        object.__setattr__(self, "status_code", status_code)
        object.__setattr__(self, "body", body)
        object.__setattr__(self, "headers", freeze_headers(headers))
        object.__setattr__(self, "body_bytes", body_bytes)


def get_json(client: httpx.Client, url: str) -> JsonHttpResponse:
    """Fetch an endpoint and parse a JSON object response.

    Args:
        client: Preconfigured synchronous HTTP client.
        url: HTTPS endpoint URL to fetch.

    Returns:
        Parsed JSON object response with URL and status code.

    Raises:
        JsonHttpClientError: If the request fails, the response is not valid
            JSON, or the payload is not a JSON object.
    """
    return send_json(client, "GET", url)


def send_json(
    client: httpx.Client,
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    json_body: JsonValue | None = None,
    json_body_bytes: bytes | None = None,
    form_body: Mapping[str, str] | None = None,
) -> JsonHttpResponse:
    """Send an HTTP request and parse a JSON object response.

    Dispatches the request using the given method. For methods that support a
    body (POST, PUT, PATCH, DELETE), exactly one of ``json_body``,
    ``json_body_bytes``, or ``form_body`` may be supplied:

    - ``json_body`` is serialised as ``application/json`` via ``httpx``.
    - ``json_body_bytes`` sends already-serialised JSON bytes unchanged.
      This is used for detached-JWS request signing, where the transmitted
      bytes must exactly match the bytes covered by the signature.
    - ``form_body`` is serialised as ``application/x-www-form-urlencoded``
      via ``httpx``'s native form encoder (never hand-rolled), following
      form-url-encoding semantics (e.g. spaces may be encoded as ``+``,
      reserved characters percent-encoded). The exact byte representation
      is delegated to ``httpx``.

    For ``form_body`` requests, ``Content-Type:
    application/x-www-form-urlencoded`` is set automatically **only** when
    the caller has not already supplied a ``Content-Type`` header
    (case-insensitive per RFC 7230). This lets a manifest opt into a
    custom content-type (for example ``application/x-www-form-urlencoded;
    charset=UTF-8``) without the helper silently overriding it.

    The response is parsed as a JSON object regardless of HTTP status code
    (status-agnostic contract per DL-0011), except for the HTTP no-content
    statuses (204, 205, 304) which are defined by RFC 9110 to carry no
    message body and are normalised to an empty JSON object so status-only
    assertions can still be evaluated.

    Args:
        client: Preconfigured synchronous HTTP client.
        method: HTTP method (GET, POST, PUT, PATCH, DELETE).
        url: HTTPS endpoint URL to send the request to.
        headers: Optional additional headers to include in the request.
        json_body: Optional JSON-serialisable body (sent as
            ``application/json`` for POST/PUT/PATCH/DELETE). Mutually
            exclusive with ``json_body_bytes`` and ``form_body``.
        json_body_bytes: Optional pre-serialized JSON request bytes, sent as
            ``application/json`` for POST/PUT/PATCH/DELETE. Mutually
            exclusive with ``json_body`` and ``form_body``.
        form_body: Optional form-field mapping (sent as
            ``application/x-www-form-urlencoded`` for POST/PUT/PATCH/DELETE).
            Mutually exclusive with ``json_body`` and ``json_body_bytes``.

    Returns:
        Parsed JSON object response with URL and status code.

    Raises:
        JsonHttpClientError: If the request fails, the response is not valid
            JSON, or the payload is not a JSON object.
        ValueError: If more than one body encoding is supplied.
    """
    # Reject ambiguous calls eagerly: a single request can carry only one
    # body encoding. Allowing both would force the helper to silently pick
    # one, hiding manifest authoring mistakes.
    supplied_body_encodings = sum(candidate is not None for candidate in (json_body, json_body_bytes, form_body))
    if supplied_body_encodings > 1:
        raise ValueError("send_json: json_body, json_body_bytes, and form_body are mutually exclusive")

    # Normalise the method to uppercase once so the body-selection guard and
    # the dispatch call agree regardless of the caller's casing. httpx accepts
    # any case, but our guard treats the supported set as a closed uppercase
    # literal — without normalisation, ``"post"`` would silently drop the body.
    method = method.upper()

    # Use httpx.Headers (case-insensitive per RFC 7230) so a manifest-supplied
    # header such as ``accept`` correctly overrides the default ``Accept``
    # instead of producing two separate Accept fields on the wire.
    request_headers = httpx.Headers({"Accept": "application/json"})
    if headers:
        request_headers.update(headers)

    # Drop any body for methods that don't carry one. Mutual exclusion
    # between json_body and form_body has already been enforced above, so
    # at most one of these is non-None here — there is no precedence rule.
    method_allows_body = method in ("POST", "PUT", "PATCH", "DELETE")
    send_json_body = json_body if method_allows_body else None
    send_json_body_bytes: bytes | None = json_body_bytes if method_allows_body else None
    send_form_body: Mapping[str, str] | None = form_body if method_allows_body else None

    if send_json_body_bytes is not None and "content-type" not in request_headers:
        request_headers["Content-Type"] = "application/json"

    # Set the form Content-Type default only when the manifest has not
    # supplied one. ``httpx.Headers.__contains__`` is case-insensitive, so
    # ``content-type`` from a manifest correctly suppresses the default.
    if send_form_body is not None and "content-type" not in request_headers:
        request_headers["Content-Type"] = "application/x-www-form-urlencoded"

    try:
        response = client.request(
            method,
            url,
            headers=request_headers,
            json=send_json_body,
            content=send_json_body_bytes,
            data=send_form_body,
        )
    except httpx.RequestError as error:
        raise JsonHttpClientError(f"Request failed for {url}: {error}") from error

    # RFC 9110 no-content statuses carry no message body. Skip JSON parsing
    # and return an empty object so the executor can still evaluate
    # status-only assertions (e.g. DELETE → 204). ``json_field`` assertions
    # against an empty object will correctly fail with "field is missing".
    if response.status_code in _NO_CONTENT_STATUS_CODES:
        return JsonHttpResponse(
            url=str(response.url),
            status_code=response.status_code,
            headers=response.headers,
            body={},
            body_bytes=response.content,
        )

    try:
        response_body: object = response.json()
    except ValueError as error:
        content_type_header = response.headers.get("content-type")
        content_type = content_type_header.strip() if content_type_header else None
        body_snippet = _safe_non_json_body_snippet(response, content_type=content_type)
        raise JsonHttpClientError(
            _build_non_json_response_error_message(
                url=url,
                status_code=response.status_code,
                content_type=content_type,
                body_snippet=body_snippet,
            ),
            status_code=response.status_code,
            content_type=content_type,
            body_snippet=body_snippet,
        ) from error

    if not isinstance(response_body, dict):
        raise JsonHttpClientError(
            f"Response from {url} must be a JSON object",
            status_code=response.status_code,
        )

    json_body_parsed = cast(dict[str, JsonValue], response_body)
    return JsonHttpResponse(
        url=str(response.url),
        status_code=response.status_code,
        headers=response.headers,
        body=json_body_parsed,
        body_bytes=response.content,
    )


def build_json_http_client(
    *,
    timeout_seconds: float,
    ca_bundle_path: Path | None = None,
    client_certificate_path: Path | None = None,
    client_private_key_path: Path | None = None,
) -> httpx.Client:
    """Build an `httpx` client for JSON conformance requests.

    Args:
        timeout_seconds: Per-request timeout in seconds.
        ca_bundle_path: Optional participant-supplied CA bundle appended to the
            default system roots and bundled Open Banking CA roots.
        client_certificate_path: Optional client certificate for mTLS.
        client_private_key_path: Optional client private key for mTLS.

    Returns:
        Configured synchronous HTTP client.

    Raises:
        ValueError: If only one of ``client_certificate_path`` /
            ``client_private_key_path`` is provided.
    """
    if (client_certificate_path is None) != (client_private_key_path is None):
        raise ValueError("client_certificate_path and client_private_key_path must be supplied together")

    verify: ssl.SSLContext = build_default_tls_context(extra_ca_bundle_path=ca_bundle_path)

    cert: tuple[str, str] | None = None
    if client_certificate_path is not None and client_private_key_path is not None:
        cert = (str(client_certificate_path), str(client_private_key_path))

    try:
        return httpx.Client(timeout=timeout_seconds, verify=verify, cert=cert)
    except ssl.SSLError as error:
        if cert is not None:
            raise ValueError(
                "Unable to load TLS client certificate/private key from "
                f"{client_certificate_path} and {client_private_key_path}: {error}"
            ) from error
        raise ValueError(f"Unable to initialise TLS configuration: {error}") from error
