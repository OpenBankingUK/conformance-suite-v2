"""Golden characterization of the current participant-plan-to-result pipeline."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest

from conformance.approved_releases import APPROVED_RELEASE_POLICY_SCHEMA_VERSION, ApprovedReleasePolicy
from conformance.catalogue import CompiledTestPlan, PlanDocumentV2, compile_test_plan_document, parse_test_plan_document
from conformance.catalogue_registry import supported_catalogues
from conformance.context import RuntimeConfig
from conformance.executor import _compiled_plan_to_manifest
from conformance.json_types import JsonObject, JsonValue
from conformance.manifest import (
    FormBody,
    GeneratedRequestObject,
    HttpStatusAssertion,
    JsonBody,
    Manifest,
    ManifestAssertion,
    PsuAuthorizationStep,
    V1Step,
)
from conformance.results import StepResult, build_smoke_check_result
from conformance.version import CONFORMANCE_TOOL_VERSION_ENV
from tests.support.paths import REPO_ROOT

pytestmark = pytest.mark.unit

_FIXTURE_ROOT = REPO_ROOT / "tests" / "fixtures" / "current_pipeline"
_CHARACTERIZATION_TOOL_VERSION = "0.1.0-characterization"
_GENERATED_INVALID_RESOURCE_ID = re.compile(r"invalid-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


@pytest.mark.parametrize(
    "journey",
    [
        "pis_standing_order",
        "ais_account_transactions",
        "dcr_registration_management",
    ],
)
def test_current_pipeline_matches_golden_fixture(
    journey: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Freeze representative current parsing, compilation, manifest, and result behaviour."""
    monkeypatch.setenv(CONFORMANCE_TOOL_VERSION_ENV, _CHARACTERIZATION_TOOL_VERSION)

    assert _pipeline_snapshot(journey, runtime_input_base_dir=tmp_path) == _load_fixture(f"{journey}.golden.json")


def _pipeline_snapshot(journey: str, *, runtime_input_base_dir: Path) -> JsonObject:
    raw_plan = _load_fixture(f"{journey}.plan.json")
    document = parse_test_plan_document(raw_plan)
    assert isinstance(document, PlanDocumentV2)
    compiled_plan = compile_test_plan_document(document, supported_catalogues())
    manifest = _compiled_plan_to_manifest(
        compiled_plan,
        runtime_inputs=document.runtime_inputs,
        runtime_input_base_dir=runtime_input_base_dir,
        runtime_config=RuntimeConfig(
            discovery_url=cast(str, document.security_environment["discoveryUrl"]),
        ),
    )
    result_steps = _passing_result_steps(compiled_plan, manifest)
    eligible_result = build_smoke_check_result(
        list(result_steps),
        started_at=datetime(2026, 9, 14, tzinfo=UTC),
        approved_release_policy=ApprovedReleasePolicy(
            schema_version=APPROVED_RELEASE_POLICY_SCHEMA_VERSION,
            approved_tool_versions=(_CHARACTERIZATION_TOOL_VERSION,),
        ),
        certification_coverage="complete",
        compiled_plan=compiled_plan,
        non_certifying_reasons=compiled_plan.traceability.non_certifying_reasons,
    ).to_json_object()
    result_without_policy = build_smoke_check_result(
        list(result_steps),
        started_at=datetime(2026, 9, 14, tzinfo=UTC),
        certification_coverage="complete",
        compiled_plan=compiled_plan,
        non_certifying_reasons=compiled_plan.traceability.non_certifying_reasons,
    ).to_json_object()
    return {
        "parsedPlan": _parsed_plan_snapshot(document),
        "compiledPlan": _compiled_plan_snapshot(compiled_plan),
        "syntheticManifest": _manifest_snapshot(manifest),
        "result": _result_snapshot(eligible_result, result_without_policy=result_without_policy),
    }


def _load_fixture(name: str) -> JsonObject:
    decoded: object = json.loads((_FIXTURE_ROOT / name).read_text(encoding="utf-8"))
    assert isinstance(decoded, dict)
    return cast(JsonObject, decoded)


