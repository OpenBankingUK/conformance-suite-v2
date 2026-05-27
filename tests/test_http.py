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
