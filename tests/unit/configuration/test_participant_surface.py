"""Tests for the shared participant-facing plan boundary."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest

import conformance.catalogue_registry
import conformance.test_plan_validation
from conformance.configuration_contracts import (
    ConfigurationContractError,
    DiagnosticCode,
)
from conformance.configuration_contracts.v2_execution_manifest import generate_execution_manifest
from conformance.configuration_contracts.v2_loader import (
    dump_execution_manifest,
    dump_resolved_plan,
    parse_participant_plan,
    participant_plan_to_document,
)
from conformance.participant_surface import (
    ParticipantSurfaceError,
    _participant_input_runtime_values,
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


def test_execution_configuration_round_trips_through_participant_plan() -> None:
    raw_plan = json.loads(_SURFACE_PLAN_PATH.read_text(encoding="utf-8"))

    plan = parse_participant_plan(raw_plan)

    assert participant_plan_to_document(plan) == raw_plan
    assert plan.execution_configuration is not None
    assert plan.execution_configuration.security_environment["clientId"] == "client-123"
    with pytest.raises(TypeError):
        cast(dict[str, object], plan.execution_configuration.security_environment)["clientId"] = "changed"


def test_execution_configuration_rejects_unknown_properties() -> None:
    raw_plan = json.loads(_SURFACE_PLAN_PATH.read_text(encoding="utf-8"))
    raw_plan["executionConfiguration"]["unknown"] = True
    with pytest.raises(ConfigurationContractError) as captured:
        parse_participant_plan(raw_plan)

    assert captured.value.diagnostics[0].code is DiagnosticCode.SCHEMA_VALIDATION_FAILED
    assert captured.value.diagnostics[0].instance_path == ""


def test_shared_surface_prepares_resolved_manifest_and_direct_execution(tmp_path: Path) -> None:
    raw_plan = json.loads(_SURFACE_PLAN_PATH.read_text(encoding="utf-8"))

    prepared = prepare_participant_plan_for_run(raw_plan, base_dir=tmp_path)

    assert prepared.resolved_plan.selection_valid is True
    assert len(prepared.execution_manifest.steps) == 5
    assert prepared.prepared_execution.manifest == prepared.execution_manifest
    assert prepared.prepared_execution.result_traceability is not None
    assert prepared.prepared_execution.artifact_resolver.manifest == prepared.execution_manifest
    assert tuple(str(step.test_definition_id) for step in prepared.execution_manifest.steps) == (
        "pis.v401.test.domestic_standing_order_consents.positive",
        "pis.v401.test.domestic_standing_order_consents.consentid.positive",
        "pis.v401.test.domestic_standing_orders.positive",
        "pis.v401.test.domestic_standing_orders.domesticstandingorderid.positive",
        "pis.v401.test.domestic-standing-order-consent.invalid-frequency",
    )


def test_execution_values_do_not_change_resolved_scope_identity(tmp_path: Path) -> None:
    raw_plan = json.loads(_SURFACE_PLAN_PATH.read_text(encoding="utf-8"))
    first = prepare_participant_plan_for_run(raw_plan, base_dir=tmp_path)
    changed = json.loads(json.dumps(raw_plan))
    changed["executionConfiguration"]["securityEnvironment"]["clientId"] = "different-client"
    second = prepare_participant_plan_for_run(changed, base_dir=tmp_path)

    assert first.resolved_plan.id == second.resolved_plan.id
    assert first.execution_manifest.id == second.execution_manifest.id


def test_material_non_sensitive_input_changes_trace_identity(tmp_path: Path) -> None:
    raw_plan = json.loads(_SURFACE_PLAN_PATH.read_text(encoding="utf-8"))
    first = prepare_participant_plan_for_run(raw_plan, base_dir=tmp_path)
    changed = json.loads(json.dumps(raw_plan))
    amount = next(item for item in changed["predefinedInputs"] if item["inputId"] == "pis.v401.input.instructed-amount")
    amount["value"] = "11.00"

    second = prepare_participant_plan_for_run(changed, base_dir=tmp_path)

    assert first.resolved_plan.id != second.resolved_plan.id
    assert first.execution_manifest.id != second.execution_manifest.id


def test_sensitive_input_is_traceable_without_value_fingerprint(tmp_path: Path) -> None:
    raw_plan = json.loads(_SURFACE_PLAN_PATH.read_text(encoding="utf-8"))
    first = prepare_participant_plan_for_run(raw_plan, base_dir=tmp_path)
    changed = json.loads(json.dumps(raw_plan))
    creditor_id = next(
        item
        for item in changed["predefinedInputs"]
        if item["inputId"] == "pis.v401.input.creditor-account-identification"
    )
    creditor_id["value"] = "08080099999999"

    second = prepare_participant_plan_for_run(changed, base_dir=tmp_path)

    assert first.resolved_plan.id == second.resolved_plan.id
    assert first.execution_manifest.id == second.execution_manifest.id
    serialized_trace = dump_resolved_plan(first.resolved_plan) + dump_execution_manifest(first.execution_manifest)
    assert "08080021325698" not in serialized_trace
    resolved_input = next(
        item for item in first.resolved_plan.predefined_inputs if item.id.endswith("creditor-account-identification")
    )
    assert resolved_input.redacted is True
    assert resolved_input.value is None


def test_pis_predefined_inputs_are_non_work_runtime_values(tmp_path: Path) -> None:
    raw_plan = json.loads(_SURFACE_PLAN_PATH.read_text(encoding="utf-8"))

    prepared = prepare_participant_plan_for_run(raw_plan, base_dir=tmp_path)

    assert prepared.participant_plan.execution_configuration is not None
    assert prepared.participant_plan.execution_configuration.compatibility_runtime_inputs == {}
    assert prepared.runtime_inputs["pisCreditorAccountIdentification"] == "08080021325698"
    assert prepared.runtime_inputs["pisInstructedAmountAmount"] == "10.00"
    assert prepared.runtime_inputs["pisStandingOrderFrequencyType"] == "WEEK"
    sensitive_input_ids = {item.id for item in prepared.execution_manifest.inputs if item.redacted}
    masked_pointers = {
        binding.target
        for step in prepared.execution_manifest.steps
        for binding in step.request.input_bindings
        if binding.input_id in sensitive_input_ids and binding.type == "json-body"
    }
    assert masked_pointers == {
        "/Data/Initiation/CreditorAccount/Identification",
        "/Data/Initiation/CreditorAccount/Name",
    }


def test_public_surface_rejects_pis_business_compatibility_alias(tmp_path: Path) -> None:
    raw_plan = json.loads(_SURFACE_PLAN_PATH.read_text(encoding="utf-8"))
    raw_plan["executionConfiguration"]["compatibilityRuntimeInputs"] = {
        "pisCreditorAccountIdentification": "08080021325698"
    }

    with pytest.raises(ParticipantSurfaceError, match="must use predefinedInputs"):
        prepare_participant_plan_for_run(raw_plan, base_dir=tmp_path)


def test_public_surface_rejects_unknown_pis_compatibility_input(tmp_path: Path) -> None:
    raw_plan = json.loads(_SURFACE_PLAN_PATH.read_text(encoding="utf-8"))
    raw_plan["executionConfiguration"]["compatibilityRuntimeInputs"] = {"arbitraryRequestOverride": "not allowed"}

    with pytest.raises(ParticipantSurfaceError, match="only environment, credential"):
        prepare_participant_plan_for_run(raw_plan, base_dir=tmp_path)


def test_public_surface_rejects_malformed_pis_date_time(tmp_path: Path) -> None:
    raw_plan = json.loads(_SURFACE_PLAN_PATH.read_text(encoding="utf-8"))
    first_payment = next(
        item for item in raw_plan["predefinedInputs"] if item["inputId"] == "pis.v401.input.first-payment-date-time"
    )
    first_payment["value"] = "tomorrow"

    with pytest.raises(ParticipantSurfaceError, match="input.invalid"):
        prepare_participant_plan_for_run(raw_plan, base_dir=tmp_path)


def test_safe_snapshot_and_export_redact_compatibility_values(tmp_path: Path) -> None:
    raw_plan = json.loads(_SURFACE_PLAN_PATH.read_text(encoding="utf-8"))
    prepared = prepare_participant_plan_for_run(raw_plan, base_dir=tmp_path)

    snapshot = build_safe_participant_plan_snapshot(
        prepared.participant_plan,
        prepared.catalogue.test_catalogue,
    )
    execution = snapshot["executionConfiguration"]
    assert isinstance(execution, dict)
    assert execution["compatibilityRuntimeInputs"] == {
        key: None for key in raw_plan["executionConfiguration"]["compatibilityRuntimeInputs"]
    }

    exported = participant_plan_export_document(
        prepared.participant_plan,
        prepared.catalogue.test_catalogue,
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


def test_legacy_authorities_cannot_affect_v2_compile_review_or_launch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(*args: object, **kwargs: object) -> None:
        raise AssertionError("legacy authority was called")

    monkeypatch.setattr(conformance.catalogue_registry, "resolve_catalogue", fail)
    monkeypatch.setattr(conformance.test_plan_validation, "prepare_test_plan_for_run", fail)
    raw_plan = json.loads(_SURFACE_PLAN_PATH.read_text(encoding="utf-8"))

    _plan, catalogue, resolved = compile_participant_document(raw_plan)
    prepared = prepare_participant_plan_for_run(raw_plan, base_dir=tmp_path)

    assert resolved.test_instances
    assert catalogue.test_catalogue.test_definitions
    assert prepared.execution_manifest.steps


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
    assert _participant_input_runtime_values(plan, scope=family)[expected_input_id] == expected_value


def test_cbpii_manifest_keeps_sensitive_input_out_of_generated_value() -> None:
    raw_plan = json.loads(
        (
            REPO_ROOT / "tests" / "fixtures" / "configuration_contracts" / "cbpii" / "v4_0_1" / "participant-plan.json"
        ).read_text(encoding="utf-8")
    )
    predefined_inputs = raw_plan["predefinedInputs"]
    assert isinstance(predefined_inputs, list)
    predefined_inputs.append(
        {
            "inputId": "cbpii.v401.input.debtor-account-name",
            "value": "Debtor account",
        }
    )

    _plan, catalogue, resolved = compile_participant_document(raw_plan)
    manifest = generate_execution_manifest(
        resolved,
        catalogue.test_catalogue,
    )
    debtor_id = next(item for item in manifest.inputs if item.id.endswith("debtor-account-identification"))
    assert debtor_id.redacted is True
    assert debtor_id.value is None
    debtor_name = next(item for item in manifest.inputs if item.id.endswith("debtor-account-name"))
    assert debtor_name.redacted is False
    assert debtor_name.value == "Debtor account"
