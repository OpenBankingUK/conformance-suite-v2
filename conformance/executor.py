"""Execute parsed v0/v1 manifests against JSON HTTP endpoints."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx

from conformance.assertions import AssertionResult, evaluate_assertion
from conformance.context import (
    ExecutionContext,
    PlaceholderResolutionError,
    RequestRecord,
    ResponseRecord,
    record_step,
    resolve_placeholders,
)
from conformance.http import JsonHttpClientError, JsonHttpResponse, get_json
from conformance.json_types import JsonObject
from conformance.manifest import (
    Manifest,
    ManifestAssertion,
    ManifestStep,
    ManifestTest,
)
from conformance.results import SmokeCheckResult, StepResult, build_smoke_check_result
from conformance.url_validation import HttpsUrlValidationError, validate_https_url


def run_manifest(manifest: Manifest, *, environment: str, client: httpx.Client) -> SmokeCheckResult:
    """Run a parsed manifest and return a structured smoke-check result.

    Dispatches to the v0 or v1 execution path based on schema version.
    v0 manifests are internally desugared to v1 sequential steps.

    Args:
        manifest: Parsed and validated manifest to execute.
        environment: Environment name copied into the result file.
        client: Preconfigured synchronous HTTP client used for network requests.

    Returns:
        Smoke-check result containing ordered manifest test steps.
    """
    if manifest.schema_version == "v1":
        return _run_manifest_v1(manifest, environment=environment, client=client)
    return _run_manifest_v0(manifest, environment=environment, client=client)


def _run_manifest_v1(manifest: Manifest, *, environment: str, client: httpx.Client) -> SmokeCheckResult:
    """Execute a v1 manifest with sequential steps and context carry-forward.

    Each step resolves ``${...}`` placeholders from earlier step responses,
    validates the resolved URL, fetches the endpoint, evaluates assertions,
    and records the result into the execution context for later steps.

    Args:
        manifest: Parsed v1 manifest containing sequential steps.
        environment: Environment name copied into the result file.
        client: Preconfigured synchronous HTTP client.

    Returns:
        Smoke-check result with one entry per step.
    """
    started_at = datetime.now(UTC)
    steps: list[StepResult] = []
    context = ExecutionContext()

    for manifest_step in manifest.steps:
        step_result, context = _execute_v1_step(manifest_step, context=context, client=client)
        steps.append(step_result)

    return build_smoke_check_result(environment, steps, started_at=started_at)


def _execute_v1_step(
    manifest_step: ManifestStep,
    *,
    context: ExecutionContext,
    client: httpx.Client,
) -> tuple[StepResult, ExecutionContext]:
    """Execute a single v1 manifest step with placeholder resolution.

    Resolves placeholders in the request URL, validates the resolved URL,
    issues the HTTP request, evaluates assertions, and records the step
    into the execution context.

    Args:
        manifest_step: The v1 step to execute.
        context: Current execution context with earlier step records.
        client: Preconfigured synchronous HTTP client.

    Returns:
        A tuple of the step result and the updated execution context.
    """
    # Resolve placeholders in the URL
    try:
        resolved_url = resolve_placeholders(manifest_step.request.url, context)
    except PlaceholderResolutionError as error:
        # Record request (with unresolved URL) but no response
        request_record = RequestRecord(method=manifest_step.request.method, url=manifest_step.request.url)
        new_context = record_step(context, manifest_step.id, request_record, None)
        return (
            StepResult(
                name=manifest_step.id,
                status="failed",
                message=f"Placeholder resolution failed: {error}",
                url=manifest_step.request.url,
            ),
            new_context,
        )

    # Validate method
    if manifest_step.request.method != "GET":
        request_record = RequestRecord(method=manifest_step.request.method, url=resolved_url)
        new_context = record_step(context, manifest_step.id, request_record, None)
        return (
            StepResult(
                name=manifest_step.id,
                status="failed",
                message=f"Unsupported request method: {manifest_step.request.method}",
                url=resolved_url,
            ),
            new_context,
        )

    # Validate resolved URL is HTTPS
    try:
        validate_https_url(resolved_url, label=f"Step '{manifest_step.id}' request URL")
    except HttpsUrlValidationError as error:
        request_record = RequestRecord(method="GET", url=resolved_url)
        new_context = record_step(context, manifest_step.id, request_record, None)
        return (
            StepResult(
                name=manifest_step.id,
                status="failed",
                message=str(error),
                url=resolved_url,
            ),
            new_context,
        )

    # Execute HTTP request
    request_record = RequestRecord(method="GET", url=resolved_url)
    try:
        response = get_json(client, resolved_url)
    except JsonHttpClientError as error:
        new_context = record_step(context, manifest_step.id, request_record, None)
        return (
            StepResult(
                name=manifest_step.id,
                status="failed",
                message=str(error),
                url=resolved_url,
            ),
            new_context,
        )

    # Record response into context
    response_record = ResponseRecord(
        status_code=response.status_code,
        body=response.body,
    )
    new_context = record_step(context, manifest_step.id, request_record, response_record)

    # Evaluate assertions
    step_result = _build_assertion_step(
        name=manifest_step.id,
        success_message=f"{manifest_step.name} passed",
        failure_message=f"{manifest_step.name} failed",
        response=response,
        assertions=manifest_step.assertions,
    )
    return step_result, new_context


def _run_manifest_v0(manifest: Manifest, *, environment: str, client: httpx.Client) -> SmokeCheckResult:
    """Execute a v0 manifest preserving original skip-on-fail semantics.

    In v0, follow-up steps are only executed when the primary step passes.
    This differs from v1 where all steps run regardless of earlier assertion
    outcomes. The method desugars each test into v1 steps but gates follow-up
    execution on primary step success.

    Args:
        manifest: Parsed v0 manifest containing tests with optional followUp.
        environment: Environment name copied into the result file.
        client: Preconfigured synchronous HTTP client.

    Returns:
        Smoke-check result with step entries matching v0 naming conventions.
    """
    from conformance.manifest import ManifestRequest

    started_at = datetime.now(UTC)
    steps: list[StepResult] = []
    context = ExecutionContext()

    for test in manifest.tests:
        # Primary step
        primary_step = ManifestStep(
            id=test.id,
            name=test.name,
            request=test.request,
            assertions=test.assertions,
        )
        step_result, context = _execute_v1_step(primary_step, context=context, client=client)
        steps.append(step_result)

        # Follow-up: only execute if primary passed (v0 semantics)
        if step_result.status == "passed" and test.follow_up is not None:
            follow_up_url = _desugar_follow_up_url(test)
            follow_up_step = ManifestStep(
                id=f"{test.id}.followUp",
                name=f"{test.name} follow-up",
                request=ManifestRequest(method=test.follow_up.request.method, url=follow_up_url),
                assertions=test.follow_up.assertions,
            )
            follow_up_result, context = _execute_v1_step(follow_up_step, context=context, client=client)
            steps.append(follow_up_result)

    return build_smoke_check_result(environment, steps, started_at=started_at)


def _desugar_follow_up_url(test: ManifestTest) -> str:
    """Build the placeholder URL for a desugared v0 follow-up step.

    Maps the v0 ``urlSource`` value to an equivalent v1 ``${...}`` placeholder
    expression referencing the primary step's response body.

    Args:
        test: The v0 manifest test containing the follow-up to desugar.

    Returns:
        A ``${...}`` placeholder string for the follow-up URL.
    """
    assert test.follow_up is not None  # noqa: S101 — caller guarantees follow_up exists
    # Map "response.body.jwks_uri" → "${steps.<id>.response.body.jwks_uri}"
    return f"${{steps.{test.id}.{test.follow_up.url_source}}}"


def _build_assertion_step(
    *,
    name: str,
    success_message: str,
    failure_message: str,
    response: JsonHttpResponse,
    assertions: tuple[ManifestAssertion, ...],
) -> StepResult:
    """Build a step result by evaluating all assertions for a response.

    Args:
        name: The step name displayed in the conformance report.
        success_message: Message emitted when all assertions pass.
        failure_message: Message emitted when any assertion fails.
        response: The HTTP response to evaluate assertions against.
        assertions: The manifest assertions to apply to the response.

    Returns:
        A completed step result containing the overall pass/fail status
        and per-assertion details.
    """
    assertion_results = tuple(
        evaluate_assertion(assertion, status_code=response.status_code, body=response.body) for assertion in assertions
    )
    passed = all(assertion_result.passed for assertion_result in assertion_results)
    return StepResult(
        name=name,
        status="passed" if passed else "failed",
        message=success_message if passed else failure_message,
        url=response.url,
        status_code=response.status_code,
        details={"assertions": [_assertion_result_to_json(assertion_result) for assertion_result in assertion_results]},
    )


def _assertion_result_to_json(assertion_result: AssertionResult) -> JsonObject:
    """Convert an assertion result to the step details JSON shape.

    Args:
        assertion_result: Evaluated assertion outcome to serialise.

    Returns:
        JSON-serialisable dictionary with ``status`` and ``message`` keys.
    """
    return {
        "status": "passed" if assertion_result.passed else "failed",
        "message": assertion_result.message,
    }
