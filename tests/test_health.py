import pytest
from django.conf import settings
from django.test import Client

from config.settings import _build_allowed_hosts


@pytest.mark.integration
def test_home_redirects_to_plan_builder() -> None:
    """The base URL should land browser users on the plan builder."""
    response = Client().get("/")

    assert response.status_code == 302
    assert response["Location"] == "/plan/"


@pytest.mark.integration
def test_health_endpoint_returns_200() -> None:
    client = Client()
    response = client.get("/health/")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.integration
def test_unknown_browser_route_renders_friendly_404() -> None:
    """Unknown browser routes should render the friendly 404 page."""
    response = Client().get("/not-a-real-page/")

    assert response.status_code == 404
    content = response.content.decode("utf-8")
    assert "Page not found" in content
    assert "/not-a-real-page/" in content
    assert "Open plan builder" in content


@pytest.mark.integration
def test_unknown_api_route_returns_json_404() -> None:
    """Unknown API namespace routes should keep the REST JSON error shape."""
    response = Client().get("/api/not-a-real-endpoint/")

    assert response.status_code == 404
    assert response["Content-Type"] == "application/json"
    assert response.json() == {"error": "API endpoint not found"}


@pytest.mark.unit
def test_healthcheck_host_is_always_allowed() -> None:
    """The reserved container healthcheck host must always be in ALLOWED_HOSTS.

    The Dockerfile HEALTHCHECK sends ``Host: healthcheck.local`` so the probe
    succeeds regardless of operator-supplied ``DJANGO_ALLOWED_HOSTS``. Django
    must accept that host or it returns ``400 DisallowedHost`` and the
    container is incorrectly marked unhealthy.
    """
    assert settings.HEALTHCHECK_HOST == "healthcheck.local"
    assert settings.HEALTHCHECK_HOST in settings.ALLOWED_HOSTS


@pytest.mark.unit
def test_debug_settings_allow_localhost() -> None:
    """Debug runs must accept browser requests to local development hosts."""
    allowed_hosts = _build_allowed_hosts(debug=True)

    assert "localhost" in allowed_hosts
    assert "127.0.0.1" in allowed_hosts


@pytest.mark.integration
def test_health_endpoint_accepts_healthcheck_host_header() -> None:
    """Request with the reserved healthcheck Host header must succeed."""
    client = Client(HTTP_HOST=settings.HEALTHCHECK_HOST)
    response = client.get("/health/")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
