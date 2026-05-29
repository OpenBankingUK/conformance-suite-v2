"""Shared fixtures for live-network Ozone integration tests.

Test modules here run against real Ozone infrastructure and are gated behind
tier-specific environment variables via ``tests/_ozone.py``. Presence and
well-formedness checks for those variables live in the ``requires_ozone``
marker, which both decides whether to skip and surfaces the reason in pytest
output. The fixtures in this module are deliberately thin env-var readers
that assume the marker has already gated the test; they do not re-validate.
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
