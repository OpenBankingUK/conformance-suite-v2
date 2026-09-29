"""Builders for the small executable AIS catalogue used by plan-execution tests.

The same fixture catalogue, plan payloads, and mock HTTP client factory back
both the in-process compiled-plan/CLI unit tests and the REST endpoint
component tests, so they are defined once here.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import httpx

from conformance.catalogue import (
    CatalogueAssertion,
    CatalogueKey,
    CatalogueRequestStep,
    CatalogueTestCase,
    EndpointCapability,
    EndpointRef,
    ImplementedEndpoint,
    RuntimeInputRequirement,
    SecurityProfileApplicability,
    TestCaseApplicability,
    TestCatalogue,
    TestPlanSpec,
)

CATALOGUE_KEY = CatalogueKey(standard="open-banking", version="v4.0", api="ais")
"""Catalogue key shared by the fixture catalogue and every fixture plan payload."""

ACCOUNTS_ENDPOINT = EndpointRef(method="GET", path="/open-banking/v4.0/aisp/accounts")
"""The single implemented endpoint the fixture catalogue exercises."""


def build_test_catalogue() -> TestCatalogue:
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
                    endpoint_refs=(ACCOUNTS_ENDPOINT,),
                    required_capability_ids=("accounts.read",),
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
            CatalogueTestCase(
                test_case_id="accounts-balances",
                name="Read account balances",
                role="resource",
                compliance_scope=("legacy-fcs-script:test#balances",),
                applicability=TestCaseApplicability(
                    security_profiles=SecurityProfileApplicability(profiles=("all",)),
                    endpoint_refs=(ACCOUNTS_ENDPOINT,),
                    required_capability_ids=("accounts.balances",),
                ),
                mandatory=True,
                runtime_input_requirements=(
                    RuntimeInputRequirement("resourceBaseUrl", "url", "Resource base URL"),
                    RuntimeInputRequirement("accessToken", "string", "Access token", sensitive=True),
                ),
                request_steps=(
                    CatalogueRequestStep(
                        step_id="accounts-balances-request",
                        name="GET account balances",
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
        capabilities=(
            EndpointCapability(
                capability_id="accounts.read",
                label="Read accounts",
                description="Baseline account-list endpoint support.",
                required=True,
                endpoint_refs=(ACCOUNTS_ENDPOINT,),
            ),
            EndpointCapability(
                capability_id="accounts.balances",
                label="Read account balances",
                description="Optional balance data support for the account endpoint.",
                required=False,
                endpoint_refs=(ACCOUNTS_ENDPOINT,),
            ),
        ),
    )


def build_plan_spec(*, capabilities: tuple[str, ...] = ()) -> TestPlanSpec:
    """Build a plan spec selecting the accounts fixture endpoint.

    Args:
        capabilities: Endpoint-scoped optional capabilities to declare.

    Returns:
        Plan spec with runtime values needed by :func:`build_test_catalogue`.
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
                capability_ids=capabilities,
            ),
        ),
        runtime_inputs={
            "resourceBaseUrl": "https://rs.example.com",
            "accessToken": "secret-access-token",
        },
    )


def build_plan_spec_json(*, capabilities: tuple[str, ...] = ()) -> dict[str, object]:
    """Build a JSON plan-spec payload accepted by CLI/API parsing.

    Args:
        capabilities: Endpoint-scoped optional capabilities to declare.

    Returns:
        JSON object for the fixture plan spec.
    """
    endpoint: dict[str, object] = {
        "method": "GET",
        "path": "/open-banking/v4.0/aisp/accounts",
        "resourceGroup": "Accounts",
    }
    if capabilities:
        endpoint["capabilities"] = list(capabilities)
    return {
        "schemaVersion": "v1",
        "catalogue": {"standard": "open-banking", "version": "v4.0", "api": "ais"},
        "securityProfile": "fapi1-advanced",
        "implementedEndpoints": [endpoint],
        "runtimeInputs": {
            "resourceBaseUrl": "https://rs.example.com",
            "accessToken": "secret-access-token",
        },
    }


def build_canonical_plan_json(*, capabilities: tuple[str, ...] = ()) -> dict[str, object]:
    """Build a canonical JSON-first test plan selecting the accounts fixture endpoint.

    Args:
        capabilities: Endpoint-scoped optional capabilities to declare.

    Returns:
        Canonical schemaVersion ``1.0`` test plan.
    """
    endpoint: dict[str, object] = {
        "method": "GET",
        "path": "/open-banking/v4.0/aisp/accounts",
    }
    if capabilities:
        endpoint["capabilities"] = list(capabilities)
    return {
        "schemaVersion": "1.0",
        "specification": {"family": "OBL_READ_WRITE", "version": "4.0.1", "profile": "FAPI1_ADVANCED"},
        "securityEnvironment": {
            "discoveryUrl": "https://auth.example.com/.well-known/openid-configuration",
            "resourceBaseUrl": "https://rs.example.com",
        },
        "resourceGroups": [{"id": "AIS", "label": "Accounts", "endpoints": [endpoint]}],
        "businessTestData": {"inputs": {"accessToken": {"value": "secret-access-token"}}},
        "metadata": {},
    }


def build_plan_document_v2_json(*, capabilities: tuple[str, ...] = ()) -> dict[str, object]:
    """Build a v2 shared plan document for CLI/API parsing.

    Args:
        capabilities: Endpoint-scoped optional capabilities to declare.

    Returns:
        JSON object using the shared v2 plan-document shape.
    """
    endpoint: dict[str, object] = {
        "method": "GET",
        "path": "/open-banking/v4.0/aisp/accounts",
    }
    if capabilities:
        endpoint["capabilities"] = list(capabilities)
    return {
        "schemaVersion": "v2",
        "scheme": "open-banking-uk",
        "specification": "read-write",
        "version": "4.0.1",
        "securityProfile": "fapi1-advanced",
        "scope": {
            "resourceGroups": [
                {
                    "id": "ais.accounts",
                    "label": "Accounts",
                    "endpoints": [endpoint],
                }
            ]
        },
        "config": {
            "resourceBaseUrl": "https://rs.example.com",
            "inputs": {"accessToken": {"value": "secret-access-token"}},
        },
    }


def build_config_json(tmp_path: Path) -> dict[str, object]:
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


def mock_client_factory(handler: Callable[[httpx.Request], httpx.Response]) -> Callable[..., httpx.Client]:
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
