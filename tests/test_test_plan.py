"""Unit tests for :mod:`conformance.test_plan`."""

from __future__ import annotations

from typing import cast

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
        "conditionalSelected": 0,
        "conditionalDeselectedMissingValues": 0,
    }


@pytest.mark.unit
def test_test_plan_entry_is_immutable() -> None:
    """``TestPlanEntry`` is frozen and cannot be mutated after construction."""
    entry = TestPlanEntry(step_id="x", mandatory=True, optional=False, selected=True)
    with pytest.raises(AttributeError):
        entry.selected = False  # type: ignore[misc]  # frozen dataclass — assignment must fail


# ---------------------------------------------------------------------------
# Conditional plan semantics
# ---------------------------------------------------------------------------


def _manifest_with_conditional_step(required_keys: list[str] | None = None) -> dict[str, JsonValue]:
    """Build a v1 manifest with a conditional step and a test-value profile.

    Args:
        required_keys: Keys to put in ``selectionMetadata.requiredTestValueKeys``.
            Defaults to ``["paymentId"]``.

    Returns:
        A v1 manifest JSON object ready to feed to ``parse_manifest``.
    """
    keys: list[str] = required_keys if required_keys is not None else ["paymentId"]
    return {
        "schemaVersion": "v1",
        "name": "Conditional",
        "testValueProfiles": {
            "defaultProfileId": "sandbox",
            "profiles": [
                {
                    "id": "sandbox",
                    "label": "Sandbox",
                    "values": {"paymentId": "pmnt-001"},
                },
            ],
            "allowedOverrideKeys": ["paymentId"],
            "nonSecretKeys": ["paymentId"],
        },
        "steps": [
            {
                "id": "unconditional",
                "name": "Always selected",
                "request": {"method": "GET", "url": "https://example.com/x"},
                "assertions": [{"type": "http_status", "expected": 200}],
            },
            {
                "id": "cond",
                "name": "Conditional step",
                "request": {"method": "GET", "url": "https://example.com/${testValues.paymentId}"},
                "assertions": [{"type": "http_status", "expected": 200}],
                "selectionMetadata": {
                    "conditionId": "payment-available",
                    "conditionLabel": "Payment feature available",
                    "conditional": True,
                    "requiredTestValueKeys": cast(JsonValue, keys),
                },
            },
        ],
    }


@pytest.mark.unit
def test_conditional_step_selected_when_required_values_in_default_profile() -> None:
    """Conditional step is auto-selected when the default profile supplies all required keys."""
    from conformance.test_plan import build_plan_test_value_context

    manifest = parse_manifest(_manifest_with_conditional_step())
    ctx = build_plan_test_value_context(manifest, config_test_values=None)
    plan = TestPlan.default_plan_from_manifest(manifest, test_value_context=ctx)

    assert "cond" in plan.selected_step_ids()
    cond_entry = next(e for e in plan.entries if e.step_id == "cond")
    assert cond_entry.conditional is True
    assert cond_entry.condition_id == "payment-available"
    assert cond_entry.condition_label == "Payment feature available"
    assert cond_entry.required_test_value_keys == ("paymentId",)
    assert cond_entry.missing_test_value_keys == ()
    assert cond_entry.test_value_profile_id == "sandbox"
    assert cond_entry.test_value_profile_source == "default"
    assert cond_entry.test_value_override_keys == ()


@pytest.mark.unit
def test_conditional_step_deselected_when_required_values_missing() -> None:
    """Conditional step is deselected when the effective profile has no value for a required key."""
    from conformance.test_plan import PlanTestValueContext

    manifest = parse_manifest(
        {
            "schemaVersion": "v1",
            "name": "Missing",
            "testValueProfiles": {
                "defaultProfileId": "sandbox",
                "profiles": [{"id": "sandbox", "label": "Sandbox", "values": {"paymentId": "pmnt-001"}}],
                "allowedOverrideKeys": [],
                "nonSecretKeys": ["paymentId"],
            },
            "steps": [
                {
                    "id": "cond",
                    "name": "Cond",
                    "request": {"method": "GET", "url": "https://example.com/${testValues.paymentId}"},
                    "assertions": [{"type": "http_status", "expected": 200}],
                    "selectionMetadata": {
                        "conditional": True,
                        "requiredTestValueKeys": ["paymentId"],
                    },
                }
            ],
        }
    )
    # Simulate a context that has no resolved values (e.g. profile failed to load)
    no_values_ctx = PlanTestValueContext(
        effective_values={},
        profile_id="sandbox",
        profile_source="default",
        override_keys=frozenset(),
    )
    plan = TestPlan.default_plan_from_manifest(manifest, test_value_context=no_values_ctx)

    cond_entry = next(e for e in plan.entries if e.step_id == "cond")
    assert cond_entry.selected is False
    assert "paymentId" in cond_entry.missing_test_value_keys


