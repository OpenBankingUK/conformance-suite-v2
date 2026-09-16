"""Unit tests for compiled catalogue execution."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from conformance.catalogue import compile_test_plan
from conformance.context import RuntimeConfig
from conformance.executor import run_compiled_test_plan
from conformance.masking import MASKED_VALUE
from tests.support.catalogue_plans import build_plan_spec as _plan_spec
from tests.support.catalogue_plans import build_test_catalogue as _test_catalogue

pytestmark = pytest.mark.unit


def test_run_compiled_plan_preserves_masked_evidence_and_catalogue_trace(tmp_path: Path) -> None:
    requested_headers: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_headers.append(request.headers.get("authorization"))
        return httpx.Response(200, json={"Data": {}}, headers={"x-fapi-interaction-id": "interaction-1"})

    compiled_plan = compile_test_plan(_test_catalogue(), _plan_spec())
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = run_compiled_test_plan(
            compiled_plan,
            runtime_inputs=_plan_spec().runtime_inputs,
            runtime_input_base_dir=tmp_path,
            client=client,
            runtime_config=RuntimeConfig(
                discovery_url="https://auth.example.com/.well-known/openid-configuration",
            ),
        )

    result_json = result.to_json_object()
    catalogue_evidence = result_json["catalogue"]
    steps = result_json["steps"]
    assert isinstance(catalogue_evidence, dict)
    assert isinstance(steps, list)
    step = steps[0]
    assert isinstance(step, dict)
    runtime_input_snapshot = catalogue_evidence["runtimeInputSnapshot"]
    assert isinstance(runtime_input_snapshot, list)
    sensitive_runtime_input = runtime_input_snapshot[1]
    assert isinstance(sensitive_runtime_input, dict)
    step_details = step["details"]
    assert isinstance(step_details, dict)
    request_details = step_details["request"]
    assert isinstance(request_details, dict)
    request_headers = request_details["headers"]
    assert isinstance(request_headers, dict)
    step_catalogue = step_details["catalogue"]
    assert isinstance(step_catalogue, dict)

    assert requested_headers == ["Bearer secret-access-token"]
    assert result_json["status"] == "passed"
    assert catalogue_evidence["generatedTestCaseIds"] == ["accounts-read"]
    assert catalogue_evidence["selectedCapabilities"] == [
        {
            "method": "GET",
            "path": "/open-banking/v4.0/aisp/accounts",
            "capabilityId": "accounts.read",
            "label": "Read accounts",
            "required": True,
        }
    ]
    assert "value" not in sensitive_runtime_input
    assert request_headers["Authorization"] == MASKED_VALUE
    assert step_catalogue["testCaseId"] == "accounts-read"
    assert step_catalogue["role"] == "resource"
    assert step_catalogue["complianceScope"] == ["legacy-fcs-script:test#accounts"]
