import pytest
from django.conf import settings
from django.test import Client, override_settings

from config.settings import LEGACY_FCS_CALLBACK_HOST, _build_allowed_hosts


@pytest.mark.integration
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


@pytest.mark.integration
def test_health_endpoint_returns_200() -> None:
    client = Client()
    response = client.get("/health/")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.unit
def test_default_session_backend_does_not_require_database_migrations() -> None:
    """Browser wizard drafts should work before a local SQLite session table exists."""
    assert settings.SESSION_ENGINE == "django.contrib.sessions.backends.file"


@pytest.mark.integration
def test_unknown_browser_route_renders_friendly_404() -> None:
    """Unknown browser routes should render the friendly 404 page."""
    response = Client().get("/not-a-real-page/")

    assert response.status_code == 404
    content = response.content.decode("utf-8")
    assert "Page not found" in content
    assert "/not-a-real-page/" in content
    assert "Open main menu" in content


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
    assert LEGACY_FCS_CALLBACK_HOST in allowed_hosts


@pytest.mark.integration
def test_home_accepts_legacy_fcs_host_header() -> None:
    """Debug browser runs accept the legacy FCS callback host literal."""
    with override_settings(ALLOWED_HOSTS=_build_allowed_hosts(debug=True)):
        response = Client(HTTP_HOST="0.0.0.0:8443").get("/")

    assert response.status_code == 200
    assert "Open Banking conformance suite" in response.content.decode("utf-8")


@pytest.mark.integration
def test_health_endpoint_accepts_healthcheck_host_header() -> None:
    """Request with the reserved healthcheck Host header must succeed."""
    client = Client(HTTP_HOST=settings.HEALTHCHECK_HOST)
    response = client.get("/health/")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