def _parsed_plan_snapshot(document: PlanDocumentV2) -> JsonObject:
    return {
        "schemaVersion": document.schema_version,
        "boundary": {
            "scheme": document.scheme,
            "specification": document.specification,
            "version": document.version,
            "securityProfile": document.security_profile,
        },
        "executionMode": document.execution_mode,
        "resourceGroups": [
            {
                "id": group.resource_group_id,
                "label": group.label,
                "selectAll": group.select_all,
                "endpoints": [
                    {
                        "method": endpoint.method,
                        "path": endpoint.path,
                        "capabilities": list(endpoint.capability_ids),
                        "operationId": endpoint.operation_id,
                    }
                    for endpoint in group.endpoints
                ],
            }
            for group in document.resource_groups
        ],
        "endpoints": [
            {
                "method": endpoint.method,
                "path": endpoint.path,
                "capabilities": list(endpoint.capability_ids),
                "operationId": endpoint.operation_id,
                "required": endpoint.required,
                "locked": endpoint.locked,
            }
            for endpoint in document.endpoints
        ],
        "runtimeInputs": {
            input_id: _normalise_generated_values(value) for input_id, value in sorted(document.runtime_inputs.items())
        },
    }


def _compiled_plan_snapshot(compiled_plan: CompiledTestPlan) -> JsonObject:
    traceability = compiled_plan.traceability
    return {
        "catalogue": {
            "standard": compiled_plan.catalogue_key.standard,
            "version": compiled_plan.catalogue_key.version,
            "api": compiled_plan.catalogue_key.api,
            "catalogueVersion": compiled_plan.catalogue_version,
        },
        "certifying": compiled_plan.certifying,
        "selectedEndpoints": [
            {
                "method": endpoint.method,
                "path": endpoint.path,
                "resourceGroup": endpoint.resource_group,
                "capabilities": list(endpoint.capability_ids),
                "operationId": endpoint.operation_id,
            }
            for endpoint in traceability.selected_endpoints
        ],
        "selectedCapabilities": [
            {
                "method": capability.method,
                "path": capability.path,
                "capabilityId": capability.capability_id,
                "required": capability.required,
            }
            for capability in traceability.selected_capabilities
        ],
        "applicabilityDecisions": [
            {
                "testCaseId": decision.test_case_id,
                "selected": decision.selected,
                "reason": decision.reason,
                "dependencyOf": list(decision.dependency_of),
            }
            for decision in traceability.applicability_decisions
        ],
        "testCases": [
            {
                "testCaseId": test_case.test_case_id,
                "mandatory": test_case.mandatory,
                "dependencies": list(test_case.dependencies),
            }
            for test_case in compiled_plan.test_cases
        ],
        "generatedTestCaseIds": list(traceability.generated_test_case_ids),
        "skippedTestCaseIds": [test_case.test_case_id for test_case in compiled_plan.skipped_test_cases],
        "runtimeInputSnapshot": [
            {
                "inputId": runtime_input.input_id,
                "inputType": runtime_input.input_type,
                "required": runtime_input.required,
                "sensitive": runtime_input.sensitive,
                "provided": runtime_input.provided,
                "value": _normalise_generated_values(runtime_input.value),
            }
            for runtime_input in traceability.runtime_input_snapshot
        ],
        "nonCertifyingReasons": list(traceability.non_certifying_reasons),
    }


def _manifest_snapshot(manifest: Manifest) -> JsonObject:
    return {
        "schemaVersion": manifest.schema_version,
        "name": manifest.name,
        "certificationCoverage": manifest.certification_coverage,
        "steps": [_manifest_step_snapshot(step) for step in manifest.steps],
    }


