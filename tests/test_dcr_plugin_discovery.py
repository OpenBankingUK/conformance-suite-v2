"""Unit tests for conformance.plugins.dcr.discovery module."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from conformance.http import JsonHttpClientError, JsonHttpResponse
from conformance.json_types import JsonObject
from conformance.plugins.dcr.discovery import (
    DcrDiscoveryError,
    DcrDiscoveryResult,
    _extract_string_list,
    _select_auth_method,
    _validate_required_fields,
    fetch_discovery,
)


def _make_discovery_response(overrides: JsonObject | None = None) -> JsonObject:
    """Build a minimal valid OIDC discovery document."""
    doc: JsonObject = {
        "issuer": "https://as.example.com",
        "registration_endpoint": "https://as.example.com/register",
        "token_endpoint": "https://as.example.com/token",
        "jwks_uri": "https://as.example.com/.well-known/jwks.json",
        "token_endpoint_auth_methods_supported": ["tls_client_auth", "private_key_jwt"],
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "client_credentials"],
    }
    if overrides:
        doc.update(overrides)
    return doc


def _make_http_response(body: JsonObject, status_code: int = 200) -> JsonHttpResponse:
    """Build a mock JsonHttpResponse with the given body."""
    url = "https://as.example.com/.well-known/openid-configuration"
    return JsonHttpResponse(url=url, status_code=status_code, body=body)


@pytest.mark.unit
class TestSelectAuthMethod:
    """Verify auth-method selection logic."""

    def test_prefers_tls_client_auth_over_private_key_jwt(self) -> None:
        """tls_client_auth is chosen when both methods are advertised."""
        result = _select_auth_method(
            ["private_key_jwt", "tls_client_auth"],
            discovery_url="https://as.example.com/.well-known/openid-configuration",
        )
        assert result == "tls_client_auth"

    def test_falls_back_to_private_key_jwt(self) -> None:
        """private_key_jwt is chosen when tls_client_auth is absent."""
        result = _select_auth_method(
            ["private_key_jwt"],
            discovery_url="https://as.example.com/.well-known/openid-configuration",
        )
        assert result == "private_key_jwt"

    def test_raises_when_neither_method_advertised(self) -> None:
        """DcrDiscoveryError is raised when no FAPI method is advertised."""
        with pytest.raises(DcrDiscoveryError, match="FAPI 1 Advanced-compatible"):
            _select_auth_method(
                ["client_secret_post", "none"],
                discovery_url="https://as.example.com/.well-known/openid-configuration",
            )

    def test_raises_when_methods_list_is_empty(self) -> None:
        """DcrDiscoveryError is raised for an empty advertised methods list."""
        with pytest.raises(DcrDiscoveryError):
            _select_auth_method(
                [],
                discovery_url="https://as.example.com/.well-known/openid-configuration",
            )


@pytest.mark.unit
class TestValidateRequiredFields:
    """Verify required-field checks on discovery documents."""

    def test_passes_when_all_required_fields_present(self) -> None:
        """No exception raised when all required fields are present."""
        _validate_required_fields(_make_discovery_response(), "https://as.example.com/.well-known/openid-configuration")

    def test_raises_when_registration_endpoint_missing(self) -> None:
        """DcrDiscoveryError is raised when registration_endpoint is absent."""
        doc = _make_discovery_response()
        del doc["registration_endpoint"]
        with pytest.raises(DcrDiscoveryError, match="registration_endpoint"):
            _validate_required_fields(doc, "https://as.example.com/.well-known/openid-configuration")

    def test_raises_when_token_endpoint_missing(self) -> None:
        """DcrDiscoveryError is raised when token_endpoint is absent."""
        doc = _make_discovery_response()
        del doc["token_endpoint"]
        with pytest.raises(DcrDiscoveryError, match="token_endpoint"):
            _validate_required_fields(doc, "https://as.example.com/.well-known/openid-configuration")

    def test_raises_when_issuer_missing(self) -> None:
        """DcrDiscoveryError is raised when issuer is absent."""
        doc = _make_discovery_response()
        del doc["issuer"]
        with pytest.raises(DcrDiscoveryError, match="issuer"):
            _validate_required_fields(doc, "https://as.example.com/.well-known/openid-configuration")

    def test_raises_when_jwks_uri_missing(self) -> None:
        """DcrDiscoveryError is raised when jwks_uri is absent."""
        doc = _make_discovery_response()
        del doc["jwks_uri"]
        with pytest.raises(DcrDiscoveryError, match="jwks_uri"):
            _validate_required_fields(doc, "https://as.example.com/.well-known/openid-configuration")


@pytest.mark.unit
class TestExtractStringList:
    """Verify _extract_string_list handles various field types."""

    def test_returns_list_of_strings(self) -> None:
        """String items are extracted correctly."""
        doc: JsonObject = {"methods": ["tls_client_auth", "private_key_jwt"]}
        assert _extract_string_list(doc, "methods") == ["tls_client_auth", "private_key_jwt"]

    def test_returns_empty_list_when_field_absent(self) -> None:
        """Empty list returned when field is not present."""
        assert _extract_string_list({}, "methods") == []

    def test_returns_empty_list_when_field_is_not_array(self) -> None:
        """Empty list returned when field value is not a JSON array."""
        doc: JsonObject = {"methods": "tls_client_auth"}
        assert _extract_string_list(doc, "methods") == []

    def test_excludes_non_string_elements(self) -> None:
        """Non-string elements are excluded from the result."""
        doc: JsonObject = {"methods": ["tls_client_auth", 42, None, "private_key_jwt"]}
        assert _extract_string_list(doc, "methods") == ["tls_client_auth", "private_key_jwt"]


@pytest.mark.unit
class TestFetchDiscovery:
    """Verify fetch_discovery happy-path and error paths."""

    def test_returns_discovery_result_on_success(self) -> None:
        """DcrDiscoveryResult is returned when the discovery doc is valid."""
        mock_client = MagicMock()
        response_body = _make_discovery_response()

        with patch("conformance.plugins.dcr.discovery.send_json") as mock_send:
            mock_send.return_value = _make_http_response(response_body)
            result = fetch_discovery(mock_client, "https://as.example.com")

        assert isinstance(result, DcrDiscoveryResult)
        assert result.issuer == "https://as.example.com"
        assert result.registration_endpoint == "https://as.example.com/register"
        assert result.token_endpoint == "https://as.example.com/token"  # noqa: S105
        assert result.selected_auth_method == "tls_client_auth"

    def test_constructs_discovery_url_by_appending_well_known_path(self) -> None:
        """Discovery URL is constructed by appending /.well-known/openid-configuration."""
        mock_client = MagicMock()

        with patch("conformance.plugins.dcr.discovery.send_json") as mock_send:
            mock_send.return_value = _make_http_response(_make_discovery_response())
            fetch_discovery(mock_client, "https://as.example.com")

        called_url = mock_send.call_args[0][2]
        assert called_url == "https://as.example.com/.well-known/openid-configuration"

    def test_strips_trailing_slash_from_issuer_url(self) -> None:
        """Trailing slash on issuer URL is stripped before appending the path."""
        mock_client = MagicMock()

        with patch("conformance.plugins.dcr.discovery.send_json") as mock_send:
            mock_send.return_value = _make_http_response(_make_discovery_response())
            fetch_discovery(mock_client, "https://as.example.com/")

        called_url = mock_send.call_args[0][2]
        assert called_url == "https://as.example.com/.well-known/openid-configuration"

    def test_raises_on_http_error(self) -> None:
        """DcrDiscoveryError is raised when the HTTP request fails."""
        mock_client = MagicMock()

        with patch("conformance.plugins.dcr.discovery.send_json") as mock_send:
            mock_send.side_effect = JsonHttpClientError("connection refused")
            with pytest.raises(DcrDiscoveryError, match="Failed to fetch"):
                fetch_discovery(mock_client, "https://as.example.com")

    def test_raises_when_no_fapi_auth_method(self) -> None:
        """DcrDiscoveryError is raised when discovery has no FAPI auth methods."""
        mock_client = MagicMock()
        doc = _make_discovery_response({"token_endpoint_auth_methods_supported": ["none"]})

        with patch("conformance.plugins.dcr.discovery.send_json") as mock_send:
            mock_send.return_value = _make_http_response(doc)
            with pytest.raises(DcrDiscoveryError, match="FAPI 1 Advanced-compatible"):
                fetch_discovery(mock_client, "https://as.example.com")

    def test_defaults_response_types_to_code_when_absent(self) -> None:
        """response_types defaults to ['code'] when absent from discovery."""
        mock_client = MagicMock()
        doc = _make_discovery_response()
        del doc["response_types_supported"]

        with patch("conformance.plugins.dcr.discovery.send_json") as mock_send:
            mock_send.return_value = _make_http_response(doc)
            result = fetch_discovery(mock_client, "https://as.example.com")

        assert result.response_types_supported == ["code"]

    def test_selects_private_key_jwt_when_tls_not_advertised(self) -> None:
        """private_key_jwt is selected when tls_client_auth is not in the list."""
        mock_client = MagicMock()
        doc = _make_discovery_response({"token_endpoint_auth_methods_supported": ["private_key_jwt"]})

        with patch("conformance.plugins.dcr.discovery.send_json") as mock_send:
            mock_send.return_value = _make_http_response(doc)
            result = fetch_discovery(mock_client, "https://as.example.com")

        assert result.selected_auth_method == "private_key_jwt"
