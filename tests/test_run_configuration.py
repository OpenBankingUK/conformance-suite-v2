"""Unit tests for compiled run-configuration assembly."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from conformance.manifest import ManifestTestValues
from conformance.run_configuration import RunConfigurationCompiler, compile_run_configuration


def _manifest_test_values() -> ManifestTestValues:
    """Build reusable manifest test-value declarations for compiler tests.

    Returns:
        Parsed manifest test-value metadata with baseline, generated, and
        allowed custom keys populated.
    """
    return ManifestTestValues(
        baseline={
            "creditorName": "Test Merchant",
            "instructedAmountValue": "1.00",
        },
        generated_keys={"consentIdempotencyKey": "per-run-uuid"},
        allowed_custom_keys=frozenset({"creditorName", "instructedAmountValue"}),
    )


@pytest.mark.unit
def test_compile_uses_baseline_values_when_no_custom_values_are_supplied() -> None:
    """Compiler resolves referenced baseline keys to baseline values."""
    compiler = RunConfigurationCompiler(_manifest_test_values())

    config = compiler.compile(
        suite_id="suite-id",
        referenced_keys={"creditorName", "instructedAmountValue"},
        test_data_values={},
    )

    assert dict(config.effective_test_values) == {
        "creditorName": "Test Merchant",
        "instructedAmountValue": "1.00",
    }
    assert config.baseline_delta_keys == frozenset()
    assert config.missing_required_keys == frozenset()


@pytest.mark.unit
def test_compile_marks_custom_values_as_baseline_deltas() -> None:
    """Compiler marks differing participant values as custom deltas."""
    compiler = RunConfigurationCompiler(_manifest_test_values())

    config = compiler.compile(
        suite_id="suite-id",
        referenced_keys={"creditorName"},
        test_data_values={"creditorName": "Custom Merchant"},
    )

    assert config.effective_test_values["creditorName"] == "Custom Merchant"
    assert config.resolved_values["creditorName"].source == "custom"
    assert config.baseline_delta_keys == frozenset({"creditorName"})


@pytest.mark.unit
def test_compile_normalises_same_as_baseline_values_away() -> None:
    """Compiler treats submitted same-as-baseline values as baseline values."""
    compiler = RunConfigurationCompiler(_manifest_test_values())

    config = compiler.compile(
        suite_id="suite-id",
        referenced_keys={"creditorName"},
        test_data_values={"creditorName": "Test Merchant"},
    )

    assert config.effective_test_values["creditorName"] == "Test Merchant"
    assert config.resolved_values["creditorName"].source == "baseline"
    assert config.baseline_delta_keys == frozenset()


@pytest.mark.unit
def test_compile_includes_generated_keys() -> None:
    """Compiler resolves generated keys to their strategy identifiers."""
    compiler = RunConfigurationCompiler(_manifest_test_values())

    config = compiler.compile(
        suite_id="suite-id",
        referenced_keys={"consentIdempotencyKey"},
        test_data_values={},
    )

    assert config.effective_test_values["consentIdempotencyKey"] == "per-run-uuid"
    assert config.resolved_values["consentIdempotencyKey"].source == "generated"
    assert config.resolved_values["consentIdempotencyKey"].baseline_value is None


@pytest.mark.unit
def test_compile_tracks_missing_required_keys() -> None:
    """Compiler records referenced keys absent from baseline and submitted data."""
    compiler = RunConfigurationCompiler(_manifest_test_values())

    config = compiler.compile(
        suite_id="suite-id",
        referenced_keys={"missingKey"},
        test_data_values={},
    )

    assert config.missing_required_keys == frozenset({"missingKey"})
    assert config.can_execute is False


@pytest.mark.unit
def test_compile_rejects_disallowed_custom_keys() -> None:
    """Compiler raises ValueError for submitted keys outside allowedCustomKeys."""
    compiler = RunConfigurationCompiler(_manifest_test_values())

    with pytest.raises(ValueError, match="allowedCustomKeys"):
        compiler.compile(
            suite_id="suite-id",
            referenced_keys={"creditorName"},
            test_data_values={"notAllowed": "value"},
        )


@pytest.mark.unit
def test_run_configuration_properties_reflect_compiled_state() -> None:
    """RunConfiguration convenience properties mirror delta and missing-key state."""
    compiler = RunConfigurationCompiler(_manifest_test_values())

    baseline_only = compiler.compile(
        suite_id="suite-id",
        referenced_keys={"creditorName"},
        test_data_values={},
    )
    customised = compiler.compile(
        suite_id="suite-id",
        referenced_keys={"creditorName"},
        test_data_values={"creditorName": "Custom Merchant"},
    )
    missing = compiler.compile(
        suite_id="suite-id",
        referenced_keys={"missingKey"},
        test_data_values={},
    )

    assert baseline_only.is_certifiable_by_value_purity is True
    assert baseline_only.has_custom_values is False
    assert baseline_only.can_execute is True
    assert customised.is_certifiable_by_value_purity is False
    assert customised.has_custom_values is True
    assert customised.can_execute is True
    assert missing.can_execute is False


def _make_manifest_step(step_id: str, consumed_keys: frozenset[str]) -> MagicMock:
    """Build a minimal mock manifest step for helper tests.

    Args:
        step_id: Step identifier.
        consumed_keys: Set of test-value key names consumed by the step.

    Returns:
        MagicMock with ``id`` and ``consumed_test_value_keys`` attributes set.
    """
    step = MagicMock()
    step.id = step_id
    step.consumed_test_value_keys = consumed_keys
    return step


def _make_manifest(
    test_values: ManifestTestValues | None,
    steps: list[MagicMock],
) -> MagicMock:
    """Build a minimal mock Manifest for helper tests.

    Args:
        test_values: Manifest-level test-value declarations, or ``None``.
        steps: Ordered list of mock steps.

    Returns:
        MagicMock with ``test_values``, ``steps``, and ``name`` set.
    """
    manifest = MagicMock()
    manifest.test_values = test_values
    manifest.steps = steps
    manifest.name = "test-suite"
    return manifest


@pytest.mark.unit
def test_compile_run_configuration_returns_none_when_no_test_values() -> None:
    """Helper returns None when the manifest has no testValues block."""
    manifest = _make_manifest(test_values=None, steps=[])

    result = compile_run_configuration(manifest=manifest, test_data_values={})

    assert result is None


@pytest.mark.unit
def test_compile_run_configuration_collects_all_step_keys() -> None:
    """Helper aggregates consumed_test_value_keys across all steps."""
    tv = ManifestTestValues(
        baseline={"keyA": "valA", "keyB": "valB"},
        generated_keys={},
        allowed_custom_keys=frozenset(),
    )
    step1 = _make_manifest_step("step-1", frozenset({"keyA"}))
    step2 = _make_manifest_step("step-2", frozenset({"keyB"}))
    manifest = _make_manifest(tv, [step1, step2])

    result = compile_run_configuration(manifest=manifest, test_data_values={})

    assert result is not None
    assert "keyA" in result.effective_test_values
    assert "keyB" in result.effective_test_values
    assert result.missing_required_keys == frozenset()


@pytest.mark.unit
def test_compile_run_configuration_filters_by_selected_step_ids() -> None:
    """Helper restricts key collection to selected steps when ids provided."""
    tv = ManifestTestValues(
        baseline={"keyA": "valA", "keyB": "valB"},
        generated_keys={},
        allowed_custom_keys=frozenset(),
    )
    step1 = _make_manifest_step("step-1", frozenset({"keyA"}))
    step2 = _make_manifest_step("step-2", frozenset({"keyB"}))
    manifest = _make_manifest(tv, [step1, step2])

    result = compile_run_configuration(
        manifest=manifest,
        selected_step_ids={"step-1"},
        test_data_values={},
    )

    assert result is not None
    assert "keyA" in result.effective_test_values
    assert "keyB" not in result.effective_test_values


@pytest.mark.unit
def test_compile_run_configuration_detects_custom_values() -> None:
    """Helper reports has_custom_values when participant overrides baseline."""
    tv = ManifestTestValues(
        baseline={"creditorName": "Default Merchant"},
        generated_keys={},
        allowed_custom_keys=frozenset({"creditorName"}),
    )
    step = _make_manifest_step("step-1", frozenset({"creditorName"}))
    manifest = _make_manifest(tv, [step])

    result = compile_run_configuration(
        manifest=manifest,
        test_data_values={"creditorName": "Custom Merchant"},
    )

    assert result is not None
    assert result.has_custom_values is True
    assert result.baseline_delta_keys == frozenset({"creditorName"})


@pytest.mark.unit
def test_compile_run_configuration_normalises_same_as_baseline_values() -> None:
    """Helper treats submitted values matching baseline as no custom impact."""
    tv = ManifestTestValues(
        baseline={"creditorName": "Default Merchant"},
        generated_keys={},
        allowed_custom_keys=frozenset({"creditorName"}),
    )
    step = _make_manifest_step("step-1", frozenset({"creditorName"}))
    manifest = _make_manifest(tv, [step])

    result = compile_run_configuration(
        manifest=manifest,
        test_data_values={"creditorName": "Default Merchant"},
    )

    assert result is not None
    assert result.has_custom_values is False
    assert result.baseline_delta_keys == frozenset()


@pytest.mark.unit
def test_compile_run_configuration_surfaces_missing_required_keys() -> None:
    """Helper surfaces keys referenced by steps but absent from baseline and test data."""
    tv = ManifestTestValues(
        baseline={},
        generated_keys={},
        allowed_custom_keys=frozenset(),
    )
    step = _make_manifest_step("step-1", frozenset({"missingKey"}))
    manifest = _make_manifest(tv, [step])

    result = compile_run_configuration(manifest=manifest, test_data_values={})

    assert result is not None
    assert result.missing_required_keys == frozenset({"missingKey"})
    assert result.can_execute is False


@pytest.mark.unit
def test_compile_run_configuration_raises_for_disallowed_keys() -> None:
    """Helper propagates ValueError when test data contains non-allowed keys."""
    tv = ManifestTestValues(
        baseline={},
        generated_keys={},
        allowed_custom_keys=frozenset({"allowedKey"}),
    )
    manifest = _make_manifest(tv, [])

    with pytest.raises(ValueError, match="allowedCustomKeys"):
        compile_run_configuration(
            manifest=manifest,
            test_data_values={"notAllowed": "value"},
        )