def _manifest_step_snapshot(step: V1Step) -> JsonObject:
    if isinstance(step, PsuAuthorizationStep):
        request_object = step.request_object
        return {
            "id": step.id,
            "kind": "psu-authorization",
            "mode": step.mode,
            "scope": step.scope,
            "mandatory": step.mandatory,
            "phase": step.phase,
            "openbankingIntentId": (
                request_object.openbanking_intent_id if isinstance(request_object, GeneratedRequestObject) else None
            ),
        }

    body: JsonValue | None = None
    if isinstance(step.request.body, JsonBody):
        body = {"encoding": "json", "value": _normalise_generated_values(step.request.body.value)}
    elif isinstance(step.request.body, FormBody):
        body = {"encoding": "form", "fields": dict(step.request.body.fields)}
    detached_jws: JsonObject | None = None
    if step.request.detached_jws is not None:
        omitted_headers: list[JsonValue] = list(step.request.detached_jws.omit_protected_headers)
        detached_jws = {
            "source": step.request.detached_jws.source,
            "profile": step.request.detached_jws.profile,
            "omitProtectedHeaders": omitted_headers,
        }
    header_names = cast("list[JsonValue]", sorted((step.request.headers or {}).keys()))
    return {
        "id": step.id,
        "kind": "http",
        "method": step.request.method,
        "url": _normalise_generated_string(step.request.url),
        "headerNames": header_names,
        "body": body,
        "detachedJws": detached_jws,
        "assertions": [_assertion_snapshot(assertion) for assertion in step.assertions],
        "mandatory": step.mandatory,
        "phase": step.phase,
        "requiredTokenId": step.required_token_id,
        "producesTokenId": step.produces_token_id,
    }


def _assertion_snapshot(assertion: ManifestAssertion) -> JsonObject:
    if isinstance(assertion, HttpStatusAssertion):
        return {
            "type": assertion.type,
            "expected": assertion.expected,
            "expectedOneOf": list(assertion.expected_one_of),
        }
    return {"type": assertion.type}


def _passing_result_steps(compiled_plan: CompiledTestPlan, manifest: Manifest) -> tuple[StepResult, ...]:
    if manifest.steps:
        return tuple(
            StepResult(
                name=step.id,
                status="passed",
                message="Characterization pass",
                mandatory=step.mandatory,
            )
            for step in manifest.steps
        )
    return tuple(
        StepResult(
            name=execution_step.step_id,
            status="passed",
            message="Characterization pass",
            mandatory=test_case.mandatory,
        )
        for test_case in compiled_plan.test_cases
        for execution_step in test_case.execution_steps
    )


def _result_snapshot(result: JsonObject, *, result_without_policy: JsonObject) -> JsonObject:
    catalogue = cast(JsonObject, result["catalogue"])
    trace_groups = cast(list[JsonValue], catalogue.get("traceGroups", []))
    return {
        "status": result["status"],
        "summary": result["summary"],
        "certificationEligibility": result["certificationEligibility"],
        "eligibilityWithoutApprovedReleasePolicy": result_without_policy["certificationEligibility"],
        "steps": [{"name": step["name"], "status": step["status"]} for step in cast(list[JsonObject], result["steps"])],
        "catalogue": {
            "standard": catalogue["standard"],
            "version": catalogue["version"],
            "api": catalogue["api"],
            "catalogueVersion": catalogue["catalogueVersion"],
            "certifying": catalogue["certifying"],
            "generatedTestCaseIds": catalogue["generatedTestCaseIds"],
            "skippedTestCaseIds": catalogue["skippedTestCaseIds"],
            "selectedEndpoints": catalogue["selectedEndpoints"],
            "selectedCapabilities": catalogue["selectedCapabilities"],
            "runtimeInputSnapshot": catalogue["runtimeInputSnapshot"],
            "traceGroups": [_trace_group_snapshot(cast(JsonObject, group)) for group in trace_groups],
        },
    }


def _trace_group_snapshot(trace_group: JsonObject) -> JsonObject:
    return {
        "traceGroupId": trace_group["traceGroupId"],
        "status": trace_group["status"],
        "testCases": [
            {
                "testCaseId": test_case["testCaseId"],
                "status": test_case["status"],
                "steps": [
                    {"stepId": step["stepId"], "status": step["status"]}
                    for step in cast(list[JsonObject], test_case["steps"])
                ],
            }
            for test_case in cast(list[JsonObject], trace_group["testCases"])
        ],
    }


def _normalise_generated_values(value: JsonValue | None) -> JsonValue | None:
    if isinstance(value, str):
        return _normalise_generated_string(value)
    if isinstance(value, list):
        return [_normalise_generated_values(item) for item in value]
    if isinstance(value, dict):
        return {key: _normalise_generated_values(item) for key, item in value.items()}
    return value


def _normalise_generated_string(value: str) -> str:
    if len(value) == 32 and value.isalnum():
        return "<generated-32-character-value>"
    return _GENERATED_INVALID_RESOURCE_ID.sub("<generated-invalid-resource-id>", value)
