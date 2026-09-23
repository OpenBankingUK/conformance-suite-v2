"""Focused contract tests for the schema-version 2.0 production cutover."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest

from conformance.configuration_contracts import (
    ConfigurationContractError,
    DiagnosticCode,
    LegacyExecutionEngine,
    PreparedExecutionManifest,
    StableId,
    SuiteReleaseArtifactError,
    SuiteReleaseArtifactErrorCode,
    V2ParticipantPlanCompilationError,
    compile_participant_plan_v2,
    dump_execution_manifest_v2,
    dump_resolved_plan_v2,
    generate_execution_manifest_v2,
    load_suite_release_v2,
    load_test_definition_catalogue_v2,
    parse_participant_plan_v2,
    parse_test_definition_catalogue_v2,
    preflight_suite_release_artifacts,
    validate_bundled_schemas_v2,
    validate_execution_manifest_compatibility,
)
from conformance.configuration_contracts.v2_loader import (
    execution_manifest_to_document,
    parse_execution_manifest,
    parse_resolved_plan,
    participant_plan_to_document,
    resolved_plan_to_document,
)
from conformance.configuration_contracts.v2_loader import (
    test_definition_catalogue_to_document as catalogue_to_document,
)
from conformance.context import RuntimeConfig
from conformance.executor import _execution_manifest_to_runtime_manifest
from conformance.json_types import JsonObject, JsonValue
from conformance.participant_surface import _participant_input_runtime_values
from conformance.results import (
    ResultTraceabilitySource,
    StepResult,
    build_safe_participant_plan_snapshot,
    build_smoke_check_result,
)
from tests.support.paths import REPO_ROOT

pytestmark = pytest.mark.unit

_ROOT = REPO_ROOT / "conformance" / "configuration_contracts"
_RELEASE_PATH = _ROOT / "bundles" / "open-banking-mvp" / "suite-release.json"
_CATALOGUE_PATHS = tuple(sorted(_ROOT.glob("catalogues/*/*/test-catalogue.v2.json")))
_FIXTURE_ROOT = REPO_ROOT / "tests" / "fixtures" / "configuration_contracts"
_PARITY_CASES = (
    (
        "ais",
        "v3_1_11",
        REPO_ROOT
        / "conformance"
        / "standards"
        / "ob_read_write"
        / "v3_1_11"
        / "legacy"
        / "ob_3.1_accounts_transactions_fca.json",
    ),
    (
        "ais",
        "v4_0_1",
        REPO_ROOT
        / "conformance"
        / "standards"
        / "ob_read_write"
        / "v4_0"
        / "legacy-ob_4.0_accounts_transactions_fca.json",
    ),
    (
        "cbpii",
        "v3_1_11",
        REPO_ROOT / "conformance" / "standards" / "ob_read_write" / "v3_1_11" / "legacy" / "ob_3.1_cbpii_fca.json",
    ),
    ("cbpii", "v4_0_1", _FIXTURE_ROOT / "cbpii" / "v4_0_1" / "ob_4.0_cbpii_fca.json"),
    (
        "dcr",
        "v3_4",
        REPO_ROOT / "conformance" / "standards" / "ob_dcr" / "v3_4" / "parity-contract.json",
    ),
    (
        "pis",
        "v3_1_11",
        REPO_ROOT / "conformance" / "standards" / "ob_read_write" / "v3_1_11" / "legacy" / "ob_3.1_payment_fca.json",
    ),
    (
        "pis",
        "v4_0_1",
        REPO_ROOT / "conformance" / "standards" / "ob_read_write" / "v4_0" / "legacy" / "ob_4.0_payment_fca.json",
    ),
    (
        "vrp",
        "v3_1_11",
        REPO_ROOT
        / "conformance"
        / "standards"
        / "ob_read_write"
        / "v3_1_11"
        / "legacy"
        / "ob_3.1_variable_recurring_payments.json",
    ),
    ("vrp", "v4_0_1", _FIXTURE_ROOT / "vrp" / "v4_0_1" / "ob_4.0_variable_recurring_payments.json"),
)


def test_v2_schemas_and_all_nine_manually_reviewed_catalogues_are_valid() -> None:
    assert validate_bundled_schemas_v2() == ()
    release = load_suite_release_v2(_RELEASE_PATH)
    catalogues = tuple(load_test_definition_catalogue_v2(path) for path in _CATALOGUE_PATHS)

    assert release.schema_version == "2.0"
    assert len(catalogues) == 9
    assert len({(item.specification.test_scope, item.specification.version) for item in catalogues}) == 9
    assert all(item.schema_version == "2.0" for item in catalogues)
    serialized = json.dumps([json.loads(path.read_text()) for path in _CATALOGUE_PATHS])
    forbidden = (
        "requirementsCatalogueId",
        "coveredRequirementIds",
        "normativeReferenceIds",
        "normativeReferences",
        '"requirements"',
        '"assessment"',
    )
    assert not any(value in serialized for value in forbidden)
    assert not any(item.kind == "requirements-catalogue" for item in release.artifacts)
    assert all(
        item.allowed_security_profiles == (("all",) if item.specification.test_scope == "dcr" else ("fapi1-advanced",))
        for item in catalogues
    )


@pytest.mark.parametrize(("family", "version", "baseline_path"), _PARITY_CASES)
def test_every_supported_family_version_retains_pinned_parity(
    family: str,
    version: str,
    baseline_path: Path,
) -> None:
    report = cast(
        JsonObject,
        json.loads((_FIXTURE_ROOT / family / version / "parity-comparison.json").read_text(encoding="utf-8")),
    )
    catalogue = load_test_definition_catalogue_v2(_ROOT / "catalogues" / family / version / "test-catalogue.v2.json")
    classifications = cast(list[JsonObject], report["classifications"])
    summary = cast(JsonObject, report["summary"])
    replacement_ids = {str(item.id) for item in catalogue.test_definitions}
    source_key = "sourceCase" if family == "dcr" else "sourceRow"

    baseline = cast(JsonObject, report["baseline"])
    assert baseline["digest"] == f"sha256:{hashlib.sha256(baseline_path.read_bytes()).hexdigest()}"
    if family == "dcr":
        baseline_document = cast(JsonObject, json.loads(baseline_path.read_text(encoding="utf-8")))
        expected_sources = {
            f"parity-contract.json#/scenarios/{scenario_index}/cases/{case_index}"
            for scenario_index, scenario in enumerate(cast(list[JsonObject], baseline_document["scenarios"]))
            for case_index, _case in enumerate(cast(list[JsonObject], scenario["cases"]))
        }
    else:
        baseline_document = cast(JsonObject, json.loads(baseline_path.read_text(encoding="utf-8")))
        expected_sources = {
            f"{baseline['file']}#{index}:{script['id']}"
            for index, script in enumerate(cast(list[JsonObject], baseline_document["scripts"]))
        }
    assert baseline["purpose"] == "migration-cross-check-only"
    assert {item[source_key] for item in classifications} == expected_sources
    assert summary["genuine-omission"] == 0
    assert sum(cast(int, value) for value in summary.values()) == len(classifications)
    assert all(
        set(cast(list[str], item["replacementTestDefinitionIds"])) <= replacement_ids for item in classifications
    )


def test_every_released_capability_preflights_and_lowers_every_request_without_network() -> None:
    release = load_suite_release_v2(_RELEASE_PATH)
    exercised: set[tuple[str, str]] = set()
    for path in _CATALOGUE_PATHS:
        catalogue = load_test_definition_catalogue_v2(path)
        for capability in catalogue.capabilities:
            plan = parse_participant_plan_v2(
                _participant_document(
                    release.id,
                    catalogue,
                    selected_capability_ids=(capability.id,),
                )
            )
            resolved = compile_participant_plan_v2(release, catalogue, plan)
            manifest = generate_execution_manifest_v2(resolved, catalogue)
            resolver = preflight_suite_release_artifacts(manifest, release)
            runtime_inputs = dict(
                _participant_input_runtime_values(
                    plan,
                    scope=str(catalogue.specification.test_scope),
                )
            )
            for step in manifest.steps:
                for reference in step.request.runtime_input_refs:
                    runtime_inputs.setdefault(reference, "example")
            runtime_inputs["resourceBaseUrl"] = "https://rs.example.com"
            prepared = PreparedExecutionManifest(
                manifest=manifest,
                engine=(
                    LegacyExecutionEngine.DCR
                    if catalogue.specification.test_scope == "dcr"
                    else LegacyExecutionEngine.READ_WRITE
                ),
                runtime_inputs=runtime_inputs,
                runtime_input_base_dir=REPO_ROOT,
                artifact_resolver=resolver,
                result_traceability=cast(Any, object()),
            )
            validate_execution_manifest_compatibility(prepared)
            if prepared.engine is LegacyExecutionEngine.READ_WRITE:
                lowered = _execution_manifest_to_runtime_manifest(
                    cast(Any, manifest),
                    runtime_inputs=runtime_inputs,
                    runtime_input_base_dir=REPO_ROOT,
                    runtime_config=RuntimeConfig(
                        discovery_url="https://as.example.com/.well-known/openid-configuration",
                        oauth_token_endpoint="https://as.example.com/token",  # noqa: S106 - URL, not a credential
                    ),
                    artifact_resolver=resolver,
                )
                lowered_ids = {step.id for step in lowered.steps}
                assert {str(step.id) for step in manifest.steps} <= lowered_ids
            else:
                assert all(step.request.base_url_source is not None for step in manifest.steps)
            exercised.add((str(catalogue.id), str(capability.id)))

    assert len({catalogue_id for catalogue_id, _capability_id in exercised}) == 9
    assert len(exercised) == sum(len(load_test_definition_catalogue_v2(path).capabilities) for path in _CATALOGUE_PATHS)


@pytest.mark.parametrize(
    ("mutation", "expected_path"),
    [
        (lambda raw: raw.__setitem__("unknown", True), ""),
        (
            lambda raw: raw["capabilities"].append(raw["capabilities"][0]),
            "/capabilities/5/id",
        ),
        (
            lambda raw: raw["capabilities"][0]["requiredEndpointIds"].__setitem__(0, "missing.endpoint"),
            "/capabilities/0/requiredEndpointIds/0",
        ),
        (
            lambda raw: raw["predefinedInputs"][0]["requiredForCapabilityIds"].__setitem__(0, "missing.capability"),
            "/predefinedInputs/0/requiredForCapabilityIds/0",
        ),
        (
            lambda raw: raw["testDefinitions"][0]["applicability"]["capabilityIds"].__setitem__(
                0, "missing.capability"
            ),
            "/testDefinitions/0/applicability/capabilityIds/0",
        ),
        (
            lambda raw: raw["testDefinitions"][0]["request"].__setitem__("endpointId", "missing.endpoint"),
            "/testDefinitions/0/request/endpointId",
        ),
        (
            lambda raw: raw["testDefinitions"][0]["request"].__setitem__("method", "GET"),
            "/testDefinitions/0/request/method",
        ),
        (
            lambda raw: raw["testDefinitions"][0]["request"].__setitem__("path", "/wrong-operation"),
            "/testDefinitions/0/request/path",
        ),
        (
            lambda raw: raw["testDefinitions"][0]["request"]["inputBindings"][0].__setitem__(
                "inputId", "missing.input"
            ),
            "/testDefinitions/0/request/inputBindings/0/inputId",
        ),
        (
            lambda raw: raw["testDefinitions"][1]["dependencies"].__setitem__(0, "missing.test"),
            "/testDefinitions/1/dependencies/0",
        ),
        (
            lambda raw: raw["testDefinitions"][0]["assertions"][2].__setitem__("schemaSourceId", "missing.source"),
            "/testDefinitions/0/assertions/2/schemaSourceId",
        ),
    ],
)
def test_v2_catalogue_rejects_unknown_duplicate_and_broken_references(
    mutation: object,
    expected_path: str,
) -> None:
    raw = _raw_catalogue("pis", "v4_0_1")
    mutation(raw)  # type: ignore[operator]  # parametrized mutation fixture

    with pytest.raises(ConfigurationContractError) as captured:
        parse_test_definition_catalogue_v2(raw)

    assert any(item.instance_path == expected_path for item in captured.value.diagnostics)


def test_v2_catalogue_rejects_capability_and_test_dependency_cycles() -> None:
    raw = _raw_catalogue("vrp", "v4_0_1")
    raw["capabilities"][0]["requiredCapabilityIds"] = [raw["capabilities"][1]["id"]]
    raw["capabilities"][1]["requiredCapabilityIds"] = [raw["capabilities"][0]["id"]]

    with pytest.raises(ConfigurationContractError) as capability_error:
        parse_test_definition_catalogue_v2(raw)

    assert any(item.code is DiagnosticCode.DEPENDENCY_CYCLE for item in capability_error.value.diagnostics)

    raw = _raw_catalogue("pis", "v4_0_1")
    first = raw["testDefinitions"][0]
    second = raw["testDefinitions"][1]
    first["dependencies"] = [second["id"]]
    second["dependencies"] = [first["id"]]

    with pytest.raises(ConfigurationContractError) as test_error:
        parse_test_definition_catalogue_v2(raw)

    assert any(item.code is DiagnosticCode.DEPENDENCY_CYCLE for item in test_error.value.diagnostics)


def test_v2_catalogue_rejects_broken_technical_source_and_output_dataflow() -> None:
    raw = _raw_catalogue("dcr", "v3_4")
    raw["endpoints"][0]["sourceId"] = "missing.source"

    with pytest.raises(ConfigurationContractError) as source_error:
        parse_test_definition_catalogue_v2(raw)

    assert any(item.instance_path == "/endpoints/0/sourceId" for item in source_error.value.diagnostics)

    raw = _raw_catalogue("dcr", "v3_4")
    definition = next(item for item in raw["testDefinitions"] if item["request"].get("stateBindings"))
    definition["request"]["stateBindings"][0]["outputId"] = "missing.output"

    with pytest.raises(ConfigurationContractError) as output_error:
        parse_test_definition_catalogue_v2(raw)

    assert any(
        item.instance_path.endswith("/request/stateBindings/0/outputId") for item in output_error.value.diagnostics
    )


def test_v2_catalogue_rejects_incompatible_transforms_and_undeclared_placeholders() -> None:
    raw = _raw_catalogue("pis", "v3_1_11")
    binding = next(
        binding
        for definition in raw["testDefinitions"]
        for binding in definition["request"]["inputBindings"]
        if binding["inputId"] == "pis.v311.input.standing-order-frequency"
    )
    assert binding["transform"] == "identity-string"
    binding["transform"] = "standing-order-frequency-object"

    with pytest.raises(ConfigurationContractError) as incompatible:
        parse_test_definition_catalogue_v2(raw)

    assert any(
        item.code is DiagnosticCode.RULE_INCONSISTENT and item.instance_path.endswith("/transform")
        for item in incompatible.value.diagnostics
    )

    raw = _raw_catalogue("cbpii", "v4_0_1")
    definition = raw["testDefinitions"][0]
    definition["request"]["inputBindings"] = [
        item
        for item in definition["request"]["inputBindings"]
        if item["inputId"] != "cbpii.v401.input.debtor-account-name"
    ]

    with pytest.raises(ConfigurationContractError) as undeclared:
        parse_test_definition_catalogue_v2(raw)

    assert any(
        item.code is DiagnosticCode.RULE_INCONSISTENT
        and item.instance_path.endswith("/jsonBodyTemplate/Data/DebtorAccount/Name")
        for item in undeclared.value.diagnostics
    )

    raw = _raw_catalogue("vrp", "v4_0_1")
    raw["testDefinitions"][0]["request"]["modifications"] = [
        {
            "generator": "unsupported-request-generator",
            "id": "vrp.v401.modification.unsupported",
            "location": "json-body",
            "operation": "generate",
            "target": "/",
        }
    ]

    with pytest.raises(ConfigurationContractError) as unsupported:
        parse_test_definition_catalogue_v2(raw)

    assert any(
        item.code is DiagnosticCode.RULE_INCONSISTENT and item.instance_path.endswith("/generator")
        for item in unsupported.value.diagnostics
    )

    raw = _raw_catalogue("vrp", "v4_0_1")
    del raw["testDefinitions"][9]["request"]["requiredTokenId"]

    with pytest.raises(ConfigurationContractError) as incomplete_authorization:
        parse_test_definition_catalogue_v2(raw)

    assert any(
        item.code is DiagnosticCode.RULE_INCONSISTENT and item.instance_path.endswith("/requiredTokenId")
        for item in incomplete_authorization.value.diagnostics
    )


def test_v2_catalogue_rejects_request_header_playback_without_a_header_template() -> None:
    raw = _raw_catalogue("ais", "v4_0_1")
    definition = next(
        item for item in raw["testDefinitions"] if item["id"].endswith("statements.interaction-id-playback")
    )
    definition["request"]["headerTemplates"] = []

    with pytest.raises(ConfigurationContractError) as captured:
        parse_test_definition_catalogue_v2(raw)

    assert any(
        item.code is DiagnosticCode.RULE_INCONSISTENT and item.instance_path.endswith("/headerName")
        for item in captured.value.diagnostics
    )


def test_vrp_v401_migration_requests_are_complete_and_bound_to_created_consent() -> None:
    release = load_suite_release_v2(_RELEASE_PATH)
    catalogue = load_test_definition_catalogue_v2(_ROOT / "catalogues" / "vrp" / "v4_0_1" / "test-catalogue.v2.json")
    definitions = {str(item.id): item for item in catalogue.test_definitions}
    create = definitions["vrp.v401.test.consent-create.positive"]
    replacement = definitions["vrp.v401.test.consent-replace.positive"]
    patch = definitions["vrp.v401.test.consent-patch.positive"]

    assert create.outputs[0].json_pointer == "/Data/ConsentId"
    assert replacement.request.json_body_template == create.request.json_body_template
    assert replacement.request.state_bindings[0].output_id == create.outputs[0].id
    assert patch.request.state_bindings[0].output_id == create.outputs[0].id
    assert patch.request.content_type == "application/json-patch+json"
    assert cast(Any, patch.request.json_body_template) == (
        {
            "op": "replace",
            "path": "/Data/ControlParameters/VRPType",
            "value": ("UK.OBIE.VRPType.Sweeping",),
        },
    )
    for definition in (replacement, patch):
        assert definition.request.required_token_id == "vrp-payment-access"  # noqa: S105 - semantic token ID
        assert definition.request.detached_jws is not None
        assert {item.name for item in definition.request.header_templates} == {
            "x-fapi-interaction-id",
            "x-idempotency-key",
        }

    plan = parse_participant_plan_v2(
        _participant_document(
            release.id,
            catalogue,
            selected_capability_ids=(StableId("vrp.v401.capability.consent-version-patch"),),
        )
    )
    manifest = generate_execution_manifest_v2(
        compile_participant_plan_v2(release, catalogue, plan),
        catalogue,
    )
    runtime_inputs = dict(_participant_input_runtime_values(plan, scope="vrp"))
    runtime_inputs["resourceBaseUrl"] = "https://rs.example.com"
    lowered = _execution_manifest_to_runtime_manifest(
        cast(Any, manifest),
        runtime_inputs=runtime_inputs,
        runtime_input_base_dir=REPO_ROOT,
        runtime_config=RuntimeConfig(),
        artifact_resolver=preflight_suite_release_artifacts(manifest, release),
    )
    lowered_patch = next(step for step in lowered.steps if step.id.endswith("consent-patch.positive.instance.request"))
    request = cast(Any, lowered_patch).request
    assert "${steps.vrp.v401.test.consent-create.positive.instance.request.response.body.Data.ConsentId}" in request.url
    assert request.headers["Content-Type"] == "application/json-patch+json"
    assert request.body.value == [
        {
            "op": "replace",
            "path": "/Data/ControlParameters/VRPType",
            "value": ["UK.OBIE.VRPType.Sweeping"],
        }
    ]


def test_catalogue_security_profile_mismatch_is_a_compiler_finding() -> None:
    release = load_suite_release_v2(_RELEASE_PATH)
    catalogue = load_test_definition_catalogue_v2(_ROOT / "catalogues" / "ais" / "v4_0_1" / "test-catalogue.v2.json")
    raw = _participant_document(release.id, catalogue)
    raw["securityProfile"] = "all"
    plan = parse_participant_plan_v2(raw)

    with pytest.raises(V2ParticipantPlanCompilationError) as captured:
        compile_participant_plan_v2(release, catalogue, plan)

    assert "plan.reference.security-profile-unsupported" in str(captured.value)


@pytest.mark.parametrize(
    ("family", "version"),
    [
        ("ais", "v3_1_11"),
        ("ais", "v4_0_1"),
        ("pis", "v3_1_11"),
        ("pis", "v4_0_1"),
        ("cbpii", "v3_1_11"),
        ("cbpii", "v4_0_1"),
        ("vrp", "v3_1_11"),
        ("vrp", "v4_0_1"),
        ("dcr", "v3_4"),
    ],
)
def test_v2_compile_and_manifest_are_deterministic_for_every_supported_family(
    family: str,
    version: str,
) -> None:
    release = load_suite_release_v2(_RELEASE_PATH)
    catalogue = load_test_definition_catalogue_v2(_ROOT / "catalogues" / family / version / "test-catalogue.v2.json")
    plan = parse_participant_plan_v2(_participant_document(release.id, catalogue))

    first = compile_participant_plan_v2(release, catalogue, plan)
    second = compile_participant_plan_v2(release, catalogue, plan)
    first_manifest = generate_execution_manifest_v2(first, catalogue)
    second_manifest = generate_execution_manifest_v2(second, catalogue)

    assert dump_resolved_plan_v2(first) == dump_resolved_plan_v2(second)
    assert dump_execution_manifest_v2(first_manifest) == dump_execution_manifest_v2(second_manifest)
    assert first_manifest.steps
    definitions = {item.id: item for item in catalogue.test_definitions}
    for step in first_manifest.steps:
        request = cast(list[JsonValue], catalogue_to_document(catalogue)["testDefinitions"])
        assert isinstance(request, list)
        definition_document = next(
            item for item in request if isinstance(item, dict) and item["id"] == str(step.test_definition_id)
        )
        catalogue_request = dict(cast(JsonObject, definition_document["request"]))
        catalogue_request.pop("endpointId")
        assert (
            cast(list[JsonObject], execution_manifest_to_document(first_manifest)["steps"])[
                list(first_manifest.steps).index(step)
            ]["request"]
            == catalogue_request
        )
        assert step.request.base_url_source is not None
        assert step.request.method is definitions[step.test_definition_id].request.method
        assert step.request.path == definitions[step.test_definition_id].request.path
    assert all(
        step.evidence
        == catalogue.test_definitions[
            next(
                index
                for index, definition in enumerate(catalogue.test_definitions)
                if definition.id == step.test_definition_id
            )
        ].evidence_policy
        for step in first_manifest.steps
    )
    serialized = dump_resolved_plan_v2(first) + dump_execution_manifest_v2(first_manifest)
    assert not any(
        key in serialized
        for key in (
            "requirementsScope",
            "requirementsCatalogueId",
            "coveredRequirementIds",
            "normativeReferenceIds",
        )
    )


def test_v2_parsers_round_trip_without_legacy_contract_modules() -> None:
    assert not any((_ROOT / name).exists() for name in ("loader.py", "compiler.py", "execution_manifest.py"))
    release = load_suite_release_v2(_RELEASE_PATH)
    catalogue = load_test_definition_catalogue_v2(_ROOT / "catalogues" / "pis" / "v4_0_1" / "test-catalogue.v2.json")
    plan = parse_participant_plan_v2(_participant_document(release.id, catalogue))
    resolved = compile_participant_plan_v2(release, catalogue, plan)
    manifest = generate_execution_manifest_v2(resolved, catalogue)

    assert parse_resolved_plan(resolved_plan_to_document(resolved)) == resolved
    assert parse_execution_manifest(execution_manifest_to_document(manifest)) == manifest
    assert parse_test_definition_catalogue_v2(catalogue_to_document(catalogue)) == catalogue


def test_sensitive_values_are_masked_in_plan_manifest_export_and_result_trace() -> None:
    release = load_suite_release_v2(_RELEASE_PATH)
    catalogue = load_test_definition_catalogue_v2(_ROOT / "catalogues" / "pis" / "v4_0_1" / "test-catalogue.v2.json")
    plan = parse_participant_plan_v2(_participant_document(release.id, catalogue))
    resolved = compile_participant_plan_v2(release, catalogue, plan)
    manifest = generate_execution_manifest_v2(resolved, catalogue)
    snapshot = build_safe_participant_plan_snapshot(plan, catalogue)
    sensitive = {str(item.id) for item in catalogue.predefined_inputs if item.sensitivity != "non-sensitive"}

    assert sensitive
    assert all(item.value is None for item in resolved.predefined_inputs if str(item.id) in sensitive)
    assert all(item.value is None for item in manifest.inputs if str(item.id) in sensitive)
    assert not sensitive.intersection(
        {
            str(item["inputId"])
            for item in cast(list[JsonValue], snapshot["predefinedInputs"])
            if isinstance(item, dict) and not item["redacted"]
        }
    )
    source = ResultTraceabilitySource(manifest, resolved, snapshot)
    result = build_smoke_check_result(
        [
            StepResult(
                name=str(step.id),
                status="passed",
                message="Passed",
            )
            for step in manifest.steps
        ],
        started_at=datetime.now(UTC),
        result_traceability=source,
    ).to_json_object()
    trace = result["traceability"]
    assert isinstance(trace, dict)
    assert "requirements" not in json.dumps(participant_plan_to_document(plan))
    serialized_trace = json.dumps(trace)
    assert "requirements" not in serialized_trace.lower()
    assert "normativeReferenceIds" not in serialized_trace
    assert "coveredRequirementIds" not in serialized_trace
    trace_manifest = cast(JsonObject, trace["executionManifest"])
    assert all("assertionIds" in cast(JsonObject, step) for step in cast(list[JsonValue], trace_manifest["steps"]))


def test_v2_preflight_rejects_release_substitution_and_stale_bytes() -> None:
    release = load_suite_release_v2(_RELEASE_PATH)
    catalogue = load_test_definition_catalogue_v2(_ROOT / "catalogues" / "dcr" / "v3_4" / "test-catalogue.v2.json")
    plan = parse_participant_plan_v2(_participant_document(release.id, catalogue))
    manifest = generate_execution_manifest_v2(
        compile_participant_plan_v2(release, catalogue, plan),
        catalogue,
    )

    assert len(preflight_suite_release_artifacts(manifest, release).resolved_artifacts) == len(release.artifacts)
    with pytest.raises(SuiteReleaseArtifactError) as substituted:
        preflight_suite_release_artifacts(
            manifest,
            replace(release, release_version="substituted"),
        )
    assert substituted.value.code is SuiteReleaseArtifactErrorCode.RELEASE_MISMATCH

    changed = replace(
        release.artifacts[0],
        digest=type(release.artifacts[0].digest)("sha256:" + ("0" * 64)),
    )
    stale_release = replace(release, artifacts=(changed, *release.artifacts[1:]))
    stale_manifest = replace(
        manifest,
        provenance=replace(manifest.provenance, artifacts=stale_release.artifacts),
    )
    from conformance.configuration_contracts.v2_loader import execution_manifest_id

    stale_manifest = replace(stale_manifest, id=execution_manifest_id(stale_manifest))
    with pytest.raises(SuiteReleaseArtifactError) as stale:
        preflight_suite_release_artifacts(stale_manifest, stale_release)
    assert stale.value.code is SuiteReleaseArtifactErrorCode.DIGEST_MISMATCH


def _raw_catalogue(family: str, version: str) -> Any:
    """Return mutable fixture JSON; Any is intentional for negative-shape mutations."""
    return json.loads((_ROOT / "catalogues" / family / version / "test-catalogue.v2.json").read_text())


def _participant_document(
    release_id: object,
    catalogue: Any,
    *,
    selected_capability_ids: tuple[StableId, ...] | None = None,
) -> JsonObject:
    """Build fixture intent; Any permits the immutable catalogue fixture model."""
    capabilities = catalogue.capabilities
    selected = tuple(item.id for item in capabilities) if selected_capability_ids is None else selected_capability_ids
    required = set(selected)
    pending = list(selected)
    by_id = {item.id: item for item in capabilities}
    while pending:
        for dependency in by_id[pending.pop()].required_capability_ids:
            if dependency not in required:
                required.add(dependency)
                pending.append(dependency)
    inputs: list[JsonValue] = []
    for item in catalogue.predefined_inputs:
        if required.intersection(item.required_for_capability_ids):
            inputs.append(
                {
                    "inputId": str(item.id),
                    "value": _json_value(item.example_value),
                }
            )
    return {
        "documentType": "participant-plan",
        "id": f"participant.test.{catalogue.specification.test_scope}.{catalogue.specification.version}",
        "predefinedInputs": inputs,
        "schemaVersion": "2.0",
        "scheme": str(catalogue.scheme),
        "securityProfile": ("all" if str(catalogue.specification.test_scope) == "dcr" else "fapi1-advanced"),
        "selectedCapabilityIds": [str(item) for item in selected],
        "specification": {
            "id": str(catalogue.specification.id),
            "testScope": str(catalogue.specification.test_scope),
            "version": catalogue.specification.version,
        },
        "suiteReleaseId": str(release_id),
    }


def _json_value(value: object) -> JsonValue:
    if hasattr(value, "frequency_type"):
        frequency = cast(Any, value)  # fixture model is intentionally duck-typed across v1/v2 aliases
        return {
            "frequencyType": frequency.frequency_type,
            **({"countPerPeriod": frequency.count_per_period} if frequency.count_per_period is not None else {}),
            **({"pointInTime": frequency.point_in_time} if frequency.point_in_time is not None else {}),
        }
    return cast(JsonValue, value)
