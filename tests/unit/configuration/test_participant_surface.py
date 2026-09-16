"""Tests for the shared participant-facing plan boundary."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

from conformance.configuration_contracts import (
    ConfigurationContractError,
    DiagnosticCode,
    generate_execution_manifest,
    parse_participant_plan,
    participant_plan_to_document,
)
from conformance.participant_surface import (
    ParticipantSurfaceError,
    _legacy_predefined_inputs,
    _manifest_observation_ids,
    compile_participant_document,
    participant_plan_export_document,
    prepare_participant_plan_for_run,
)
from conformance.results import build_safe_participant_plan_snapshot
from tests.support.paths import REPO_ROOT

pytestmark = pytest.mark.unit

_SURFACE_PLAN_PATH = (
    REPO_ROOT / "tests" / "fixtures" / "configuration_contracts" / "pis" / "v4_0_1" / "participant-plan.surface.json"
)
_AIS_ACCOUNTS_SURFACE_PLAN_PATH = (
    REPO_ROOT / "tests" / "fixtures" / "configuration_contracts" / "ais" / "v4_0_1" / "participant-plan.surface.json"
)
_INVALID_EXECUTION_PATH = (
    REPO_ROOT
    / "tests"
    / "fixtures"
    / "configuration_contracts"
    / "v1"
    / "participant-plan.invalid-execution-property.json"
)


def test_execution_configuration_round_trips_through_participant_plan() -> None:
    raw_plan = json.loads(_SURFACE_PLAN_PATH.read_text(encoding="utf-8"))

    plan = parse_participant_plan(raw_plan)

    assert participant_plan_to_document(plan) == raw_plan
    assert plan.execution_configuration is not None
    assert plan.execution_configuration.security_environment["clientId"] == "client-123"
    with pytest.raises(TypeError):
        cast(dict[str, object], plan.execution_configuration.security_environment)["clientId"] = "changed"


def test_execution_configuration_rejects_unknown_properties() -> None:
    with pytest.raises(ConfigurationContractError) as captured:
        parse_participant_plan(json.loads(_INVALID_EXECUTION_PATH.read_text(encoding="utf-8")))

    assert captured.value.diagnostics[0].code is DiagnosticCode.SCHEMA_VALIDATION_FAILED
    assert captured.value.diagnostics[0].instance_path == "/executionConfiguration/unknown"


def test_shared_surface_prepares_resolved_manifest_and_legacy_execution(tmp_path: Path) -> None:
    raw_plan = json.loads(_SURFACE_PLAN_PATH.read_text(encoding="utf-8"))

    prepared = prepare_participant_plan_for_run(raw_plan, base_dir=tmp_path)

    assert prepared.resolved_plan.selection_valid is True
    assert len(prepared.execution_manifest.steps) == 5
    assert prepared.prepared_execution.manifest == prepared.execution_manifest
    assert prepared.prepared_execution.result_traceability is not None
    assert len(prepared.prepared_execution.result_traceability.result_observation_id_by_manifest_step_id) == len(
        prepared.execution_manifest.steps
    )
    assert prepared.compiled_plan.traceability.generated_test_case_ids == (
        "pis-v4-domestic-standing-order-consent-create",
        "pis-v4-domestic-standing-order-consent-read",
        "pis-v4-domestic-standing-order-create",
        "pis-v4-domestic-standing-order-read",
        "pis-v4-domestic-standing-order-consent-reject-invalid-frequency",
    )


def test_execution_values_do_not_change_resolved_scope_identity(tmp_path: Path) -> None:
    raw_plan = json.loads(_SURFACE_PLAN_PATH.read_text(encoding="utf-8"))
    first = prepare_participant_plan_for_run(raw_plan, base_dir=tmp_path)
    changed = json.loads(json.dumps(raw_plan))
    changed["executionConfiguration"]["securityEnvironment"]["clientId"] = "different-client"
    second = prepare_participant_plan_for_run(changed, base_dir=tmp_path)

    assert first.resolved_plan.id == second.resolved_plan.id
    assert first.execution_manifest.id == second.execution_manifest.id


def test_safe_snapshot_and_export_redact_compatibility_values(tmp_path: Path) -> None:
    raw_plan = json.loads(_SURFACE_PLAN_PATH.read_text(encoding="utf-8"))
    prepared = prepare_participant_plan_for_run(raw_plan, base_dir=tmp_path)

    snapshot = build_safe_participant_plan_snapshot(
        prepared.participant_plan,
        prepared.catalogue.requirements,
    )
    execution = snapshot["executionConfiguration"]
    assert isinstance(execution, dict)
    assert execution["compatibilityRuntimeInputs"] == {
        key: None for key in raw_plan["executionConfiguration"]["compatibilityRuntimeInputs"]
    }

    exported = participant_plan_export_document(
        prepared.participant_plan,
        prepared.catalogue.requirements,
        include_secrets=False,
    )
    exported_execution = exported["executionConfiguration"]
    assert isinstance(exported_execution, dict)
    assert exported_execution["compatibilityRuntimeInputs"] == {}


def test_public_surface_rejects_non_registered_suite_release(tmp_path: Path) -> None:
    raw_plan = json.loads(_SURFACE_PLAN_PATH.read_text(encoding="utf-8"))
    raw_plan["suiteReleaseId"] = "untrusted.release"

    with pytest.raises(ParticipantSurfaceError, match="this tool accepts"):
        prepare_participant_plan_for_run(raw_plan, base_dir=tmp_path)


def test_missing_compatibility_operation_has_stable_reference_diagnostic(tmp_path: Path) -> None:
    raw_plan = json.loads(_AIS_ACCOUNTS_SURFACE_PLAN_PATH.read_text(encoding="utf-8"))
    prepared = prepare_participant_plan_for_run(raw_plan, base_dir=tmp_path)
    compiled_without_delete = replace(
        prepared.compiled_plan,
        test_cases=tuple(
            test_case
            for test_case in prepared.compiled_plan.test_cases
            if not any(request.method == "DELETE" for request in test_case.request_steps)
        ),
    )

    with pytest.raises(
        ParticipantSurfaceError,
        match=(
            r"Compatibility catalogue reference unresolved: no executable observation matches "
            r"manifest step .* \(DELETE /account-access-consents/\{ConsentId\}\)"
        ),
    ):
        _manifest_observation_ids(prepared.execution_manifest, compiled_without_delete)


@pytest.mark.parametrize(
    ("family", "expected_input_id", "expected_value"),
    [
        ("cbpii", "cbpiiInstructedAmountAmount", "10.00"),
        ("vrp", "vrpInstructedAmountAmount", "1.00"),
    ],
)
def test_scope_specific_amounts_reach_compatibility_runtime(
    family: str,
    expected_input_id: str,
    expected_value: str,
) -> None:
    raw_plan = json.loads(
        (
            REPO_ROOT / "tests" / "fixtures" / "configuration_contracts" / family / "v4_0_1" / "participant-plan.json"
        ).read_text(encoding="utf-8")
    )
    plan = parse_participant_plan(raw_plan)
    assert _legacy_predefined_inputs(plan, scope=family)[expected_input_id] == expected_value


def test_cbpii_manifest_keeps_sensitive_input_out_of_generated_value() -> None:
    raw_plan = json.loads(
        (
            REPO_ROOT / "tests" / "fixtures" / "configuration_contracts" / "cbpii" / "v4_0_1" / "participant-plan.json"
        ).read_text(encoding="utf-8")
    )
    raw_plan["suiteReleaseId"] = "obl.open-banking-mvp.catalogue-release"

    _plan, catalogue, resolved = compile_participant_document(raw_plan)
    manifest = generate_execution_manifest(
        resolved,
        catalogue.requirements,
        catalogue.test_definitions,
    )
    debtor_id = next(item for item in manifest.inputs if item.id.endswith("debtor-account-identification"))
    assert debtor_id.redacted is True
    assert debtor_id.value is None
