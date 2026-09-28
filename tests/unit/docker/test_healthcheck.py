"""Unit tests for :mod:`docker.healthcheck`."""

from __future__ import annotations

import ssl

import httpx
import pytest

from docker.healthcheck import HEALTHCHECK_HOST_HEADER, HEALTHCHECK_URL, TLS_CERTIFICATE_PATH, main

pytestmark = pytest.mark.unit


class TestMain:
    """Behaviour of the healthcheck's ``main`` function."""

    def test_healthy_response_returns_zero(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A successful health response exits 0."""
        tls_context = ssl.create_default_context()
        recorded_cafile: str | None = None

        def _fake_create_default_context(*, cafile: str) -> ssl.SSLContext:
            nonlocal recorded_cafile
            recorded_cafile = cafile
            return tls_context

        def _fake_get(
            url: str,
            *,
            headers: dict[str, str],
            timeout: float,
            verify: ssl.SSLContext,
        ) -> httpx.Response:
            assert url == HEALTHCHECK_URL
            assert headers == {"Host": HEALTHCHECK_HOST_HEADER}
            assert verify is tls_context
            return httpx.Response(200, request=httpx.Request("GET", url))

        monkeypatch.setattr(ssl, "create_default_context", _fake_create_default_context)
        monkeypatch.setattr(httpx, "get", _fake_get)
        assert main() == 0
        assert recorded_cafile == TLS_CERTIFICATE_PATH

    def test_error_status_returns_one(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A non-2xx health response exits 1."""
        tls_context = ssl.create_default_context()
        monkeypatch.setattr(ssl, "create_default_context", lambda *, cafile: tls_context)

        def _fake_get(
            url: str,
            *,
            headers: dict[str, str],
            timeout: float,
            verify: ssl.SSLContext,
        ) -> httpx.Response:
            return httpx.Response(503, request=httpx.Request("GET", url))

        monkeypatch.setattr(httpx, "get", _fake_get)
        assert main() == 1

    def test_connection_failure_returns_one(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A connection failure (app not yet ready) exits 1 rather than raising."""
        tls_context = ssl.create_default_context()
        monkeypatch.setattr(ssl, "create_default_context", lambda *, cafile: tls_context)

        def _fake_get(
            url: str,
            *,
            headers: dict[str, str],
            timeout: float,
            verify: ssl.SSLContext,
        ) -> httpx.Response:
            raise httpx.ConnectError("connection refused", request=httpx.Request("GET", url))

        monkeypatch.setattr(httpx, "get", _fake_get)
        assert main() == 1

    def test_missing_certificate_returns_one(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Missing generated trust material reports an unhealthy container."""

        def _missing_certificate(*, cafile: str) -> ssl.SSLContext:
            raise FileNotFoundError(cafile)

        monkeypatch.setattr(ssl, "create_default_context", _missing_certificate)

        assert main() == 1
