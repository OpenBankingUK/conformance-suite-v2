"""Component tests for health, home, and error routing through Django."""

import pytest
from django.conf import settings
from django.test import Client, override_settings

from config.settings import _build_allowed_hosts

pytestmark = pytest.mark.component


def test_home_renders_browser_menu() -> None:
    """The base URL should render the browser main menu."""
    response = Client().get("/")

    assert response.status_code == 200
    content = response.content.decode("utf-8")
    assert "Open Banking conformance suite" in content
    assert "Create new test plan with builder" in content
    assert "Import test plan" in content
    assert "View health" not in content
    assert 'href="/health/"' not in content


def test_health_endpoint_returns_200() -> None:
    client = Client()
    response = client.get("/health/")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_unknown_browser_route_renders_friendly_404() -> None:
    """Unknown browser routes should render the friendly 404 page."""
    response = Client().get("/not-a-real-page/")

    assert response.status_code == 404
    content = response.content.decode("utf-8")
    assert "Page not found" in content
    assert "/not-a-real-page/" in content
    assert "Open main menu" in content


def test_unknown_api_route_returns_json_404() -> None:
    """Unknown API namespace routes should keep the REST JSON error shape."""
    response = Client().get("/api/not-a-real-endpoint/")

    assert response.status_code == 404
    assert response["Content-Type"] == "application/json"
    assert response.json() == {"error": "API endpoint not found"}


def test_home_accepts_legacy_fcs_host_header() -> None:
    """Debug browser runs accept the legacy FCS callback host literal."""
    with override_settings(ALLOWED_HOSTS=_build_allowed_hosts(debug=True)):
        response = Client(HTTP_HOST="0.0.0.0:8443").get("/")

    assert response.status_code == 200
    assert "Open Banking conformance suite" in response.content.decode("utf-8")


def test_health_endpoint_accepts_healthcheck_host_header() -> None:
    """Request with the reserved healthcheck Host header must succeed."""
    client = Client(HTTP_HOST=settings.HEALTHCHECK_HOST)
    response = client.get("/health/")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
