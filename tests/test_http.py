"""Tests for conformance.http module."""

from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from conformance.http import JsonHttpClientError, build_json_http_client, send_json


@pytest.mark.unit
class TestBuildJsonHttpClientMtlsValidation:
    """Verify that build_json_http_client rejects partial mTLS configuration."""

    def test_rejects_certificate_without_key(self, tmp_path: Path) -> None:
        """Supplying only client_certificate_path raises ValueError."""
        cert = tmp_path / "client.pem"
        cert.touch()

        with pytest.raises(ValueError, match="must be supplied together"):
            build_json_http_client(
                timeout_seconds=10.0,
                client_certificate_path=cert,
            )

    def test_rejects_key_without_certificate(self, tmp_path: Path) -> None:
        """Supplying only client_private_key_path raises ValueError."""
        key = tmp_path / "client.key"
        key.touch()

        with pytest.raises(ValueError, match="must be supplied together"):
            build_json_http_client(
                timeout_seconds=10.0,
                client_private_key_path=key,
            )

    @patch("conformance.http.httpx.Client")
    def test_accepts_both_certificate_and_key(self, mock_client: object, tmp_path: Path) -> None:
        """Supplying both mTLS paths does not raise ValueError."""
        cert = tmp_path / "client.pem"
        key = tmp_path / "client.key"
        cert.touch()
        key.touch()

        # Should not raise — validation passes when both are supplied.
        build_json_http_client(
            timeout_seconds=10.0,
            client_certificate_path=cert,
            client_private_key_path=key,
        )

    def test_accepts_neither_certificate_nor_key(self) -> None:
        """Omitting both mTLS paths does not raise."""
        client = build_json_http_client(timeout_seconds=10.0)
        client.close()


@pytest.mark.unit
class TestSendJsonDeleteBody:
    """Verify that send_json transmits the JSON body for DELETE requests."""

    def test_delete_sends_json_body(self) -> None:
        """DELETE method should include json_body in the request."""
        received_bodies: list[bytes | None] = []

        def handler(request: httpx.Request) -> httpx.Response:
            """Capture the request body and return a JSON response."""
            received_bodies.append(request.content)
            return httpx.Response(200, json={"deleted": True})

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            result = send_json(
                client,
                "DELETE",
                "https://example.com/resource/1",
                json_body={"reason": "expired"},
            )

        assert result.status_code == 200
        assert received_bodies[0] is not None
        assert b'"reason"' in received_bodies[0]

    def test_get_does_not_send_json_body(self) -> None:
        """GET method should not include json_body in the request."""
        received_bodies: list[bytes | None] = []

        def handler(request: httpx.Request) -> httpx.Response:
            """Capture the request body and return a JSON response."""
            received_bodies.append(request.content)
            return httpx.Response(200, json={"ok": True})

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            result = send_json(
                client,
                "GET",
                "https://example.com/resource",
                json_body={"ignored": True},
            )

        assert result.status_code == 200
        assert received_bodies[0] == b""


@pytest.mark.unit
class TestSendJsonHeaderMerging:
    """Verify that send_json merges headers case-insensitively per RFC 7230."""

    def test_manifest_header_overrides_default_accept_case_insensitively(self) -> None:
        """A lowercase ``accept`` header must replace the default ``Accept``."""
        captured: list[httpx.Headers] = []

        def handler(request: httpx.Request) -> httpx.Response:
            """Capture the outgoing request headers and return a JSON response."""
            captured.append(request.headers)
            return httpx.Response(200, json={"ok": True})

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            send_json(
                client,
                "GET",
                "https://example.com/resource",
                headers={"accept": "application/vnd.example+json"},
            )

        sent = captured[0]
        # httpx.Headers exposes case-insensitive lookup and collapses duplicate
        # case-equivalent names; ``get_list`` returns every value that was set.
        assert sent.get_list("Accept", split_commas=True) == ["application/vnd.example+json"]

    def test_manifest_header_added_alongside_default_accept(self) -> None:
        """A header with a different name is added without affecting Accept."""
        captured: list[httpx.Headers] = []

        def handler(request: httpx.Request) -> httpx.Response:
            """Capture the outgoing request headers and return a JSON response."""
            captured.append(request.headers)
            return httpx.Response(200, json={"ok": True})

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            send_json(
                client,
                "GET",
                "https://example.com/resource",
                headers={"X-Request-ID": "abc-123"},
            )

        sent = captured[0]
        assert sent["accept"] == "application/json"
        assert sent["x-request-id"] == "abc-123"


