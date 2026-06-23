"""Unit tests for exploratory-run labelling and acknowledgement gating."""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest

from conformance.api.plan_builder import _is_exploratory_run, build_plan_preview
from conformance.json_types import JsonValue
from conformance.manifest import load_manifest_from_object
from conformance.model_bank_config import parse_model_bank_config
from conformance.run_plan import RunPlan, RunPlanSuiteCoordinates, RunPlanTestValues

_VALID_CONFIG: dict[str, JsonValue] = {
    "environment": "test-env",
    "discoveryUrl": "https://example.com/.well-known/openid-configuration",
}


def _build_test_run_plan(*, profile: str | None, custom_values: dict[str, str]) -> RunPlan:
    """Build a minimal Run Plan for exploratory helper tests.

    Args:
        profile: Selected test-value profile id, or ``None`` for default.
        custom_values: Custom test-value override mapping.

    Returns:
        Run Plan instance suitable for ``_is_exploratory_run`` assertions.
    """
    return RunPlan(
        schema_version="1",
        suite=RunPlanSuiteCoordinates(id="suite-id", version="v1", manifest_hash="manifest-hash"),
        selected_step_ids=("mandatory",),
        test_values=RunPlanTestValues(profile=profile, custom_values=custom_values),
    )


def _build_manifest() -> dict[str, JsonValue]:
    """Return a minimal v1 manifest object for preview tests.

    Returns:
        Manifest JSON object with one mandatory step.
    """
    return {
        "schemaVersion": "v1",
        "name": "Exploratory ack manifest",
        "steps": cast(
            list[JsonValue],
            [
                {
                    "id": "mandatory",
                    "name": "Mandatory step",
                    "mandatory": True,
                    "request": {"method": "GET", "url": "https://example.com/mandatory"},
                    "assertions": [{"type": "http_status", "expected": 200}],
                }
            ],
        ),
    }


@pytest.mark.unit
def test_is_exploratory_run_false_for_default_profile_without_custom_values() -> None:
    run_plan = _build_test_run_plan(profile=None, custom_values={})
    assert _is_exploratory_run(run_plan) is False


@pytest.mark.unit
def test_is_exploratory_run_true_for_non_default_profile() -> None:
    run_plan = _build_test_run_plan(profile="sandbox", custom_values={})
    assert _is_exploratory_run(run_plan) is True


@pytest.mark.unit
def test_is_exploratory_run_true_for_custom_test_values() -> None:
    run_plan = _build_test_run_plan(profile=None, custom_values={"paymentId": "pid-123"})
    assert _is_exploratory_run(run_plan) is True


@pytest.mark.unit
def test_is_exploratory_run_true_for_profile_and_custom_values() -> None:
    run_plan = _build_test_run_plan(profile="sandbox", custom_values={"paymentId": "pid-123"})
    assert _is_exploratory_run(run_plan) is True


@pytest.mark.unit
def test_build_plan_preview_marks_exploratory_and_disables_certification_eligibility() -> None:
    config = parse_model_bank_config(_VALID_CONFIG, base_dir=Path.cwd(), output_base_dir=Path.cwd())
    manifest = load_manifest_from_object(_build_manifest())

    preview = build_plan_preview(
        config=config,
        manifest=manifest,
        selection_mode="select",
        selected_step_ids=["mandatory"],
        test_value_profile="sandbox",
        exploratory_ack=True,
    )

    assert preview.is_exploratory_run is True
    assert preview.exploratory_ack_required is True
    assert preview.certification_eligible_by_selection is False


@pytest.mark.unit
def test_build_plan_preview_adds_ack_launch_blocker_when_unchecked() -> None:
    config = parse_model_bank_config(_VALID_CONFIG, base_dir=Path.cwd(), output_base_dir=Path.cwd())
    manifest = load_manifest_from_object(_build_manifest())

    preview = build_plan_preview(
        config=config,
        manifest=manifest,
        selection_mode="select",
        selected_step_ids=["mandatory"],
        test_value_profile="sandbox",
        exploratory_ack=False,
    )

    assert (
        "Exploratory Run acknowledgement required — check the acknowledgement box to launch." in preview.launch_blockers
    )


@pytest.mark.unit
def test_build_plan_preview_omits_ack_launch_blocker_when_checked() -> None:
    config = parse_model_bank_config(_VALID_CONFIG, base_dir=Path.cwd(), output_base_dir=Path.cwd())
    manifest = load_manifest_from_object(_build_manifest())

    preview = build_plan_preview(
        config=config,
        manifest=manifest,
        selection_mode="select",
        selected_step_ids=["mandatory"],
        test_value_profile="sandbox",
        exploratory_ack=True,
    )

    assert (
        "Exploratory Run acknowledgement required — check the acknowledgement box to launch."
        not in preview.launch_blockers
    )
