"""HTTP client for the initial Ozone model-bank discovery smoke check.

The current smoke check exercises OpenID Provider discovery and JWKS retrieval,
which are early FAPI/OIDC prerequisites before the full conformance engine lands.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast
from urllib.parse import urlparse

import httpx

from conformance.json_types import JsonObject, JsonValue
from conformance.model_bank_config import ModelBankConfig


class OzoneClientError(RuntimeError):
    """Raised when the Ozone model-bank request or response is invalid."""


@dataclass(frozen=True)
class JsonHttpResponse:
    """Typed JSON response captured for result reporting.

    Attributes:
        url: Final response URL after redirects.
        status_code: HTTP status code returned by the model bank.
        body: Parsed JSON object body.
    """

    url: str
    status_code: int
    body: JsonObject


@dataclass(frozen=True)
class DiscoveryDocument:
    """Validated OpenID discovery metadata needed by the smoke check.

    Attributes:
        issuer: HTTPS issuer identifier from the discovery document.
        jwks_uri: HTTPS JWKS endpoint advertised by the issuer.
        raw: Complete discovery document retained for future result details.
    """

    issuer: str
    jwks_uri: str
    raw: JsonObject


class OzoneModelBankClient:
    """Fetch Ozone model-bank OpenID metadata using configured TLS settings."""

    def __init__(self, client: httpx.Client) -> None:
        """Create a model-bank client around an `httpx` client.

        Args:
            client: Preconfigured synchronous HTTP client. Tests can inject a
                mock transport here without changing conformance logic.
        """
        self._client = client

    @classmethod
    def from_config(cls, config: ModelBankConfig) -> OzoneModelBankClient:
        """Build a model-bank client from validated runtime configuration.

        Args:
            config: Model-bank configuration containing timeout and TLS paths.

        Returns:
            Client ready to fetch discovery and JWKS metadata.
        """
        verify: bool | str = True
        if config.tls.ca_bundle_path is not None:
            verify = str(config.tls.ca_bundle_path)

        cert: tuple[str, str] | None = None
        if config.tls.client_certificate_path is not None and config.tls.client_private_key_path is not None:
            cert = (str(config.tls.client_certificate_path), str(config.tls.client_private_key_path))

        return cls(httpx.Client(timeout=config.timeout_seconds, verify=verify, cert=cert))

    def close(self) -> None:
        """Close the underlying HTTP connection pool."""
        self._client.close()

    def fetch_discovery_document(self, discovery_url: str) -> tuple[DiscoveryDocument, JsonHttpResponse]:
        """Fetch and validate OpenID Provider discovery metadata.

        Args:
            discovery_url: HTTPS discovery document URL to request.

        Returns:
            Tuple containing validated discovery metadata and the raw JSON HTTP
            response used for structured result reporting.

        Raises:
            OzoneClientError: If the request fails or required discovery fields
                are missing or unsafe.
        """
        response = self._get_json(discovery_url)
        issuer = _required_response_string(response.body, "issuer")
        jwks_uri = _required_response_string(response.body, "jwks_uri")
        _validate_https_url(issuer, key="issuer")
        _validate_https_url(jwks_uri, key="jwks_uri")
        return DiscoveryDocument(issuer=issuer, jwks_uri=jwks_uri, raw=response.body), response

    def fetch_jwks(self, jwks_uri: str) -> JsonHttpResponse:
        """Fetch and minimally validate the issuer JWKS document.

        Args:
            jwks_uri: HTTPS JWKS endpoint from the discovery document.

        Returns:
            JSON HTTP response containing a `keys` array.

        Raises:
            OzoneClientError: If the request fails, the response is not a JSON
                object, or the JWKS payload does not contain a keys array.
        """
        response = self._get_json(jwks_uri)
        keys = response.body.get("keys")
        if not isinstance(keys, list):
            raise OzoneClientError("JWKS response must contain a keys array")
        return response

    def _get_json(self, url: str) -> JsonHttpResponse:
        try:
            response = self._client.get(url, headers={"Accept": "application/json"})
            response.raise_for_status()
        except httpx.HTTPError as error:
            raise OzoneClientError(f"Request failed for {url}: {error}") from error

        try:
            response_body: object = response.json()
        except ValueError as error:
            raise OzoneClientError(f"Response from {url} was not valid JSON") from error

        if not isinstance(response_body, dict):
            raise OzoneClientError(f"Response from {url} must be a JSON object")

        json_body = cast(dict[str, JsonValue], response_body)
        return JsonHttpResponse(url=str(response.url), status_code=response.status_code, body=json_body)


def _required_response_string(response_body: JsonObject, key: str) -> str:
    value = response_body.get(key)
    if not isinstance(value, str) or not value.strip():
        raise OzoneClientError(f"Discovery document must contain a non-empty {key}")
    return value.strip()


def _validate_https_url(value: str, *, key: str) -> None:
    parsed_url = urlparse(value)
    try:
        parsed_port = parsed_url.port
    except ValueError as error:
        raise OzoneClientError(f"{key} must be a valid HTTPS URL") from error

    if parsed_port is not None and parsed_port <= 0:
        raise OzoneClientError(f"{key} must be a valid HTTPS URL")
    if parsed_url.scheme != "https" or parsed_url.hostname is None:
        raise OzoneClientError(f"{key} must be an HTTPS URL")
    if parsed_url.username is not None or parsed_url.password is not None:
        raise OzoneClientError(f"{key} must not include credentials")