@pytest.mark.unit
class TestSendJsonMethodCaseInsensitive:
    """Verify that send_json normalises the HTTP method to uppercase.

    The body-selection guard treats the supported methods as a closed
    uppercase set, but httpx accepts any case. Without normalisation, a
    caller passing ``"post"`` would silently drop the JSON body.
    """

    @pytest.mark.parametrize("method", ["post", "Post", "PUT", "patch", "delete"])
    def test_lowercase_or_mixed_case_method_still_sends_body(self, method: str) -> None:
        """Body must be transmitted regardless of method casing."""
        received_bodies: list[bytes] = []
        received_methods: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            """Capture the request method and body, then return JSON."""
            received_methods.append(request.method)
            received_bodies.append(request.content)
            return httpx.Response(200, json={"ok": True})

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            send_json(
                client,
                method,
                "https://example.com/resource",
                json_body={"key": "value"},
            )

        # httpx uppercases the method on the wire; we assert the body made it through.
        assert received_methods[0] == method.upper()
        assert b'"key"' in received_bodies[0]


@pytest.mark.unit
class TestSendJsonNoContentResponses:
    """Verify that send_json tolerates RFC 9110 no-content responses.

    Statuses 204, 205, and 304 are defined by RFC 9110 to carry no message
    body. A manifest step that exercises one of these (e.g. ``DELETE``
    returning 204) must reach the assertion phase rather than failing in
    the transport layer with "not valid JSON".
    """

    @pytest.mark.parametrize("status_code", [204, 205, 304])
    def test_no_content_status_returns_empty_body(self, status_code: int) -> None:
        """No-content statuses produce an empty JSON object body."""

        def handler(_request: httpx.Request) -> httpx.Response:
            """Return a no-content response with no body."""
            return httpx.Response(status_code)

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            result = send_json(client, "DELETE", "https://example.com/resource/1")

        assert result.status_code == status_code
        assert result.body == {}

    @pytest.mark.parametrize("status_code", [204, 205, 304])
    def test_no_content_status_ignores_unexpected_body(self, status_code: int) -> None:
        """Non-empty bodies on no-content statuses are discarded, not parsed.

        Some upstreams incorrectly include a body on 204/205/304. We must
        not let invalid JSON in such a body raise — the status itself is
        the authoritative signal and the executor's assertions only need
        the status code.
        """

        def handler(_request: httpx.Request) -> httpx.Response:
            """Return a no-content status with a non-JSON body."""
            return httpx.Response(status_code, text="not-json-at-all")

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            result = send_json(client, "DELETE", "https://example.com/resource/1")

        assert result.status_code == status_code
        assert result.body == {}

    def test_non_no_content_status_with_invalid_body_still_raises(self) -> None:
        """The no-content carve-out does not relax JSON parsing for 200 OK."""

        def handler(_request: httpx.Request) -> httpx.Response:
            """Return a 200 OK with a non-JSON body."""
            return httpx.Response(200, text="not-json")

        with (
            httpx.Client(transport=httpx.MockTransport(handler)) as client,
            pytest.raises(JsonHttpClientError, match="was not valid JSON"),
        ):
            send_json(client, "GET", "https://example.com/resource")

    def test_non_no_content_status_with_non_object_body_still_raises(self) -> None:
        """A 200 OK returning a JSON array still violates the object contract."""

        def handler(_request: httpx.Request) -> httpx.Response:
            """Return a 200 OK with a JSON array body."""
            return httpx.Response(200, json=[1, 2, 3])

        with (
            httpx.Client(transport=httpx.MockTransport(handler)) as client,
            pytest.raises(JsonHttpClientError, match="must be a JSON object"),
        ):
            send_json(client, "GET", "https://example.com/resource")

    def test_invalid_json_body_preserves_status_code_on_error(self) -> None:
        """A non-JSON 4xx body must expose the HTTP status on the raised error (DL-0011)."""

        def handler(_request: httpx.Request) -> httpx.Response:
            """Return a 404 with an HTML body (typical reverse-proxy error page)."""
            return httpx.Response(404, text="<html>Not Found</html>")

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(JsonHttpClientError, match="was not valid JSON") as excinfo:
                send_json(client, "GET", "https://example.com/missing")

        assert excinfo.value.status_code == 404

    def test_non_object_json_body_preserves_status_code_on_error(self) -> None:
        """A JSON array 4xx body must still expose the HTTP status on the raised error."""

        def handler(_request: httpx.Request) -> httpx.Response:
            """Return a 422 with a JSON array body."""
            return httpx.Response(422, json=["error1", "error2"])

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(JsonHttpClientError, match="must be a JSON object") as excinfo:
                send_json(client, "GET", "https://example.com/resource")

        assert excinfo.value.status_code == 422

    def test_connection_failure_has_no_status_code(self) -> None:
        """A transport-level failure raises with status_code=None (no response received)."""

        def handler(_request: httpx.Request) -> httpx.Response:
            """Simulate a connection error before any response is received."""
            raise httpx.ConnectError("connection refused")

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(JsonHttpClientError, match="Request failed") as excinfo:
                send_json(client, "GET", "https://example.com/resource")

        assert excinfo.value.status_code is None


