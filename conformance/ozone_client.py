"""HTTP client for the initial Ozone model-bank discovery smoke check.

The current smoke check exercises OpenID Provider discovery and JWKS retrieval,
which are early FAPI/OIDC prerequisites before the full conformance engine lands.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from conformance.http import JsonHttpClientError, JsonHttpResponse, build_json_http_client, get_json
from conformance.json_types import JsonObject
from conformance.model_bank_config import ModelBankConfig
from conformance.url_validation import HttpsUrlValidationError, validate_https_url


class OzoneClientError(RuntimeError):
    """Raised when the Ozone model-bank request or response is invalid."""


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
        return cls(
            build_json_http_client(
                timeout_seconds=config.timeout_seconds,
                ca_bundle_path=config.tls.ca_bundle_path,
                client_certificate_path=config.tls.client_certificate_path,
                client_private_key_path=config.tls.client_private_key_path,
            )
        )

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
        """Fetch a JSON object response and translate generic errors."""
        try:
            response = get_json(self._client, url)
        except JsonHttpClientError as error:
            raise OzoneClientError(str(error)) from error
        if response.status_code >= 400:
            raise OzoneClientError(f"Request failed for {url}: HTTP status {response.status_code}")
        return response


def _required_response_string(response_body: JsonObject, key: str) -> str:
    value = response_body.get(key)
    if not isinstance(value, str) or not value.strip():
        raise OzoneClientError(f"Discovery document must contain a non-empty {key}")
    return value.strip()


def _validate_https_url(value: str, *, key: str) -> None:
    try:
        validate_https_url(value, label=key)
    except HttpsUrlValidationError as error:
        raise OzoneClientError(str(error)) from error
