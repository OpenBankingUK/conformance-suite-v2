"""Unit tests for Django settings that shape the served web surface."""

import pytest
from django.conf import settings

from config.settings import LEGACY_FCS_CALLBACK_HOST, _build_allowed_hosts

pytestmark = pytest.mark.unit


def test_default_session_backend_does_not_require_database_migrations() -> None:
    """Browser wizard drafts should work before a local SQLite session table exists."""
    assert settings.SESSION_ENGINE == "django.contrib.sessions.backends.file"


def test_healthcheck_host_is_always_allowed() -> None:
    """The reserved container healthcheck host must always be in ALLOWED_HOSTS.

    The Dockerfile HEALTHCHECK sends ``Host: healthcheck.local`` so the probe
    succeeds regardless of operator-supplied ``DJANGO_ALLOWED_HOSTS``. Django
    must accept that host or it returns ``400 DisallowedHost`` and the
    container is incorrectly marked unhealthy.
    """
    assert settings.HEALTHCHECK_HOST == "healthcheck.local"
    assert settings.HEALTHCHECK_HOST in settings.ALLOWED_HOSTS


def test_debug_settings_allow_localhost() -> None:
    """Debug runs must accept browser requests to local development hosts."""
    allowed_hosts = _build_allowed_hosts(debug=True)

    assert "localhost" in allowed_hosts
    assert "127.0.0.1" in allowed_hosts
    assert LEGACY_FCS_CALLBACK_HOST in allowed_hosts
