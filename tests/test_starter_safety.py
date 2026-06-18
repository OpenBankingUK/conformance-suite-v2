"""Unit tests for reusable starter-suite safety metadata."""

from __future__ import annotations

import pytest

from conformance.starter_safety import (
    PIS_DOMESTIC_PAYMENT_STARTER_SAFETY,
    StarterSafetyError,
    StarterSafetyMetadata,
    format_starter_safety_note,
    validate_starter_safety,
)


@pytest.mark.unit
def test_pis_domestic_payment_starter_safety_is_partial_and_sandbox_only() -> None:
    """Future PIS payment starter metadata should stay conservative."""
    validate_starter_safety(PIS_DOMESTIC_PAYMENT_STARTER_SAFETY)

    assert PIS_DOMESTIC_PAYMENT_STARTER_SAFETY.certification_coverage == "partial"
    assert PIS_DOMESTIC_PAYMENT_STARTER_SAFETY.environment_scope == "sandbox/model-bank-only"
    assert PIS_DOMESTIC_PAYMENT_STARTER_SAFETY.default_test_value_profile == "Ozone-demo/synthetic"

    note = format_starter_safety_note(PIS_DOMESTIC_PAYMENT_STARTER_SAFETY)
    assert "sandbox/model-bank-only" in note
    assert "Ozone-demo/synthetic" in note
    assert "partial certification coverage" in note
    assert "live-payment" in note
    assert "full-certification" in note


@pytest.mark.unit
def test_validate_starter_safety_rejects_unsafe_coverage_labels() -> None:
    """Unsafe starter metadata should be rejected before it can be reused."""
    with pytest.raises(StarterSafetyError, match="partial certification coverage"):
        validate_starter_safety(
            StarterSafetyMetadata(
                certification_coverage="complete",
                environment_scope="sandbox/model-bank-only",
                default_test_value_profile="Ozone-demo/synthetic",
            )
        )