@pytest.mark.unit
def test_conditional_step_deselected_without_context() -> None:
    """Conditional step is deselected when no test_value_context is provided (backward compat)."""
    manifest = parse_manifest(
        {
            "schemaVersion": "v1",
            "name": "No ctx",
            "testValueProfiles": {
                "defaultProfileId": "sandbox",
                "profiles": [{"id": "sandbox", "label": "Sandbox", "values": {"paymentId": "pmnt-001"}}],
                "allowedOverrideKeys": [],
                "nonSecretKeys": ["paymentId"],
            },
            "steps": [
                {
                    "id": "cond",
                    "name": "Cond",
                    "request": {"method": "GET", "url": "https://example.com/${testValues.paymentId}"},
                    "assertions": [{"type": "http_status", "expected": 200}],
                    "selectionMetadata": {
                        "conditional": True,
                        "requiredTestValueKeys": ["paymentId"],
                    },
                }
            ],
        }
    )
    # No test_value_context → conditional step deselected, missing keys populated
    plan = TestPlan.default_plan_from_manifest(manifest)

    cond_entry = next(e for e in plan.entries if e.step_id == "cond")
    assert cond_entry.selected is False
    assert "paymentId" in cond_entry.missing_test_value_keys


@pytest.mark.unit
def test_conditional_step_selected_after_participant_override() -> None:
    """Conditional step is auto-selected when participant overrides supply all required values."""
    from pathlib import Path

    from conformance.model_bank_config import parse_model_bank_config
    from conformance.test_plan import build_plan_test_value_context

    manifest = parse_manifest(_manifest_with_conditional_step())
    config = parse_model_bank_config(
        {
            "environment": "sandbox",
            "discoveryUrl": "https://example.com/.well-known/openid-configuration",
            "testValues": {"overrides": {"paymentId": "custom-pmnt-999"}},
        },
        base_dir=Path.cwd(),
        output_base_dir=Path.cwd(),
    )
    ctx = build_plan_test_value_context(manifest, config_test_values=config.test_values)
    plan = TestPlan.default_plan_from_manifest(manifest, test_value_context=ctx)

    assert "cond" in plan.selected_step_ids()
    cond_entry = next(e for e in plan.entries if e.step_id == "cond")
    assert cond_entry.test_value_profile_source == "overridden"
    assert "paymentId" in cond_entry.test_value_override_keys
    assert cond_entry.missing_test_value_keys == ()


@pytest.mark.unit
def test_optional_step_stays_deselected_regardless_of_test_values() -> None:
    """Existing optional-deselection semantics are preserved even with test-value context."""
    from conformance.test_plan import PlanTestValueContext

    manifest = parse_manifest(_v1_manifest([{"id": "m", "mandatory": True}, {"id": "o", "optional": True}]))
    ctx = PlanTestValueContext(
        effective_values={"paymentId": "pmnt-001"},
        profile_id="sandbox",
        profile_source="default",
        override_keys=frozenset(),
    )
    plan = TestPlan.default_plan_from_manifest(manifest, test_value_context=ctx)

    assert "m" in plan.selected_step_ids()
    assert "o" not in plan.selected_step_ids()


@pytest.mark.unit
def test_unconditional_steps_unaffected_by_test_value_context() -> None:
    """Steps without selectionMetadata are unaffected by the presence of a test-value context."""
    from conformance.test_plan import PlanTestValueContext

    manifest = parse_manifest(_v1_manifest([{"id": "a"}, {"id": "b", "mandatory": True}]))
    ctx = PlanTestValueContext(
        effective_values={"someKey": "val"},
        profile_id="sandbox",
        profile_source="default",
        override_keys=frozenset(),
    )
    plan = TestPlan.default_plan_from_manifest(manifest, test_value_context=ctx)

    assert plan.selected_step_ids() == ["a", "b"]
    for entry in plan.entries:
        assert entry.conditional is False
        assert entry.missing_test_value_keys == ()


@pytest.mark.unit
def test_backward_compatible_plan_has_no_conditional_fields() -> None:
    """Old manifests without selectionMetadata produce entries with zero-valued conditional fields."""
    manifest = parse_manifest(_v1_manifest([{"id": "a"}, {"id": "b", "mandatory": True}]))
    plan = TestPlan.default_plan_from_manifest(manifest)

    for entry in plan.entries:
        assert entry.conditional is False
        assert entry.condition_id is None
        assert entry.condition_label is None
        assert entry.required_test_value_keys == ()
        assert entry.missing_test_value_keys == ()
        assert entry.test_value_profile_id is None
        assert entry.test_value_profile_source is None
        assert entry.test_value_override_keys == ()


