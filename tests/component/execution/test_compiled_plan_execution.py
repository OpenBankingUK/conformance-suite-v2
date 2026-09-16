"""Component tests for complete compiled catalogue plan execution."""

import uuid
from pathlib import Path
from typing import Any, cast

import httpx
import pytest

from conformance.execution_log import BufferedExecutionLogger
from conformance.json_types import JsonObject

pytestmark = pytest.mark.component


def test_run_compiled_test_plan_attaches_catalogue_traceability(tmp_path: Path) -> None:
    """Compiled catalogue execution embeds traceability and omits suite metadata.

    Args:
        tmp_path: Pytest temporary directory used as the runtime-input base.
    """
    from conformance.catalogue import (
        CatalogueAssertion,
        CatalogueKey,
        CatalogueRequestStep,
        CatalogueTestCase,
        ImplementedEndpoint,
        RuntimeInputRequirement,
        SecurityProfileApplicability,
        TestCaseApplicability,
        TestCatalogue,
        TestPlanSpec,
        compile_test_plan,
    )
    from conformance.executor import run_compiled_test_plan

    catalogue_key = CatalogueKey(standard="open-banking", version="v4.0", api="ais")
    catalogue = TestCatalogue(
        key=catalogue_key,
        catalogue_version="2026.7.0",
        test_cases=(
            CatalogueTestCase(
                test_case_id="ais-accounts-list",
                name="Accounts list",
                role="resource",
                compliance_scope=("OBRW v4.0 AIS accounts",),
                applicability=TestCaseApplicability(
                    security_profiles=SecurityProfileApplicability(profiles=("all",)),
                ),
                mandatory=True,
                runtime_input_requirements=(
                    RuntimeInputRequirement(
                        input_id="resourceBaseUrl",
                        input_type="url",
                        label="Resource base URL",
                    ),
                ),
                request_steps=(
                    CatalogueRequestStep(
                        step_id="ais-accounts-list-request",
                        name="List accounts",
                        method="GET",
                        path="/open-banking/v4.0/aisp/accounts",
                        runtime_input_refs=("resourceBaseUrl",),
                    ),
                ),
                assertions=(
                    CatalogueAssertion(
                        assertion_id="status-200",
                        kind="http_status",
                        description="Accounts endpoint returns HTTP 200",
                        rule={"expected": 200},
                    ),
                ),
            ),
        ),
    )
    spec = TestPlanSpec(
        schema_version="v1",
        catalogue_key=catalogue_key,
        security_profile="fapi1-advanced",
        implemented_endpoints=(
            ImplementedEndpoint(
                method="GET",
                path="/open-banking/v4.0/aisp/accounts",
                resource_group="Accounts",
            ),
        ),
        runtime_inputs={"resourceBaseUrl": "https://resource.example.com"},
    )
    compiled_plan = compile_test_plan(catalogue, spec)

    def mock_handler(request: httpx.Request) -> httpx.Response:
        """Return the mocked accounts response for compiled-plan execution.

        Args:
            request: Outbound HTTP request emitted by the executor.

        Returns:
            Mocked successful accounts response.
        """
        assert str(request.url) == "https://resource.example.com/open-banking/v4.0/aisp/accounts"
        return httpx.Response(200, json={"Data": {"Account": []}})

    execution_logger = BufferedExecutionLogger(run_id="compiled-plan-run", developer_mode=False)
    with httpx.Client(transport=httpx.MockTransport(mock_handler)) as client:
        result = run_compiled_test_plan(
            compiled_plan,
            runtime_inputs=spec.runtime_inputs,
            runtime_input_base_dir=tmp_path,
            client=client,
            execution_logger=execution_logger,
        )

    assert result.status == "passed"
    result_json = result.to_json_object()
    assert "suite" not in result_json
    catalogue_evidence = cast(JsonObject, result_json["catalogue"])
    assert catalogue_evidence["generatedTestCaseIds"] == ["ais-accounts-list"]
    details = cast("dict[str, Any]", result.steps[0].details)
    assert details["catalogue"] == {
        "testCaseId": "ais-accounts-list",
        "requestStepId": "ais-accounts-list-request",
        "role": "resource",
        "complianceScope": ["OBRW v4.0 AIS accounts"],
    }
    run_started = execution_logger.events()[0]
    assert run_started.payload["catalogue"] == {
        "standard": "open-banking",
        "version": "v4.0",
        "api": "ais",
        "catalogueVersion": "2026.7.0",
    }


