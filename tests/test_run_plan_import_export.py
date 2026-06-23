"""Unit tests for Run Plan import/export and drift handling in plan preview."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from conformance.api.plan_builder import build_plan_preview
from conformance.json_types import JsonValue
from conformance.manifest import Manifest, load_manifest_from_object
from conformance.model_bank_config import ModelBankConfig, parse_model_bank_config
from conformance.run_plan import (
    RunPlan,
    RunPlanSuiteCoordinates,
    RunPlanTestData,
    RunPlanTestValues,
    compute_manifest_hash,
    parse_run_plan,
    serialise_run_plan,
)


@pytest.fixture(name="base_config")
def fixture_base_config() -> ModelBankConfig:
    """Build a minimal valid model-bank configuration for unit tests.

    Returns:
        Parsed configuration suitable for plan-preview tests.
    """
    return parse_model_bank_config(
        {
            "environment": "test-env",
            "discoveryUrl": "https://example.com/.well-known/openid-configuration",
        },
        base_dir=Path.cwd(),
    )


@pytest.fixture(name="manifest_payload")
def fixture_manifest_payload() -> tuple[Manifest, bytes]:
    """Build a minimal v1 manifest and corresponding raw bytes.

    Returns:
        Two-tuple containing parsed manifest and deterministic UTF-8 JSON bytes.
    """
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "Import/export test manifest",
        "testValueProfiles": {
            "defaultProfileId": "default",
            "profiles": [
                {
                    "id": "default",
                    "label": "Default",
                    "values": {"allowedKey": "default-value"},
                }
            ],
            "allowedOverrideKeys": ["allowedKey"],
        },
        "steps": [
            {
                "id": "step-1",
                "name": "Step 1",
                "request": {"method": "GET", "url": "https://example.com/step-1"},
                "assertions": [{"type": "http_status", "expected": 200}],
            },
            {
                "id": "step-2",
                "name": "Step 2",
                "request": {"method": "GET", "url": "https://example.com/step-2"},
                "assertions": [{"type": "http_status", "expected": 200}],
            },
        ],
    }
    manifest_bytes = json.dumps(raw_manifest, sort_keys=True).encode("utf-8")
    return load_manifest_from_object(raw_manifest), manifest_bytes


@pytest.fixture(name="manifest_test_data_payload")
def fixture_manifest_test_data_payload() -> tuple[Manifest, bytes]:
    """Build a minimal v1 manifest using the new testData/baseline contract.

    Returns:
        Two-tuple containing parsed manifest and deterministic UTF-8 JSON bytes.
    """
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "Import/export test-data manifest",
        "testValues": {
            "baseline": {
                "allowedKey": "baseline-value",
                "secondKey": "second-baseline",
            },
            "allowedCustomKeys": ["allowedKey", "secondKey"],
        },
        "steps": [
            {
                "id": "step-1",
                "name": "Step 1",
                "request": {"method": "GET", "url": "https://example.com/step-1"},
                "assertions": [{"type": "http_status", "expected": 200}],
            }
        ],
    }
    manifest_bytes = json.dumps(raw_manifest, sort_keys=True).encode("utf-8")
    return load_manifest_from_object(raw_manifest), manifest_bytes


@pytest.fixture(name="manifest_profile_and_test_data_payload")
def fixture_manifest_profile_and_test_data_payload() -> tuple[Manifest, bytes]:
    """Build a manifest carrying both legacy profiles and new baseline metadata.

    Returns:
        Two-tuple containing parsed manifest and deterministic UTF-8 JSON bytes.
    """
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "Import/export profile migration manifest",
        "testValueProfiles": {
            "defaultProfileId": "default",
            "profiles": [
                {
                    "id": "default",
                    "label": "Default",
                    "values": {
                        "allowedKey": "baseline-value",
                        "secondKey": "second-baseline",
                    },
                },
                {
                    "id": "sandbox",
                    "label": "Sandbox",
                    "values": {
                        "allowedKey": "sandbox-value",
                        "secondKey": "second-baseline",
                    },
                },
            ],
            "allowedOverrideKeys": ["allowedKey", "secondKey"],
        },
        "testValues": {
            "baseline": {
                "allowedKey": "baseline-value",
                "secondKey": "second-baseline",
            },
            "allowedCustomKeys": ["allowedKey", "secondKey"],
        },
        "steps": [
            {
                "id": "step-1",
                "name": "Step 1",
                "request": {"method": "GET", "url": "https://example.com/step-1"},
                "assertions": [{"type": "http_status", "expected": 200}],
            }
        ],
    }
    manifest_bytes = json.dumps(raw_manifest, sort_keys=True).encode("utf-8")
    return load_manifest_from_object(raw_manifest), manifest_bytes


@pytest.fixture(name="manifest_test_data_with_refs_payload")
def fixture_manifest_test_data_with_refs_payload() -> tuple[Manifest, bytes]:
    """Build a v1 manifest where the step references test-value keys in its request.

    Returns:
        Two-tuple containing parsed manifest and deterministic UTF-8 JSON bytes.
    """
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "Import/export test-data refs manifest",
        "testValues": {
            "baseline": {
                "allowedKey": "baseline-value",
                "secondKey": "second-baseline",
            },
            "allowedCustomKeys": ["allowedKey", "secondKey"],
        },
        "steps": [
            {
                "id": "step-1",
                "name": "Step 1",
                "request": {
                    "method": "POST",
                    "url": "https://example.com/step-1",
                    "body": {
                        "encoding": "json",
                        "value": {
                            "key": "${testValues.allowedKey}",
                            "second": "${testValues.secondKey}",
                        },
                    },
                },
                "assertions": [{"type": "http_status", "expected": 200}],
            }
        ],
    }
    manifest_bytes = json.dumps(raw_manifest, sort_keys=True).encode("utf-8")
    return load_manifest_from_object(raw_manifest), manifest_bytes


def _run_plan_json(
    *,
    manifest_hash: str,
    selected_step_ids: tuple[str, ...],
    profile: str | None = None,
    custom_values: dict[str, str] | None = None,
    test_data_values: dict[str, str] | None = None,
) -> str:
    """Build a serialised Run Plan JSON payload for preview import tests.

    Args:
        manifest_hash: Manifest hash stored in the imported Run Plan suite block.
        selected_step_ids: Selected step ids to include in the imported payload.
        profile: Optional imported test-value profile id.
        custom_values: Optional imported custom-value overrides.
        test_data_values: Optional imported test-data deltas.

    Returns:
        Serialised Run Plan JSON string suitable for ``run_plan_import``.
    """
    run_plan = RunPlan(
        schema_version="1",
        suite=RunPlanSuiteCoordinates(
            id="custom",
            version="unknown",
            manifest_hash=manifest_hash,
        ),
        selected_step_ids=selected_step_ids,
        test_values=RunPlanTestValues(profile=profile, custom_values=custom_values or {}),
        test_data=RunPlanTestData(values=test_data_values or {}),
    )
    return json.dumps(serialise_run_plan(run_plan))


@pytest.mark.unit
def test_parse_run_plan_round_trip_through_serialise() -> None:
    """Run Plan parse and serialise helpers round-trip without data loss."""
    plan = RunPlan(
        schema_version="1",
        suite=RunPlanSuiteCoordinates(
            id="aisp-v4",
            version="4.0",
            manifest_hash="sha256:abc123",
        ),
        selected_step_ids=("step-1", "step-2"),
        test_values=RunPlanTestValues(
            profile="default",
            custom_values={"allowedKey": "override"},
        ),
    )

    restored = parse_run_plan(serialise_run_plan(plan))

    assert restored == plan


@pytest.mark.unit
def test_build_plan_preview_import_applies_selected_steps(
    base_config: ModelBankConfig,
    manifest_payload: tuple[Manifest, bytes],
) -> None:
    """Valid import pre-populates selected steps and test-value overrides."""
    manifest, manifest_bytes = manifest_payload
    import_payload = _run_plan_json(
        manifest_hash=compute_manifest_hash(manifest_bytes),
        selected_step_ids=("step-2",),
        profile="default",
        custom_values={"allowedKey": "imported"},
    )

    preview = build_plan_preview(
        config=base_config,
        manifest=manifest,
        manifest_bytes=manifest_bytes,
        run_plan_import=import_payload,
    )

    assert preview.selected_plan.selected_step_ids() == ["step-2"]
    assert preview.run_plan.test_values.custom_values["allowedKey"] == "imported"


@pytest.mark.unit
def test_build_plan_preview_hash_mismatch_blocks_launch(
    base_config: ModelBankConfig,
    manifest_payload: tuple[Manifest, bytes],
) -> None:
    """Imported manifest hash mismatch keeps preview but blocks launch."""
    manifest, manifest_bytes = manifest_payload

    preview = build_plan_preview(
        config=base_config,
        manifest=manifest,
        manifest_bytes=manifest_bytes,
        run_plan_import=_run_plan_json(
            manifest_hash="sha256:deadbeef",
            selected_step_ids=("step-1",),
        ),
    )

    assert preview.plan_drift_blocks_launch is True
    assert any("manifest hash mismatch" in blocker for blocker in preview.launch_blockers)


@pytest.mark.unit
def test_build_plan_preview_stale_step_ids_add_warnings(
    base_config: ModelBankConfig,
    manifest_payload: tuple[Manifest, bytes],
) -> None:
    """Stale imported step ids are removed and surfaced as drift warnings."""
    manifest, manifest_bytes = manifest_payload

    preview = build_plan_preview(
        config=base_config,
        manifest=manifest,
        manifest_bytes=manifest_bytes,
        run_plan_import=_run_plan_json(
            manifest_hash=compute_manifest_hash(manifest_bytes),
            selected_step_ids=("step-1", "missing-step"),
        ),
    )

    assert preview.plan_drift_blocks_launch is False
    assert any("stale step ID" in warning for warning in preview.plan_drift_warnings)
    assert any("have been removed" in warning for warning in preview.plan_drift_warnings)


@pytest.mark.unit
def test_build_plan_preview_stale_custom_keys_add_warnings(
    base_config: ModelBankConfig,
    manifest_payload: tuple[Manifest, bytes],
) -> None:
    """Stale imported custom-value keys are flagged as drift warnings."""
    manifest, manifest_bytes = manifest_payload

    preview = build_plan_preview(
        config=base_config,
        manifest=manifest,
        manifest_bytes=manifest_bytes,
        run_plan_import=_run_plan_json(
            manifest_hash=compute_manifest_hash(manifest_bytes),
            selected_step_ids=("step-1",),
            custom_values={"allowedKey": "ok", "staleKey": "bad"},
        ),
    )

    assert preview.plan_drift_blocks_launch is False
    assert any("staleKey" in warning for warning in preview.plan_drift_warnings)


@pytest.mark.unit
def test_build_plan_preview_invalid_import_json_adds_launch_blocker(
    base_config: ModelBankConfig,
    manifest_payload: tuple[Manifest, bytes],
) -> None:
    """Invalid imported JSON blocks launch with a human-readable blocker."""
    manifest, manifest_bytes = manifest_payload

    preview = build_plan_preview(
        config=base_config,
        manifest=manifest,
        manifest_bytes=manifest_bytes,
        run_plan_import="{ this is not valid json",
    )

    assert any("Run Plan import must be valid JSON" in blocker for blocker in preview.launch_blockers)


@pytest.mark.unit
def test_build_plan_preview_export_includes_full_test_data_snapshot(
    manifest_test_data_payload: tuple[Manifest, bytes],
) -> None:
    """Preview export stores a full test-data snapshot for new-schema manifests.

    Both participant-supplied values and baseline-equal values are stored so
    that the Run Plan is a complete executable document.  Keys not referenced
    by the selected step but present in the participant config are also
    preserved in the snapshot.
    """
    manifest, _ = manifest_test_data_payload
    config = parse_model_bank_config(
        {
            "environment": "test-env",
            "discoveryUrl": "https://example.com/.well-known/openid-configuration",
            "testData": {
                "values": {
                    "allowedKey": "override-value",
                    "secondKey": "second-baseline",
                }
            },
        },
        base_dir=Path.cwd(),
    )

    preview = build_plan_preview(
        config=config,
        manifest=manifest,
    )

    # Both values preserved: override-value (differs from baseline) and
    # second-baseline (equals baseline) are both in the full snapshot.
    assert dict(preview.run_plan.test_data.values) == {
        "allowedKey": "override-value",
        "secondKey": "second-baseline",
    }
    serialised = json.loads(preview.run_plan_json)
    assert serialised["testData"] == {"values": {"allowedKey": "override-value", "secondKey": "second-baseline"}}
    assert "testValues" not in serialised
    # Only allowedKey differs from baseline, so it is the sole delta key.
    assert preview.baseline_delta_keys == frozenset()


@pytest.mark.unit
def test_build_plan_preview_not_exploratory_when_all_values_match_baseline(
    manifest_test_data_with_refs_payload: tuple[Manifest, bytes],
) -> None:
    """is_exploratory_run is False when all referenced values match manifest baseline.

    A Run Plan that contains the full baseline snapshot should not be flagged
    as exploratory simply because testData.values is non-empty.
    """
    manifest, manifest_bytes = manifest_test_data_with_refs_payload
    config = parse_model_bank_config(
        {
            "environment": "test-env",
            "discoveryUrl": "https://example.com/.well-known/openid-configuration",
            "testData": {
                "values": {
                    "allowedKey": "baseline-value",
                    "secondKey": "second-baseline",
                }
            },
        },
        base_dir=Path.cwd(),
    )

    preview = build_plan_preview(
        config=config,
        manifest=manifest,
        manifest_bytes=manifest_bytes,
    )

    assert preview.is_exploratory_run is False
    assert preview.baseline_delta_keys == frozenset()
    assert dict(preview.run_plan.test_data.values) == {
        "allowedKey": "baseline-value",
        "secondKey": "second-baseline",
    }


@pytest.mark.unit
def test_build_plan_preview_exploratory_when_referenced_value_differs_from_baseline(
    manifest_test_data_with_refs_payload: tuple[Manifest, bytes],
) -> None:
    """is_exploratory_run is True when a referenced key value differs from baseline."""
    manifest, manifest_bytes = manifest_test_data_with_refs_payload
    config = parse_model_bank_config(
        {
            "environment": "test-env",
            "discoveryUrl": "https://example.com/.well-known/openid-configuration",
            "testData": {
                "values": {
                    "allowedKey": "custom-override",
                    "secondKey": "second-baseline",
                }
            },
        },
        base_dir=Path.cwd(),
    )

    preview = build_plan_preview(
        config=config,
        manifest=manifest,
        manifest_bytes=manifest_bytes,
        exploratory_ack=True,
    )

    assert preview.is_exploratory_run is True
    assert preview.baseline_delta_keys == frozenset({"allowedKey"})


@pytest.mark.unit
def test_build_plan_preview_baseline_fills_referenced_keys_when_config_has_no_test_data(
    manifest_test_data_with_refs_payload: tuple[Manifest, bytes],
) -> None:
    """Baseline values fill step-referenced keys when config omits testData."""
    manifest, manifest_bytes = manifest_test_data_with_refs_payload
    config = parse_model_bank_config(
        {
            "environment": "test-env",
            "discoveryUrl": "https://example.com/.well-known/openid-configuration",
        },
        base_dir=Path.cwd(),
    )

    preview = build_plan_preview(
        config=config,
        manifest=manifest,
        manifest_bytes=manifest_bytes,
    )

    # Both keys referenced by the selected step are filled from baseline.
    assert dict(preview.run_plan.test_data.values) == {
        "allowedKey": "baseline-value",
        "secondKey": "second-baseline",
    }
    assert preview.is_exploratory_run is False


@pytest.mark.unit
def test_build_plan_preview_import_delta_only_plan_fills_to_full_snapshot(
    base_config: ModelBankConfig,
    manifest_test_data_with_refs_payload: tuple[Manifest, bytes],
) -> None:
    """Importing an older delta-only Run Plan fills missing keys from baseline.

    Run Plans exported before the full-snapshot change only stored keys that
    differed from baseline.  On import, the preview should reconstruct a full
    snapshot by seeding missing keys from the manifest baseline.
    """
    manifest, manifest_bytes = manifest_test_data_with_refs_payload
    # Delta-only import: only allowedKey is present (secondKey omitted)
    import_payload = _run_plan_json(
        manifest_hash=compute_manifest_hash(manifest_bytes),
        selected_step_ids=("step-1",),
        test_data_values={"allowedKey": "imported-override"},
    )

    preview = build_plan_preview(
        config=base_config,
        manifest=manifest,
        manifest_bytes=manifest_bytes,
        run_plan_import=import_payload,
    )

    assert dict(preview.run_plan.test_data.values) == {
        "allowedKey": "imported-override",
        "secondKey": "second-baseline",  # filled from baseline
    }
    assert preview.baseline_delta_keys == frozenset({"allowedKey"})
    assert preview.is_exploratory_run is True


@pytest.mark.unit
def test_build_plan_preview_import_legacy_custom_values_migrates_to_test_data(
    base_config: ModelBankConfig,
    manifest_test_data_payload: tuple[Manifest, bytes],
) -> None:
    """Legacy imported custom values are migrated to the full testData snapshot."""
    manifest, manifest_bytes = manifest_test_data_payload
    preview = build_plan_preview(
        config=base_config,
        manifest=manifest,
        manifest_bytes=manifest_bytes,
        run_plan_import=_run_plan_json(
            manifest_hash=compute_manifest_hash(manifest_bytes),
            selected_step_ids=("step-1",),
            custom_values={
                "allowedKey": "override-value",
                "secondKey": "second-baseline",
                "staleKey": "stale-value",
            },
        ),
    )

    # Both imported values are preserved in the full snapshot; staleKey is dropped.
    assert dict(preview.run_plan.test_data.values) == {
        "allowedKey": "override-value",
        "secondKey": "second-baseline",
    }
    assert preview.legacy_test_values_warning is True
    assert any("legacy testValues" in warning for warning in preview.plan_drift_warnings)
    serialised = json.loads(preview.run_plan_json)
    assert serialised["testData"] == {"values": {"allowedKey": "override-value", "secondKey": "second-baseline"}}
    assert "testValues" not in serialised


@pytest.mark.unit
def test_build_plan_preview_import_legacy_profile_maps_to_test_data_deltas(
    base_config: ModelBankConfig,
    manifest_profile_and_test_data_payload: tuple[Manifest, bytes],
) -> None:
    """Legacy profile import maps profile values to baseline deltas in testData."""
    manifest, manifest_bytes = manifest_profile_and_test_data_payload
    preview = build_plan_preview(
        config=base_config,
        manifest=manifest,
        manifest_bytes=manifest_bytes,
        run_plan_import=_run_plan_json(
            manifest_hash=compute_manifest_hash(manifest_bytes),
            selected_step_ids=("step-1",),
            profile="sandbox",
        ),
    )

    assert dict(preview.run_plan.test_data.values) == {"allowedKey": "sandbox-value"}
    assert preview.run_plan.test_values.profile is None
    serialised = json.loads(preview.run_plan_json)
    assert serialised["testData"] == {"values": {"allowedKey": "sandbox-value"}}
    assert "testValues" not in serialised
