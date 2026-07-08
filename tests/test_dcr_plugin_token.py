"""Unit tests for conformance.plugins.dcr.token module."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast
from unittest.mock import MagicMock, patch

import httpx
import pytest

from conformance.http import JsonHttpClientError, JsonHttpResponse
from conformance.json_types import JsonObject
from conformance.masking import MASKED_VALUE
from conformance.plugins.dcr.client_state import DcrClientState
from conformance.plugins.dcr.token import (
    DcrTokenError,
    _build_client_assertion,
    _parse_successful_token_response,
    _send_token_form,
    request_token,
    request_token_wrong_client_id,
)

if TYPE_CHECKING:
    pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_client_state(
    client_id: str = "client-001",
    rat: str | None = "rat-value",
) -> DcrClientState:
    """Build a minimal DcrClientState for testing.

    Args:
        client_id: The registered client identifier.
        rat: Optional registration access token.

    Returns:
        A :class:`~conformance.plugins.dcr.client_state.DcrClientState`.
    """
    return DcrClientState(
        client_id=client_id,
        registration_client_uri=None,
        token_endpoint_auth_method="tls_client_auth",  # noqa: S106
        granted_scopes=None,
        raw_response_masked={},
        client_secret_present=False,
        registration_access_token_present=rat is not None,
        _client_secret=None,
        _registration_access_token=rat,
    )


def _make_token_response_body(**overrides: object) -> JsonObject:
    """Build a minimal valid token response body dict.

    Args:
        **overrides: Fields to override or add to the base body.

    Returns:
        A :class:`~conformance.json_types.JsonObject` token response body.
    """
    base: JsonObject = {
        "access_token": "live-access-token",
        "token_type": "Bearer",
        "expires_in": 3600,
    }
    extra: JsonObject = cast(JsonObject, overrides)
    return {**base, **extra}


def _make_json_response(body: JsonObject, status_code: int = 200) -> JsonHttpResponse:
    """Build a JsonHttpResponse for use in mocks.

    Args:
        body: Response body dict.
        status_code: HTTP status code.

    Returns:
        A :class:`~conformance.http.JsonHttpResponse`.
    """
    return JsonHttpResponse(
        url="https://as.example.com/token",
        status_code=status_code,
        body=body,
    )


# ---------------------------------------------------------------------------
# Tests: _send_token_form
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSendTokenForm:
    """Verify _send_token_form dispatches and handles responses correctly."""

    def test_returns_status_and_body_on_success(self) -> None:
        """_send_token_form returns (status_code, body) on a 200 response."""
        client = MagicMock(spec=httpx.Client)
        form = {"grant_type": "client_credentials", "client_id": "x"}
        response_body: JsonObject = {"access_token": "tok", "token_type": "Bearer"}
        with patch(
            "conformance.plugins.dcr.token.send_json",
            return_value=_make_json_response(response_body),
        ):
            status, body = _send_token_form(client, "https://as.example.com/token", form)
        assert status == 200
        assert body["token_type"] == "Bearer"  # noqa: S105

    def test_returns_status_and_empty_body_on_non_json_error(self) -> None:
        """Non-JSON error responses are returned as (status_code, {})."""
        client = MagicMock(spec=httpx.Client)
        form = {"grant_type": "client_credentials"}
        with patch(
            "conformance.plugins.dcr.token.send_json",
            side_effect=JsonHttpClientError("bad response", status_code=401),
        ):
            status, body = _send_token_form(client, "https://as.example.com/token", form)
        assert status == 401
        assert body == {}

    def test_raises_dcr_token_error_on_network_failure(self) -> None:
        """DcrTokenError is raised when no HTTP response is received."""
        client = MagicMock(spec=httpx.Client)
        form = {"grant_type": "client_credentials"}
        with (
            patch(
                "conformance.plugins.dcr.token.send_json",
                side_effect=JsonHttpClientError("connection refused", status_code=None),
            ),
            pytest.raises(DcrTokenError, match="failed"),
        ):
            _send_token_form(client, "https://as.example.com/token", form)


# ---------------------------------------------------------------------------
# Tests: _parse_successful_token_response
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestParseSuccessfulTokenResponse:
    """Verify _parse_successful_token_response handles status codes and body."""

    def test_returns_token_response_on_200(self) -> None:
        """Returns a DcrTokenResponse for a valid 200 body."""
        body = _make_token_response_body()
        result = _parse_successful_token_response(body, 200, "https://as.example.com/token")
        assert result.access_token_masked == MASKED_VALUE
        assert result.token_type == "Bearer"  # noqa: S105
        assert result.expires_in == 3600

    def test_raises_on_4xx_response(self) -> None:
        """DcrTokenError is raised when the status code is 4xx."""
        body: JsonObject = {"error": "invalid_client"}
        with pytest.raises(DcrTokenError, match="401"):
            _parse_successful_token_response(body, 401, "https://as.example.com/token")

    def test_raises_on_5xx_response(self) -> None:
        """DcrTokenError is raised when the status code is 5xx."""
        with pytest.raises(DcrTokenError, match="503"):
            _parse_successful_token_response({}, 503, "https://as.example.com/token")

    def test_error_code_included_in_message(self) -> None:
        """The OAuth error code is included in the DcrTokenError message."""
        body: JsonObject = {"error": "access_denied"}
        with pytest.raises(DcrTokenError, match="access_denied"):
            _parse_successful_token_response(body, 403, "https://as.example.com/token")

    def test_raises_on_missing_token_type(self) -> None:
        """DcrTokenError is raised when token_type is absent from the body."""
        body: JsonObject = {"access_token": "tok"}
        with pytest.raises(DcrTokenError, match="invalid token response"):
            _parse_successful_token_response(body, 200, "https://as.example.com/token")


# ---------------------------------------------------------------------------
# Tests: request_token dispatching
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRequestTokenDispatching:
    """Verify request_token dispatches to the correct grant helper."""

    def test_dispatches_tls_client_auth(self) -> None:
        """request_token calls _tls_client_auth_grant for tls_client_auth method."""
        client = MagicMock(spec=httpx.Client)
        state = _make_client_state()
        credentials = MagicMock()
        expected_response_body = _make_token_response_body()

        with patch(
            "conformance.plugins.dcr.token.send_json",
            return_value=_make_json_response(expected_response_body),
        ):
            result = request_token(
                client,
                "https://as.example.com/token",
                state,
                credentials,
                "tls_client_auth",
            )
        assert result.access_token_masked == MASKED_VALUE

    def test_dispatches_private_key_jwt(self) -> None:
        """request_token calls _private_key_jwt_grant for private_key_jwt method."""
        client = MagicMock(spec=httpx.Client)
        state = _make_client_state()
        credentials = MagicMock()
        expected_response_body = _make_token_response_body()

        with (
            patch(
                "conformance.plugins.dcr.token._build_client_assertion",
                return_value="signed.jwt.token",  # noqa: S106
            ),
            patch(
                "conformance.plugins.dcr.token.send_json",
                return_value=_make_json_response(expected_response_body),
            ),
        ):
            result = request_token(
                client,
                "https://as.example.com/token",
                state,
                credentials,
                "private_key_jwt",
            )
        assert result.access_token_masked == MASKED_VALUE


# ---------------------------------------------------------------------------
# Tests: request_token_wrong_client_id
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRequestTokenWrongClientId:
    """Verify request_token_wrong_client_id (DCR-011 helper) behaviour."""

    def test_sends_fake_client_id_in_form(self) -> None:
        """The fake_client_id is included in the form sent to the token endpoint."""
        client = MagicMock(spec=httpx.Client)
        captured_form: dict[str, str] = {}

        def capture_form(
            _client: httpx.Client,
            _method: str,
            _url: str,
            *,
            form_body: dict[str, str] | None = None,
            **_kw: object,
        ) -> JsonHttpResponse:
            """Capture form_body for inspection and return a 401."""
            if form_body:
                captured_form.update(form_body)
            return _make_json_response({"error": "invalid_client"}, status_code=401)

        with patch("conformance.plugins.dcr.token.send_json", side_effect=capture_form):
            status, body = request_token_wrong_client_id(
                client,
                "https://as.example.com/token",
                fake_client_id="no-such-client-xyz",
            )

        assert captured_form["client_id"] == "no-such-client-xyz"
        assert captured_form["grant_type"] == "client_credentials"
        assert status == 401
        assert body.get("error") == "invalid_client"

    def test_returns_4xx_on_unrecognised_client(self) -> None:
        """Returns (4xx, error_body) when the ASPSP rejects the fake client ID."""
        client = MagicMock(spec=httpx.Client)
        error_body: JsonObject = {"error": "unauthorized_client"}
        with patch(
            "conformance.plugins.dcr.token.send_json",
            return_value=_make_json_response(error_body, status_code=400),
        ):
            status, body = request_token_wrong_client_id(
                client,
                "https://as.example.com/token",
                fake_client_id="bad-id",
            )
        assert status == 400
        assert body.get("error") == "unauthorized_client"


# ---------------------------------------------------------------------------
# Tests: _build_client_assertion structure
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBuildClientAssertion:
    """Verify _build_client_assertion produces a well-formed JWT."""

    def test_build_assertion_calls_sign(self) -> None:
        """_build_client_assertion invokes _sign_ps256_jwt with correct claims."""
        credentials = MagicMock()
        credentials.signing_certificate_pem = b"PEM"
        credentials.signing_private_key_pem = b"KEY"

        captured_claims: dict[str, object] = {}

        def fake_sign(
            _key_pem: bytes,
            *,
            kid: str,
            claims: dict[str, object],
        ) -> str:
            """Capture signing inputs and return a stub JWT."""
            captured_claims.update(claims)
            return "header.payload.sig"

        with (
            patch("conformance.plugins.dcr.token.derive_kid", return_value="kid-123"),
            patch("conformance.plugins.dcr.token._sign_ps256_jwt", side_effect=fake_sign),
        ):
            token = _build_client_assertion(
                token_endpoint="https://as.example.com/token",  # noqa: S106
                client_id="my-client",
                credentials=credentials,
            )

        assert token == "header.payload.sig"  # noqa: S105
        assert captured_claims["iss"] == "my-client"
        assert captured_claims["sub"] == "my-client"
        assert captured_claims["aud"] == "https://as.example.com/token"
        assert "jti" in captured_claims
        assert "iat" in captured_claims
        assert "exp" in captured_claims
        # exp should be approximately 5 minutes after iat
        assert int(str(captured_claims["exp"])) - int(str(captured_claims["iat"])) == 300  # noqa: PLR2004