def test_run_compiled_test_plan_sends_catalogue_request_headers(tmp_path: Path) -> None:
    """Compiled catalogue execution sends selected-run request metadata headers.

    Args:
        tmp_path: Pytest temporary directory used as the runtime-input base.
    """
    from conformance.catalogue import (
        CatalogueAssertion,
        CatalogueKey,
        CatalogueRequestHeader,
        CatalogueRequestStep,
        CatalogueTestCase,
        ImplementedEndpoint,
        RuntimeInputRequirement,
        SecurityProfileApplicability,
        TestCaseApplicability,
        TestCatalogue,
        TestPlanSpec,
        compile_test_plan,
    )
    from conformance.executor import run_compiled_test_plan

    catalogue_key = CatalogueKey(standard="open-banking", version="v4.0", api="pis")
    catalogue = TestCatalogue(
        key=catalogue_key,
        catalogue_version="2026.7.0",
        test_cases=(
            CatalogueTestCase(
                test_case_id="pis-submit",
                name="Submit payment",
                role="resource",
                compliance_scope=("OBRW v4.0 PIS submit",),
                applicability=TestCaseApplicability(
                    security_profiles=SecurityProfileApplicability(profiles=("all",)),
                ),
                mandatory=True,
                runtime_input_requirements=(RuntimeInputRequirement("resourceBaseUrl", "url", "Resource base URL"),),
                request_steps=(
                    CatalogueRequestStep(
                        step_id="pis-submit-request",
                        name="Submit payment",
                        method="POST",
                        path="/open-banking/v4.0/pisp/domestic-payments",
                        runtime_input_refs=("resourceBaseUrl",),
                        headers=(
                            CatalogueRequestHeader("x-fapi-interaction-id", generated_value="uuid4"),
                            CatalogueRequestHeader("x-idempotency-key", generated_value="uuid4"),
                        ),
                    ),
                ),
                assertions=(
                    CatalogueAssertion("status-201", "http_status", "Payment is accepted", {"expected": 201}),
                    CatalogueAssertion(
                        "interaction-playback",
                        "header",
                        "Response replays x-fapi-interaction-id",
                        {"name": "x-fapi-interaction-id", "rule": "playback"},
                    ),
                ),
            ),
        ),
    )
    spec = TestPlanSpec(
        schema_version="v1",
        catalogue_key=catalogue_key,
        security_profile="fapi1-advanced",
        implemented_endpoints=(
            ImplementedEndpoint(
                method="POST",
                path="/open-banking/v4.0/pisp/domestic-payments",
                resource_group="Payments",
            ),
        ),
        runtime_inputs={"resourceBaseUrl": "https://resource.example.com"},
    )
    compiled_plan = compile_test_plan(catalogue, spec)
    observed_headers: dict[str, str] = {}

    def mock_handler(request: httpx.Request) -> httpx.Response:
        """Capture the outbound headers and return a successful response.

        Args:
            request: Outbound HTTP request emitted by the executor.

        Returns:
            Mocked successful payment response.
        """
        observed_headers.update(dict(request.headers))
        return httpx.Response(
            201,
            headers={"x-fapi-interaction-id": request.headers["x-fapi-interaction-id"]},
            json={"Data": {"PaymentId": "payment-123"}},
        )

    execution_logger = BufferedExecutionLogger(run_id="compiled-plan-run", developer_mode=False)
    with httpx.Client(transport=httpx.MockTransport(mock_handler)) as client:
        result = run_compiled_test_plan(
            compiled_plan,
            runtime_inputs=spec.runtime_inputs,
            runtime_input_base_dir=tmp_path,
            client=client,
            execution_logger=execution_logger,
        )

        assert result.status == "passed"
        uuid.UUID(observed_headers["x-fapi-interaction-id"])
        uuid.UUID(observed_headers["x-idempotency-key"])
        request_events = [event for event in execution_logger.events() if event.type == "request-sent"]
        assert len(request_events) == 1
        event_headers = request_events[0].payload["headers"]
        assert isinstance(event_headers, dict)
        assert event_headers["x-fapi-interaction-id"] == observed_headers["x-fapi-interaction-id"]
        assert event_headers["x-idempotency-key"] == observed_headers["x-idempotency-key"]
