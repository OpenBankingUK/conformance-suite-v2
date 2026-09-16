"""Adversarial tests for immutable execution-manifest authority."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import httpx
import pytest

from conformance.configuration_contracts import (
    ExecutionManifestAssertion,
    ExecutionManifestHeader,
    HttpMethod,
    LegacyExecutionEngine,
    PreparedExecutionManifest,
    RequestBaseUrlSource,
    StableId,
    SuiteReleaseArtifactError,
    execution_manifest_id,
    load_execution_manifest,
    load_participant_plan,
    load_requirements_catalogue,
    load_resolved_plan,
    load_suite_release,
    preflight_suite_release_artifacts,
)
from conformance.executor import _execution_manifest_to_runtime_manifest, run_execution_manifest
from conformance.json_types import JsonObject
from conformance.manifest import JsonBody, ManifestStep
from conformance.participant_surface import prepare_participant_plan_for_run
from conformance.results import ResultTraceabilitySource, build_safe_participant_plan_snapshot
from tests.support.paths import REPO_ROOT

pytestmark = pytest.mark.unit

_BUNDLE_ROOT = REPO_ROOT / "conformance" / "configuration_contracts" / "bundles" / "pis-domestic-standing-order-v4_0"
_FIXTURE_ROOT = REPO_ROOT / "tests" / "fixtures" / "configuration_contracts" / "v1"


@pytest.mark.parametrize(
    ("method", "path", "header", "body"),
    [
        ("POST", "/payments/one", "one", {"Data": {"value": "one"}}),
        ("PUT", "/payments/two", "two", {"Data": {"value": "two"}}),
    ],
)
def test_manifest_request_mutation_changes_the_outbound_request(
    tmp_path: Path,
    method: str,
    path: str,
    header: str,
    body: JsonObject,
) -> None:
    prepared = _prepared_manifest(
        tmp_path,
        method=method,
        path=path,
        header=header,
        body=body,
    )
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(201, json={"accepted": True})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = run_execution_manifest(prepared, client=client)

    assert result.status == "passed"
    assert len(result.steps) == 1
    assert result.steps[0].name == str(prepared.manifest.steps[0].id)
    assert len(captured) == 1
    assert captured[0].method == method
    assert captured[0].url.path == path
    assert captured[0].headers["x-manifest-test"] == header
    assert json.loads(captured[0].content) == body


def test_manifest_assertion_mutation_changes_outcome_and_reported_id(tmp_path: Path) -> None:
    prepared = _prepared_manifest(
        tmp_path,
        method="POST",
        path="/payments",
        header="value",
        body={"Data": {"value": "one"}},
        expected_status=202,
        assertion_id="assertion-mutated",
    )

    with httpx.Client(
        transport=httpx.MockTransport(lambda _request: httpx.Response(201, json={"accepted": True}))
    ) as client:
        result = run_execution_manifest(prepared, client=client)

    assert result.status == "failed"
    assertions = result.steps[0].details["assertions"]
    assert assertions == [
        {
            "assertionId": "assertion-mutated",
            "status": "failed",
            "message": "Expected HTTP status 202, got 201",
        }
    ]


def test_stale_manifest_identity_fails_before_network_execution(tmp_path: Path) -> None:
    prepared = _prepared_manifest(
        tmp_path,
        method="POST",
        path="/payments",
        header="value",
        body={"Data": {}},
    )
    stale = replace(
        prepared.manifest,
        steps=(
            replace(
                prepared.manifest.steps[0],
                request=replace(prepared.manifest.steps[0].request, path="/tampered"),
            ),
        ),
    )
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(201, json={})

    with pytest.raises(SuiteReleaseArtifactError, match="manifest-id-mismatch"):
        preflight_suite_release_artifacts(
            stale,
            load_suite_release(_BUNDLE_ROOT / "suite-release.json"),
        )
    with httpx.Client(transport=httpx.MockTransport(handler)):
        pass
    assert calls == 0


def test_failed_dependency_skips_request_without_reporting_assertions(tmp_path: Path) -> None:
    prepared = _prepared_manifest(
        tmp_path,
        method="POST",
        path="/first",
        header="value",
        body={"Data": {}},
    )
    first = prepared.manifest.steps[0]
    second = replace(
        first,
        id=StableId("manifest-second"),
        test_instance_id=StableId("manifest-second.instance"),
        test_definition_id=StableId("manifest-second.definition"),
        dependency_ids=(first.id,),
        request=replace(first.request, path="/second"),
        assertions=(
            ExecutionManifestAssertion(
                id=StableId("assertion-never-evaluated"),
                type="http-status",
                expected_status=200,
            ),
        ),
    )
    provisional = replace(prepared.manifest, steps=(first, second))
    manifest = replace(provisional, id=execution_manifest_id(provisional))
    assert prepared.result_traceability is not None
    prepared = replace(
        prepared,
        manifest=manifest,
        artifact_resolver=preflight_suite_release_artifacts(
            manifest,
            load_suite_release(_BUNDLE_ROOT / "suite-release.json"),
        ),
        result_traceability=replace(
            prepared.result_traceability,
            execution_manifest=manifest,
        ),
    )
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(500, json={"error": "first failed"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = run_execution_manifest(prepared, client=client)

    assert calls == ["/first"]
    assert [step.status for step in result.steps] == ["failed", "skipped"]
    assert "assertions" not in result.steps[1].details


def test_materialized_sensitive_bindings_remain_masked_and_ais_consent_is_signed(tmp_path: Path) -> None:
    pis_plan = json.loads(
        (
            REPO_ROOT
            / "tests"
            / "fixtures"
            / "configuration_contracts"
            / "pis"
            / "v4_0_1"
            / "participant-plan.surface.json"
        ).read_text(encoding="utf-8")
    )
    pis_prepared = prepare_participant_plan_for_run(pis_plan, base_dir=tmp_path)
    pis_runtime = _execution_manifest_to_runtime_manifest(
        pis_prepared.execution_manifest,
        runtime_inputs=pis_prepared.runtime_inputs,
        runtime_input_base_dir=tmp_path,
        runtime_config=None,
        artifact_resolver=pis_prepared.prepared_execution.artifact_resolver,
    )
    sensitive_body = next(
        step.request.body
        for step in pis_runtime.steps
        if isinstance(step, ManifestStep)
        and isinstance(step.request.body, JsonBody)
        and "/Data/Initiation/CreditorAccount/Identification" in step.request.body.sensitive_json_pointers
    )
    assert isinstance(sensitive_body, JsonBody)
    assert set(sensitive_body.sensitive_json_pointers) == {
        "/Data/Initiation/CreditorAccount/Identification",
        "/Data/Initiation/CreditorAccount/Name",
    }

    ais_plan = json.loads(
        (
            REPO_ROOT
            / "tests"
            / "fixtures"
            / "configuration_contracts"
            / "ais"
            / "v4_0_1"
            / "participant-plan.surface.json"
        ).read_text(encoding="utf-8")
    )
    ais_prepared = prepare_participant_plan_for_run(ais_plan, base_dir=tmp_path)
    consent_step = next(
        step
        for step in ais_prepared.execution_manifest.steps
        if step.request.path.endswith("/account-access-consents")
        and any(modification.generator == "ais-account-access-consent" for modification in step.request.modifications)
    )
    assert consent_step.request.detached_jws is not None
    assert consent_step.request.detached_jws.profile.value == "legacy-b64-false"

    vrp_plan = json.loads(
        (
            REPO_ROOT / "tests" / "fixtures" / "configuration_contracts" / "vrp" / "v4_0_1" / "participant-plan.json"
        ).read_text(encoding="utf-8")
    )
    vrp_plan["suiteReleaseId"] = "obl.open-banking-mvp.catalogue-release"
    vrp_plan["selectedCapabilityIds"] = ["vrp.v401.capability.domestic-vrp"]
    vrp_plan["executionConfiguration"] = {
        "compatibilityRuntimeInputs": {"resourceBaseUrl": "https://rs.example.com"},
        "securityEnvironment": {"discoveryUrl": "https://as.example.com/.well-known/openid-configuration"},
        "dynamicClientRegistration": {},
        "metadata": {},
    }
    vrp_prepared = prepare_participant_plan_for_run(vrp_plan, base_dir=tmp_path)
    vrp_profiles = {
        step.request.detached_jws.profile.value
        for step in vrp_prepared.execution_manifest.steps
        if step.request.detached_jws is not None
    }
    assert vrp_profiles == {"ob-v3.1.4+"}


def test_form_body_template_is_sent_exactly(tmp_path: Path) -> None:
    prepared = _prepared_manifest(
        tmp_path,
        method="POST",
        path="/token",
        header="value",
        body={"unused": True},
    )
    request = replace(
        prepared.manifest.steps[0].request,
        content_type="application/x-www-form-urlencoded",
        json_body_template=None,
        form_body_template={
            "grant_type": "client_credentials",
            "scope": "${runtime.scope}",
        },
        runtime_input_refs=("resourceBaseUrl", "scope"),
    )
    step = replace(prepared.manifest.steps[0], request=request)
    provisional = replace(prepared.manifest, steps=(step,))
    manifest = replace(provisional, id=execution_manifest_id(provisional))
    assert prepared.result_traceability is not None
    prepared = replace(
        prepared,
        manifest=manifest,
        runtime_inputs={"resourceBaseUrl": "https://api.example.com", "scope": "payments"},
        artifact_resolver=preflight_suite_release_artifacts(
            manifest,
            load_suite_release(_BUNDLE_ROOT / "suite-release.json"),
        ),
        result_traceability=replace(prepared.result_traceability, execution_manifest=manifest),
    )
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(201, json={})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = run_execution_manifest(prepared, client=client)

    assert result.status == "passed"
    assert len(captured) == 1
    assert captured[0].headers["content-type"].startswith("application/x-www-form-urlencoded")
    assert captured[0].content == b"grant_type=client_credentials&scope=payments"


def _prepared_manifest(
    tmp_path: Path,
    *,
    method: str,
    path: str,
    header: str,
    body: JsonObject,
    expected_status: int = 201,
    assertion_id: str = "assertion-status",
) -> PreparedExecutionManifest:
    source = load_execution_manifest(_FIXTURE_ROOT / "execution-manifest.valid.json")
    source_step = source.steps[0]
    request = replace(
        source_step.request,
        method=HttpMethod(method),
        path=path,
        input_bindings=(),
        modifications=(),
        state_bindings=(),
        content_type="application/json",
        base_url_source=RequestBaseUrlSource.RESOURCE,
        query_templates={},
        header_templates=(ExecutionManifestHeader(name="x-manifest-test", literal_value=header),),
        json_body_template=body,
        form_body_template=None,
        runtime_input_refs=("resourceBaseUrl",),
        generated_values={},
        required_token_id=None,
        required_token_scope=None,
        produced_token_id=None,
        detached_jws=None,
        token_endpoint_auth=None,
        response_signature=None,
        psu_authorization=None,
        required_psu_authorization_step_id=None,
    )
    step = replace(
        source_step,
        dependency_ids=(),
        request=request,
        assertions=(
            ExecutionManifestAssertion(
                id=StableId(assertion_id),
                type="http-status",
                expected_status=expected_status,
            ),
        ),
        outputs=(),
    )
    provisional = replace(source, steps=(step,))
    manifest = replace(provisional, id=execution_manifest_id(provisional))
    release = load_suite_release(_BUNDLE_ROOT / "suite-release.json")
    resolved = load_resolved_plan(_FIXTURE_ROOT / "resolved-plan.valid.json")
    participant = load_participant_plan(_FIXTURE_ROOT / "participant-plan.valid.json")
    requirements = load_requirements_catalogue(_BUNDLE_ROOT / "requirements.json")
    return PreparedExecutionManifest(
        manifest=manifest,
        engine=LegacyExecutionEngine.READ_WRITE,
        runtime_inputs={"resourceBaseUrl": "https://api.example.com"},
        runtime_input_base_dir=tmp_path,
        artifact_resolver=preflight_suite_release_artifacts(manifest, release),
        result_traceability=ResultTraceabilitySource(
            execution_manifest=manifest,
            resolved_plan=resolved,
            participant_plan_snapshot=build_safe_participant_plan_snapshot(participant, requirements),
        ),
    )
