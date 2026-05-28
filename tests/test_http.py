"""Tests for conformance.http module."""

from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from conformance.http import build_json_http_client, send_json


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
