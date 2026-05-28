"""Shared fixtures for live-network Ozone integration tests.

Test modules here run against real Ozone infrastructure and are gated behind
tier-specific environment variables via ``tests/_ozone.py``. The fixtures in
this module only expose validated env-var values to tests; the skip decision
itself is owned by the ``requires_ozone`` marker so the skip reason stays
visible in pytest output.
"""

from __future__ import annotations

import os

import pytest


@pytest.fixture
def ozone_discovery_url() -> str:
    """Return the Ozone OpenID discovery URL from ``OZONE_DISCOVERY_URL``.

    Tests that consume this fixture must also apply ``requires_ozone(1)`` so
    that the test is skipped (rather than erroring) when the variable is not
    set in the current environment.

    Returns:
        The absolute HTTPS discovery URL configured in the environment.
    """
    return os.environ["OZONE_DISCOVERY_URL"]
