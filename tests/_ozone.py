"""Gating helpers for live-network Ozone integration tests.

Each Ozone integration tier opts in via tier-specific environment variables so
a developer (or CI job) can run lower tiers without provisioning the material
needed by higher tiers. ``requires_ozone(tier)`` returns a ``pytest.mark.skipif``
decorator with a self-documenting reason — tests skip cleanly when the
required variables are absent or malformed, and never silently pass.

See ``ai/plans/2026-05-28-ozone-integration-tiers.md`` for the full tier
definition.
"""

from __future__ import annotations

import os
from typing import Final

import pytest

from conformance.url_validation import HttpsUrlValidationError, validate_https_url

_TIER_ENV_VARS: Final[dict[int, tuple[str, ...]]] = {
    1: ("OZONE_DISCOVERY_URL",),
    2: ("OZONE_DISCOVERY_URL", "OZONE_HEADLESS_PSU_SUPPORTED"),
}
"""Environment variables required by each supported Ozone integration tier."""

_HEADLESS_PSU_SUPPORTED_ENV_VAR: Final[str] = "OZONE_HEADLESS_PSU_SUPPORTED"
"""Environment variable that explicitly opts into the provisional tier 2 PSU run."""

_HEADLESS_PSU_OPEN_INVESTIGATION_REASON: Final[str] = (
    "PRD open-investigation item: headless PSU feasibility against Ozone is not yet confirmed; "
    "set OZONE_HEADLESS_PSU_SUPPORTED=true only for environments known to support it"
)
"""Skip-reason suffix for the provisional headless PSU Ozone test."""


def _skip_reason_for_tier(tier: int) -> str | None:
    """Return a skip reason if the tier's env vars are missing or malformed.

    Args:
        tier: Ozone integration tier number.

    Returns:
        Human-readable skip reason, or ``None`` if all required env vars are
        present and well-formed.

    Raises:
        ValueError: If ``tier`` has no registered env-var requirements.
    """
    try:
        required = _TIER_ENV_VARS[tier]
    except KeyError as error:
        raise ValueError(f"Unsupported Ozone integration tier: {tier}") from error

    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        reason = f"Ozone tier {tier} requires env vars: {', '.join(missing)}"
        return _with_headless_psu_context(reason, tier=tier)

    if tier in {1, 2}:
        discovery_url = os.environ["OZONE_DISCOVERY_URL"]
        try:
            validate_https_url(discovery_url, label="OZONE_DISCOVERY_URL")
        except HttpsUrlValidationError as error:
            return _with_headless_psu_context(
                f"OZONE_DISCOVERY_URL is not a valid HTTPS URL: {error}",
                tier=tier,
            )

    if tier == 2 and os.environ[_HEADLESS_PSU_SUPPORTED_ENV_VAR].strip().lower() != "true":
        return _with_headless_psu_context(
            "OZONE_HEADLESS_PSU_SUPPORTED must be set to true for Ozone tier 2",
            tier=tier,
        )

    return None


def _with_headless_psu_context(reason: str, *, tier: int) -> str:
    """Append the PRD investigation caveat to tier 2 skip reasons.

    Args:
        reason: Base skip reason produced by the tier gate.
        tier: Ozone integration tier number currently being evaluated.

    Returns:
        The original reason for non-tier-2 checks, or the reason with the
        headless PSU PRD open-investigation caveat appended for tier 2.
    """
    if tier != 2:
        return reason
    return f"{reason}. {_HEADLESS_PSU_OPEN_INVESTIGATION_REASON}"


def requires_ozone(tier: int) -> pytest.MarkDecorator:
    """Return a ``skipif`` marker that gates a test on Ozone tier env vars.

    Args:
        tier: Ozone integration tier number.

    Returns:
        A pytest skip marker that skips the test when the tier's env vars are
        missing or malformed, carrying a reason that names the missing or
        invalid variable.
    """
    reason = _skip_reason_for_tier(tier)
    return pytest.mark.skipif(reason is not None, reason=reason or f"Ozone tier {tier} env vars satisfied")
