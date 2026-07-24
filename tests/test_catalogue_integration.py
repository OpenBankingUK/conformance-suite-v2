"""Integration tests for compiled catalogue execution entry points."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
from django.test import Client

from conformance import cli
from conformance.catalogue import (
    CatalogueAssertion,
    CatalogueKey,
    CatalogueRequestStep,
    CatalogueTestCase,
    EndpointRef,
    ImplementedEndpoint,
    RuntimeInputRequirement,
    SecurityProfileApplicability,
    TestCaseApplicability,
    TestCatalogue,
    TestPlanSpec,
    compile_test_plan,
)
from conformance.context import RuntimeConfig
from conformance.executor import run_compiled_test_plan
from conformance.masking import MASKED_VALUE

CATALOGUE_KEY = CatalogueKey(standard="open-banking", version="v4.0", api="ais")


def _test_catalogue() -> TestCatalogue:
    """Build a small executable catalogue fixture.

    Returns:
        Catalogue fixture with one bearer-token protected accounts request.
    """
    return TestCatalogue(
        key=CATALOGUE_KEY,
        catalogue_version="test.1",
        test_cases=(
            CatalogueTestCase(
                test_case_id="accounts-read",
                name="Read accounts",
                role="resource",
                compliance_scope=("legacy-fcs-script:test#accounts",),
                applicability=TestCaseApplicability(
                    security_profiles=SecurityProfileApplicability(profiles=("all",)),
                    endpoint_refs=(EndpointRef(method="GET", path="/open-banking/v4.0/aisp/accounts"),),
                ),
                mandatory=True,
                runtime_input_requirements=(
                    RuntimeInputRequirement("resourceBaseUrl", "url", "Resource base URL"),
                    RuntimeInputRequirement("accessToken", "string", "Access token", sensitive=True),
                ),
                request_steps=(
                    CatalogueRequestStep(
                        step_id="accounts-read-request",
                        name="GET accounts",
                        method="GET",
                        path="/open-banking/v4.0/aisp/accounts",
                        runtime_input_refs=("resourceBaseUrl", "accessToken"),
                    ),
                ),
                assertions=(
                    CatalogueAssertion("status-200", "http_status", "HTTP 200", {"expected": 200}),
                    CatalogueAssertion(
                        "data-present",
                        "json_field",
                        "Data object is present",
                        {"path": "Data", "present": True},
                    ),
                ),
            ),
        ),
    )


def _plan_spec() -> TestPlanSpec:
    """Build a plan spec selecting the accounts fixture endpoint.

    Returns:
        Plan spec with runtime values needed by :func:`_test_catalogue`.
    """
    return TestPlanSpec(
        schema_version="v1",
        catalogue_key=CATALOGUE_KEY,
        security_profile="fapi1-advanced",
        implemented_endpoints=(
            ImplementedEndpoint(
                method="GET",
                path="/open-banking/v4.0/aisp/accounts",
                resource_group="Accounts",
            ),
        ),
        runtime_inputs={
            "resourceBaseUrl": "https://rs.example.com",
            "accessToken": "secret-access-token",
        },
    )


def _plan_spec_json() -> dict[str, object]:
    """Build a JSON plan-spec payload accepted by CLI/API parsing.

    Returns:
        JSON object for the fixture plan spec.
    """
    return {
        "schemaVersion": "v1",
        "catalogue": {"standard": "open-banking", "version": "v4.0", "api": "ais"},
        "securityProfile": "fapi1-advanced",
        "implementedEndpoints": [
            {
                "method": "GET",
                "path": "/open-banking/v4.0/aisp/accounts",
                "resourceGroup": "Accounts",
            }
        ],
        "runtimeInputs": {
            "resourceBaseUrl": "https://rs.example.com",
            "accessToken": "secret-access-token",
        },
    }


def _config_json(tmp_path: Path) -> dict[str, object]:
    """Build config JSON for catalogue integration tests.

    Args:
        tmp_path: Temporary directory used for output paths.

    Returns:
        JSON object accepted by the model-bank config parser.
    """
    return {
        "environment": "catalogue-env",
        "discoveryUrl": "https://auth.example.com/.well-known/openid-configuration",
        "resultOutputPath": str(tmp_path / "result.json"),
        "executionLogPath": str(tmp_path / "execution.ndjson"),
    }


def _mock_client_factory(handler: Callable[[httpx.Request], httpx.Response]) -> Callable[..., httpx.Client]:
    """Build an ``httpx.Client`` replacement using ``MockTransport``.

    Args:
        handler: Request handler passed to ``httpx.MockTransport``.

    Returns:
        Callable compatible with the subset of ``httpx.Client`` used by the
        app's HTTP-client factory.
    """
    original_client = httpx.Client

    def mock_client(*, timeout: float, verify: bool | str, cert: tuple[str, str] | None) -> httpx.Client:
        """Return a mock-transport client.

        Args:
            timeout: Ignored timeout argument supplied by the app.
            verify: Ignored TLS verification argument supplied by the app.
            cert: Ignored client-certificate argument supplied by the app.

        Returns:
            HTTP client using the provided mock handler.
        """
        del timeout, verify, cert
        return original_client(transport=httpx.MockTransport(handler))

    return mock_client


@pytest.mark.unit
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
            environment="catalogue-env",
            client=client,
            runtime_config=RuntimeConfig(
                discovery_url="https://auth.example.com/.well-known/openid-configuration",
                environment="catalogue-env",
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
    assert "value" not in sensitive_runtime_input
    assert request_headers["Authorization"] == MASKED_VALUE
    assert step_catalogue["testCaseId"] == "accounts-read"
    assert step_catalogue["role"] == "resource"
    assert step_catalogue["complianceScope"] == ["legacy-fcs-script:test#accounts"]


@pytest.mark.unit
def test_cli_executes_plan_spec(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    plan_path = tmp_path / "plan.json"
    config_path.write_text(json.dumps(_config_json(tmp_path)), encoding="utf-8")
    plan_path.write_text(json.dumps(_plan_spec_json()), encoding="utf-8")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"Data": {}}, headers={"x-fapi-interaction-id": "interaction-1"})

    monkeypatch.setattr(cli, "resolve_catalogue", lambda _key: _test_catalogue())
    monkeypatch.setattr(httpx, "Client", _mock_client_factory(handler))

    exit_code = cli.run([str(config_path), "--plan-spec", str(plan_path)])

    result = json.loads((tmp_path / "result.json").read_text(encoding="utf-8"))
    assert exit_code == 0
    assert result["catalogue"]["api"] == "ais"
    assert result["steps"][0]["details"]["catalogue"]["testCaseId"] == "accounts-read"


@pytest.mark.integration
def test_api_create_run_accepts_plan_spec(tmp_path: Path) -> None:
    body = {"config": _config_json(tmp_path), "planSpec": _plan_spec_json()}

    with (
        patch("conformance.api.views.resolve_catalogue", return_value=_test_catalogue()),
        patch("conformance.api.views.start_run", return_value={"id": "run-1", "status": "pending"}) as start_run_mock,
    ):
        response = Client().post("/api/runs/", data=json.dumps(body), content_type="application/json")

    assert response.status_code == 201
    assert response.json()["id"] == "run-1"
    assert start_run_mock.call_args.kwargs["compiled_plan"].catalogue_key == CATALOGUE_KEY
    assert start_run_mock.call_args.kwargs["runtime_inputs"]["accessToken"] == "secret-access-token"


@pytest.mark.integration
def test_api_create_run_rejects_removed_manifest_field(tmp_path: Path) -> None:
    body = {"config": _config_json(tmp_path), "manifest": {"schemaVersion": "v1"}}

    response = Client().post("/api/runs/", data=json.dumps(body), content_type="application/json")

    assert response.status_code == 400
    assert "Unknown request field(s): manifest" in response.json()["error"]
