import pytest
from django.conf import settings
from django.test import Client


@pytest.mark.integration
def test_health_endpoint_returns_200() -> None:
    client = Client()
    response = client.get("/health/")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


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


@pytest.mark.integration
def test_health_endpoint_accepts_healthcheck_host_header() -> None:
    """Request with the reserved healthcheck Host header must succeed."""
    client = Client(HTTP_HOST=settings.HEALTHCHECK_HOST)
    response = client.get("/health/")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
