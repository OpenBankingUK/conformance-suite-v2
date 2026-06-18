"""Reusable safety labels for bundled starter suites.

This module holds non-secret starter metadata that can be shared by future
suite manifests, catalog entries, and plan-preview helpers without duplicating
the safety boundary text. The labels are deliberately conservative: starter
flows remain partial coverage, model-bank / sandbox scoped, and do not claim
live-payment or full-certification support.
"""

from __future__ import annotations

from dataclasses import dataclass

from conformance.manifest import CertificationCoverage


class StarterSafetyError(ValueError):
    """Raised when starter-suite safety metadata is inconsistent or unsafe."""


@dataclass(frozen=True)
class StarterSafetyMetadata:
    """Non-secret safety labels for a starter suite.

    Attributes:
        certification_coverage: Manifest-level coverage boundary that the
            starter must advertise.
        environment_scope: Human-readable deployment scope label for the
            starter, such as ``sandbox/model-bank-only``.
        default_test_value_profile: Human-readable label describing the safe
            default test-value profile used by the starter.
    """

    certification_coverage: CertificationCoverage
    environment_scope: str
    default_test_value_profile: str


PIS_DOMESTIC_PAYMENT_STARTER_SAFETY = StarterSafetyMetadata(
    certification_coverage="partial",
    environment_scope="sandbox/model-bank-only",
    default_test_value_profile="Ozone-demo/synthetic",
)
"""Safety metadata for the future PIS domestic-payment starter.

The starter is intentionally partial, scoped to model-bank or sandbox test
accounts, and designed around Ozone-demo or synthetic default values. It must
not be presented as live-payment coverage or full certification coverage.
"""


def validate_starter_safety(metadata: StarterSafetyMetadata) -> None:
    """Validate that starter metadata stays on the safe, partial boundary.

    Args:
        metadata: Starter safety metadata to validate.

    Raises:
        StarterSafetyError: If the starter claims full coverage or leaves the
            sandbox/model-bank-only boundary.
    """
    if metadata.certification_coverage != "partial":
        raise StarterSafetyError("Starter suites must remain partial certification coverage")
    if metadata.environment_scope != "sandbox/model-bank-only":
        raise StarterSafetyError("Starter suites must remain sandbox/model-bank-only")
    if metadata.default_test_value_profile != "Ozone-demo/synthetic":
        raise StarterSafetyError("Starter suites must use Ozone-demo/synthetic default values")


def format_starter_safety_note(metadata: StarterSafetyMetadata) -> str:
    """Render a human-readable note describing a starter suite's safety scope.

    Args:
        metadata: Starter safety metadata to render.

    Returns:
        A short note that can be used in catalog descriptions and docs.
    """
    validate_starter_safety(metadata)
    return (
        f"{metadata.environment_scope} starter with {metadata.default_test_value_profile} "
        f"defaults and {metadata.certification_coverage} certification coverage; "
        "not for live-payment or full-certification claims."
    )
