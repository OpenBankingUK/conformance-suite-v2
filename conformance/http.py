"""Generic HTTP helpers for conformance engine network requests."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import cast

import httpx

from conformance.json_types import JsonObject, JsonValue


class JsonHttpClientError(RuntimeError):
    """Raised when a JSON HTTP request or response is invalid."""


@dataclass(frozen=True)
class JsonHttpResponse:
    """Typed JSON response captured for result reporting.

    Attributes:
        url: Response URL reported by `httpx` for the returned response.
        status_code: HTTP status code returned by the endpoint.
        body: Parsed JSON object body.
    """

    url: str
    status_code: int
    body: JsonObject


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
) -> JsonHttpResponse:
    """Send an HTTP request and parse a JSON object response.

    Dispatches the request using the given method. For methods that support a
    body (POST, PUT, PATCH, DELETE), ``json_body`` is serialised as JSON via ``httpx``.
    The response is parsed as a JSON object regardless of HTTP status code
    (status-agnostic contract per DL-0011).

    Args:
        client: Preconfigured synchronous HTTP client.
        method: HTTP method (GET, POST, PUT, PATCH, DELETE).
        url: HTTPS endpoint URL to send the request to.
        headers: Optional additional headers to include in the request.
        json_body: Optional JSON-serialisable body (sent as ``application/json``
            for POST/PUT/PATCH/DELETE).

    Returns:
        Parsed JSON object response with URL and status code.

    Raises:
        JsonHttpClientError: If the request fails, the response is not valid
            JSON, or the payload is not a JSON object.
    """
    request_headers: dict[str, str] = {"Accept": "application/json"}
    if headers:
        request_headers.update(headers)

    # Only send json_body for methods that support a body
    send_body = json_body if method in ("POST", "PUT", "PATCH", "DELETE") else None

    try:
        response = client.request(method, url, headers=request_headers, json=send_body)
    except httpx.RequestError as error:
        raise JsonHttpClientError(f"Request failed for {url}: {error}") from error

    try:
        response_body: object = response.json()
    except ValueError as error:
        raise JsonHttpClientError(f"Response from {url} was not valid JSON") from error

    if not isinstance(response_body, dict):
        raise JsonHttpClientError(f"Response from {url} must be a JSON object")

    json_body_parsed = cast(dict[str, JsonValue], response_body)
    return JsonHttpResponse(url=str(response.url), status_code=response.status_code, body=json_body_parsed)


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
        ca_bundle_path: Optional CA bundle used for TLS verification.
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

    verify: bool | str = True
    if ca_bundle_path is not None:
        verify = str(ca_bundle_path)

    cert: tuple[str, str] | None = None
    if client_certificate_path is not None and client_private_key_path is not None:
        cert = (str(client_certificate_path), str(client_private_key_path))

    return httpx.Client(timeout=timeout_seconds, verify=verify, cert=cert)
