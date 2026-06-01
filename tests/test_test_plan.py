"""Unit tests for :mod:`conformance.test_plan`."""

from __future__ import annotations

import pytest

from conformance.json_types import JsonValue
from conformance.manifest import ManifestError, parse_manifest
from conformance.test_plan import TestPlan, TestPlanEntry


def _v1_manifest(steps: list[dict[str, JsonValue]]) -> dict[str, JsonValue]:
    """Build a minimal v1 manifest dict with the given step overrides.

    Each entry in ``steps`` is merged onto a default step shape so tests
    only need to set the fields they care about (``id``, ``mandatory``,
    ``optional``).

    Args:
        steps: Per-step overrides applied to a shared default skeleton.

    Returns:
        A v1 manifest JSON object ready to feed to ``parse_manifest``.
    """
    rendered: list[JsonValue] = []
    for index, overrides in enumerate(steps):
        base: dict[str, JsonValue] = {
            "id": f"step-{index}",
            "name": f"Step {index}",
            "request": {
                "method": "GET",
                "url": "https://example.com/endpoint",
            },
            "assertions": [{"type": "http_status", "expected": 200}],
        }
        base.update(overrides)
        rendered.append(base)
    return {"schemaVersion": "v1", "name": "test", "steps": rendered}


@pytest.mark.unit
def test_default_plan_selects_mandatory_and_non_optional() -> None:
    """Default plan: mandatory and unflagged steps are selected; optional is not."""
    manifest = parse_manifest(
        _v1_manifest(
            [
                {"id": "m", "mandatory": True},
                {"id": "n"},
                {"id": "o", "optional": True},
            ]
        )
    )

    plan = TestPlan.default_plan_from_manifest(manifest)

    assert plan.selected_step_ids() == ["m", "n"]
    assert plan.deselected_step_ids() == ["o"]
    assert plan.deselected_mandatory_step_ids() == []


@pytest.mark.unit
def test_default_plan_for_v0_manifest_is_empty() -> None:
    """v0 manifests have no plan model; default plan is empty."""
    manifest = parse_manifest(
        {
            "schemaVersion": "v0",
            "name": "v0",
            "tests": [
                {
                    "id": "t",
                    "name": "T",
                    "request": {"method": "GET", "url": "https://example.com/x"},
                    "assertions": [{"type": "http_status", "expected": 200}],
                }
            ],
        }
    )

    plan = TestPlan.default_plan_from_manifest(manifest)

    assert plan.entries == ()
    assert plan.selected_step_ids() == []


@pytest.mark.unit
def test_with_deselection_returns_new_plan_unchanged_original() -> None:
    """Deselection returns a new plan and leaves the original untouched."""
    manifest = parse_manifest(_v1_manifest([{"id": "a"}, {"id": "b"}]))
    plan = TestPlan.default_plan_from_manifest(manifest)

    narrowed = plan.with_deselection(["a"])

    assert plan.selected_step_ids() == ["a", "b"]
    assert narrowed.selected_step_ids() == ["b"]
    assert narrowed.deselected_step_ids() == ["a"]


@pytest.mark.unit
def test_with_deselection_is_idempotent() -> None:
    """Deselecting an already-deselected step is a no-op."""
    manifest = parse_manifest(_v1_manifest([{"id": "a"}, {"id": "b"}]))
    plan = TestPlan.default_plan_from_manifest(manifest).with_deselection(["a"])

    twice = plan.with_deselection(["a"])

    assert twice.selected_step_ids() == ["b"]


@pytest.mark.unit
def test_with_deselection_rejects_unknown_step_id() -> None:
    """An unknown step id raises ValueError with a helpful message."""
    manifest = parse_manifest(_v1_manifest([{"id": "a"}]))
    plan = TestPlan.default_plan_from_manifest(manifest)

    with pytest.raises(ValueError, match="ghost"):
        plan.with_deselection(["ghost"])


@pytest.mark.unit
def test_with_deselection_can_deselect_mandatory_step() -> None:
    """Mandatory steps may be deselected; the plan records the fact."""
    manifest = parse_manifest(_v1_manifest([{"id": "m", "mandatory": True}, {"id": "o"}]))
    plan = TestPlan.default_plan_from_manifest(manifest).with_deselection(["m"])

    assert plan.deselected_mandatory_step_ids() == ["m"]
    assert plan.selected_step_ids() == ["o"]


@pytest.mark.unit
def test_mandatory_and_optional_both_true_rejected_at_parse_time() -> None:
    """Mandatory and optional are mutually exclusive (PRD)."""
    with pytest.raises(ManifestError, match="mandatory.*optional"):
        parse_manifest(_v1_manifest([{"id": "x", "mandatory": True, "optional": True}]))


@pytest.mark.unit
def test_optional_must_be_boolean() -> None:
    """Truthy ints are rejected for ``optional``, matching the ``mandatory`` rule."""
    with pytest.raises(ManifestError, match="optional must be a JSON boolean"):
        parse_manifest(_v1_manifest([{"id": "x", "optional": 1}]))


@pytest.mark.unit
def test_is_eligible_by_selection_requires_mandatory_present_and_selected() -> None:
    """A plan with no mandatory step, or with any mandatory deselected, is ineligible by selection."""
    no_mandatory = TestPlan.default_plan_from_manifest(parse_manifest(_v1_manifest([{"id": "a"}])))
    assert no_mandatory.is_eligible_by_selection() is False

    all_mandatory_selected = TestPlan.default_plan_from_manifest(
        parse_manifest(_v1_manifest([{"id": "m", "mandatory": True}]))
    )
    assert all_mandatory_selected.is_eligible_by_selection() is True

    mandatory_deselected = all_mandatory_selected.with_deselection(["m"])
    assert mandatory_deselected.is_eligible_by_selection() is False


@pytest.mark.unit
def test_summary_counts() -> None:
    """``summary`` returns the stable counts the report's ``plan`` block consumes."""
    manifest = parse_manifest(
        _v1_manifest(
            [
                {"id": "m1", "mandatory": True},
                {"id": "m2", "mandatory": True},
                {"id": "n"},
                {"id": "o", "optional": True},
            ]
        )
    )
    plan = TestPlan.default_plan_from_manifest(manifest).with_deselection(["m1"])

    assert plan.summary() == {
        "totalSteps": 4,
        "selectedSteps": 2,
        "deselectedSteps": 2,
        "mandatorySelected": 1,
        "mandatoryDeselected": 1,
    }


@pytest.mark.unit
def test_test_plan_entry_is_immutable() -> None:
    """``TestPlanEntry`` is frozen and cannot be mutated after construction."""
    entry = TestPlanEntry(step_id="x", mandatory=True, optional=False, selected=True)
    with pytest.raises(AttributeError):
        entry.selected = False  # type: ignore[misc]  # frozen dataclass — assignment must fail