@pytest.mark.unit
def test_summary_includes_conditional_counts() -> None:
    """``summary`` includes ``conditionalSelected`` and ``conditionalDeselectedMissingValues``."""
    from conformance.test_plan import PlanTestValueContext

    manifest = parse_manifest(
        {
            "schemaVersion": "v1",
            "name": "Summary",
            "testValueProfiles": {
                "defaultProfileId": "sandbox",
                "profiles": [{"id": "sandbox", "label": "Sandbox", "values": {"paymentId": "pmnt-001"}}],
                "allowedOverrideKeys": [],
                "nonSecretKeys": ["paymentId"],
            },
            "steps": [
                {
                    "id": "cond-ok",
                    "name": "Conditional with value",
                    "request": {"method": "GET", "url": "https://example.com/${testValues.paymentId}"},
                    "assertions": [{"type": "http_status", "expected": 200}],
                    "selectionMetadata": {"conditional": True, "requiredTestValueKeys": ["paymentId"]},
                },
                {
                    "id": "cond-missing",
                    "name": "Conditional missing value",
                    "request": {"method": "GET", "url": "https://example.com/${testValues.paymentId}"},
                    "assertions": [{"type": "http_status", "expected": 200}],
                    "selectionMetadata": {"conditional": True, "requiredTestValueKeys": ["paymentId"]},
                },
            ],
        }
    )
    # cond-ok has values; cond-missing is simulated as deselected via no-value context
    present_ctx = PlanTestValueContext(
        effective_values={"paymentId": "pmnt-001"},
        profile_id="sandbox",
        profile_source="default",
        override_keys=frozenset(),
    )
    plan = TestPlan.default_plan_from_manifest(manifest, test_value_context=present_ctx)
    # Both conditional steps have paymentId, so both selected
    s = plan.summary()
    assert s["conditionalSelected"] == 2
    assert s["conditionalDeselectedMissingValues"] == 0

    no_ctx = PlanTestValueContext()
    empty_plan = TestPlan.default_plan_from_manifest(manifest, test_value_context=no_ctx)
    s2 = empty_plan.summary()
    assert s2["conditionalSelected"] == 0
    assert s2["conditionalDeselectedMissingValues"] == 2


@pytest.mark.unit
def test_with_deselection_preserves_conditional_metadata() -> None:
    """``with_deselection`` preserves all conditional metadata on affected and unaffected entries."""
    from conformance.test_plan import build_plan_test_value_context

    manifest = parse_manifest(_manifest_with_conditional_step())
    ctx = build_plan_test_value_context(manifest, config_test_values=None)
    plan = TestPlan.default_plan_from_manifest(manifest, test_value_context=ctx)
    narrowed = plan.with_deselection(["cond"])

    original = next(e for e in plan.entries if e.step_id == "cond")
    modified = next(e for e in narrowed.entries if e.step_id == "cond")

    assert modified.selected is False
    assert modified.conditional == original.conditional
    assert modified.condition_id == original.condition_id
    assert modified.condition_label == original.condition_label
    assert modified.required_test_value_keys == original.required_test_value_keys
    assert modified.missing_test_value_keys == original.missing_test_value_keys
    assert modified.test_value_profile_id == original.test_value_profile_id
    assert modified.test_value_profile_source == original.test_value_profile_source


@pytest.mark.unit
def test_build_plan_test_value_context_no_profiles_returns_empty_context() -> None:
    """Manifests without testValueProfiles return an empty context with no profile_id."""
    from conformance.test_plan import build_plan_test_value_context

    manifest = parse_manifest(
        {
            "schemaVersion": "v1",
            "name": "No profiles",
            "steps": [
                {
                    "id": "h",
                    "name": "Health",
                    "request": {"method": "GET", "url": "https://example.com/health"},
                    "assertions": [{"type": "http_status", "expected": 200}],
                }
            ],
        }
    )
    ctx = build_plan_test_value_context(manifest, config_test_values=None)

    assert ctx.effective_values == {}
    assert ctx.profile_id is None
    assert ctx.profile_source is None
    assert ctx.override_keys == frozenset()


@pytest.mark.unit
def test_build_plan_test_value_context_overridden_source_when_non_default_profile() -> None:
    """Profile source is ``overridden`` when participant selects a non-default profile."""
    from pathlib import Path

    from conformance.model_bank_config import parse_model_bank_config
    from conformance.test_plan import build_plan_test_value_context

    manifest = parse_manifest(
        {
            "schemaVersion": "v1",
            "name": "Two profiles",
            "testValueProfiles": {
                "defaultProfileId": "sandbox",
                "profiles": [
                    {"id": "sandbox", "label": "Sandbox", "values": {"paymentId": "pmnt-001"}},
                    {"id": "uat", "label": "UAT", "values": {"paymentId": "pmnt-uat"}},
                ],
                "allowedOverrideKeys": [],
                "nonSecretKeys": ["paymentId"],
            },
            "steps": [
                {
                    "id": "s",
                    "name": "S",
                    "request": {"method": "GET", "url": "https://example.com/${testValues.paymentId}"},
                    "assertions": [{"type": "http_status", "expected": 200}],
                }
            ],
        }
    )
    config = parse_model_bank_config(
        {
            "environment": "sandbox",
            "discoveryUrl": "https://example.com/.well-known/openid-configuration",
            "testValues": {"profile": "uat"},
        },
        base_dir=Path.cwd(),
        output_base_dir=Path.cwd(),
    )
    ctx = build_plan_test_value_context(manifest, config_test_values=config.test_values)

    assert ctx.profile_id == "uat"
    assert ctx.profile_source == "overridden"
