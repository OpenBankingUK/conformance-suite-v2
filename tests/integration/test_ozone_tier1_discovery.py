"""Tier 1 Ozone integration: OpenID discovery via the v1 manifest engine.

These tests exercise the v1 manifest execution path against a real Ozone
OpenID discovery URL. They are gated on the ``OZONE_DISCOVERY_URL`` environment
variable and skip cleanly when it is absent or malformed.

Assertions are deliberately environment-agnostic: we check that discovery
returns HTTP 200 with the expected shape, but do not pin issuer values because
those are environment-specific.

The plan (``ai/plans/2026-05-28-ozone-integration-tiers.md``) anticipated also
exercising the JWKS follow-up here. In practice, Ozone advertises a JWKS URI on
``keystore.openbankingtest.org.uk`` whose certificate chain requires the Open
Banking root CA bundle — wiring that bundle is part of tier 2 (alongside mTLS).
Tier 1 therefore stops at discovery; the JWKS follow-up moves to tier 2.

The second test pins DL-0011 (status-agnostic HTTP fetches) against real
network conditions by pointing the engine at a deliberately invalid path under
the Ozone host and confirming that the engine records a failed step without
raising an exception.
"""

from __future__ import annotations

from urllib.parse import urlparse

import httpx
import pytest

from conformance.executor import run_manifest
from conformance.json_types import JsonValue
from conformance.manifest import parse_manifest
from tests._ozone import requires_ozone

_OZONE_TIER1 = requires_ozone(1)
"""Skip marker applied to every tier 1 test in this module."""

_INTEGRATION_HTTP_TIMEOUT_SECONDS = 30.0
"""Per-request HTTP timeout for tier 1 integration calls (generous for sandbox latency)."""


def _v1_discovery_manifest(discovery_url: str) -> dict[str, JsonValue]:
    """Build a v1 manifest dict for a discovery-only smoke flow.

    Mirrors the discovery step of ``config/manifest-v1-openid-jwks-example.json``
    but parameterises the URL so it can be sourced from environment
    configuration rather than a hardcoded value.

    Args:
        discovery_url: Absolute HTTPS URL of the OpenID discovery endpoint.

    Returns:
        Raw v1 manifest dict suitable for ``parse_manifest``.
    """
    return {
        "schemaVersion": "v1",
        "name": "Ozone tier 1 OpenID discovery",
        "steps": [
            {
                "id": "openid-discovery",
                "name": "OpenID discovery document",
                "request": {"method": "GET", "url": discovery_url},
                "assertions": [
                    {"type": "http_status", "expected": 200},
                    {"type": "json_field", "path": "issuer", "rule": "https_url"},
                    {"type": "json_field", "path": "jwks_uri", "rule": "https_url"},
                ],
            }
        ],
    }


@pytest.mark.ozone
@_OZONE_TIER1
def test_ozone_tier1_discovery(ozone_discovery_url: str) -> None:
    manifest = parse_manifest(_v1_discovery_manifest(ozone_discovery_url))
    with httpx.Client(timeout=_INTEGRATION_HTTP_TIMEOUT_SECONDS) as client:
        result = run_manifest(manifest, environment="ozone-tier1", client=client)

    assert result.status == "passed", result.to_json_object()
    assert len(result.steps) == 1
    (discovery_step,) = result.steps
    assert discovery_step.status == "passed"
    assert discovery_step.status_code == 200


@pytest.mark.ozone
@_OZONE_TIER1
def test_ozone_tier1_invalid_path_is_recorded_not_raised(ozone_discovery_url: str) -> None:
    """A 4xx on the Ozone host must produce a failed step, not propagate an exception (DL-0011)."""
    parsed = urlparse(ozone_discovery_url)
    # Path is intentionally implausible so the Ozone host returns a client error
    # rather than accidentally hitting a real endpoint.
    invalid_url = f"{parsed.scheme}://{parsed.netloc}/__conformance_suite_invalid_path__"
    manifest_dict: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "Ozone tier 1 negative path",
        "steps": [
            {
                "id": "invalid-path",
                "name": "Deliberately invalid path",
                "request": {"method": "GET", "url": invalid_url},
                "assertions": [{"type": "http_status", "expected": 200}],
            }
        ],
    }
    manifest = parse_manifest(manifest_dict)
    # The success criterion is that this call does not raise; if it does, pytest
    # reports the exception and fails the test naturally.
    with httpx.Client(timeout=_INTEGRATION_HTTP_TIMEOUT_SECONDS) as client:
        result = run_manifest(manifest, environment="ozone-tier1", client=client)

    assert result.status == "failed"
    assert len(result.steps) == 1
    step = result.steps[0]
    assert step.status == "failed"
    assert step.url == invalid_url