@pytest.mark.unit
class TestSendJsonFormBody:
    """Verify form-urlencoded body dispatch.

    Covers the ``application/x-www-form-urlencoded`` capability used by
    OAuth 2.0 token-exchange-style manifest steps. The helper must:

    - Encode form fields using httpx's native form encoder (never a
      hand-rolled ``urllib.parse.urlencode``).
    - Default ``Content-Type: application/x-www-form-urlencoded`` when the
      caller has not supplied one.
    - Allow the caller to override ``Content-Type`` case-insensitively.
    - Preserve the default ``Accept`` header.
    - Reject ambiguous calls that supply both ``json_body`` and ``form_body``.
    """

    def test_form_body_sets_default_content_type_and_encodes_fields(self) -> None:
        """A POST with form_body sends form-urlencoded fields with the default Content-Type."""
        captured: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            """Capture the outgoing request and return a JSON response."""
            captured.append(request)
            return httpx.Response(200, json={"ok": True})

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            send_json(
                client,
                "POST",
                "https://example.com/token",
                form_body={"grant_type": "authorization_code", "code": "abc123"},
            )

        sent = captured[0]
        assert sent.headers["content-type"] == "application/x-www-form-urlencoded"
        assert sent.content == b"grant_type=authorization_code&code=abc123"

    def test_form_body_percent_encodes_special_characters(self) -> None:
        """Reserved characters in form values are percent-encoded on the wire."""
        captured: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            """Capture the outgoing request and return a JSON response."""
            captured.append(request)
            return httpx.Response(200, json={"ok": True})

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            send_json(
                client,
                "POST",
                "https://example.com/token",
                form_body={"redirect_uri": "https://client/cb?x=1&y=2", "scope": "openid profile"},
            )

        sent = captured[0]
        # ``application/x-www-form-urlencoded`` form-url-encoding: reserved
        # characters (``=`` ``&`` ``?`` ``:``) are percent-encoded, and space
        # may be encoded as ``+`` or ``%20`` depending on the httpx version.
        # Match individual encoded substrings rather than the full body so the
        # test is robust to field ordering between httpx versions.
        assert b"redirect_uri=https%3A%2F%2Fclient%2Fcb%3Fx%3D1%26y%3D2" in sent.content
        assert b"scope=openid+profile" in sent.content or b"scope=openid%20profile" in sent.content

    def test_manifest_content_type_overrides_form_default_case_insensitively(self) -> None:
        """A manifest-supplied Content-Type (any case) replaces the form default."""
        captured: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            """Capture the outgoing request and return a JSON response."""
            captured.append(request)
            return httpx.Response(200, json={"ok": True})

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            send_json(
                client,
                "POST",
                "https://example.com/token",
                headers={"content-type": "application/x-www-form-urlencoded; charset=UTF-8"},
                form_body={"grant_type": "client_credentials"},
            )

        sent = captured[0]
        # Exactly one Content-Type, matching the manifest-supplied value.
        assert sent.headers.get_list("Content-Type", split_commas=False) == [
            "application/x-www-form-urlencoded; charset=UTF-8"
        ]

    def test_form_body_preserves_default_accept_header(self) -> None:
        """The default ``Accept: application/json`` header is preserved on form requests."""
        captured: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            """Capture the outgoing request and return a JSON response."""
            captured.append(request)
            return httpx.Response(200, json={"ok": True})

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            send_json(
                client,
                "POST",
                "https://example.com/token",
                form_body={"grant_type": "client_credentials"},
            )

        assert captured[0].headers["accept"] == "application/json"

    def test_form_body_dropped_on_get_method(self) -> None:
        """GET requests must not transmit a form body, mirroring the JSON-on-GET rule."""
        captured: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            """Capture the outgoing request and return a JSON response."""
            captured.append(request)
            return httpx.Response(200, json={"ok": True})

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            send_json(
                client,
                "GET",
                "https://example.com/resource",
                form_body={"ignored": "true"},
            )

        sent = captured[0]
        assert sent.content == b""
        # No default form Content-Type leaked onto a body-less GET.
        assert "content-type" not in sent.headers

    def test_rejects_simultaneous_json_and_form_body(self) -> None:
        """Supplying both json_body and form_body raises ValueError."""
        with (
            httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={}))) as client,
            pytest.raises(ValueError, match="mutually exclusive"),
        ):
            send_json(
                client,
                "POST",
                "https://example.com/resource",
                json_body={"a": 1},
                form_body={"b": "2"},
            )
