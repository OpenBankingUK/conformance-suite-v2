"""Unit tests for :mod:`docker.healthcheck`."""

from __future__ import annotations

import httpx
import pytest

from docker.healthcheck import HEALTHCHECK_HOST_HEADER, HEALTHCHECK_URL, main

pytestmark = pytest.mark.unit


class TestMain:
    """Behaviour of the healthcheck's ``main`` function."""

    def test_healthy_response_returns_zero(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A successful health response exits 0."""

        def _fake_get(url: str, *, headers: dict[str, str], timeout: float) -> httpx.Response:
            assert url == HEALTHCHECK_URL
            assert headers == {"Host": HEALTHCHECK_HOST_HEADER}
            return httpx.Response(200, request=httpx.Request("GET", url))

        monkeypatch.setattr(httpx, "get", _fake_get)
        assert main() == 0

    def test_error_status_returns_one(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A non-2xx health response exits 1."""

        def _fake_get(url: str, *, headers: dict[str, str], timeout: float) -> httpx.Response:
            return httpx.Response(503, request=httpx.Request("GET", url))

        monkeypatch.setattr(httpx, "get", _fake_get)
        assert main() == 1

    def test_connection_failure_returns_one(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A connection failure (app not yet ready) exits 1 rather than raising."""

        def _fake_get(url: str, *, headers: dict[str, str], timeout: float) -> httpx.Response:
            raise httpx.ConnectError("connection refused", request=httpx.Request("GET", url))

        monkeypatch.setattr(httpx, "get", _fake_get)
        assert main() == 1
