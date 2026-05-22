"""Execute parsed v0 manifests against JSON HTTP endpoints."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx

from conformance.assertions import AssertionResult, evaluate_assertion
from conformance.http import JsonHttpClientError, JsonHttpResponse, get_json
from conformance.json_types import JsonObject, JsonValue
from conformance.manifest import Manifest, ManifestAssertion, ManifestFollowUp, ManifestTest
from conformance.results import SmokeCheckResult, StepResult, build_smoke_check_result
from conformance.url_validation import HttpsUrlValidationError, validate_https_url


def run_manifest(manifest: Manifest, *, environment: str, client: httpx.Client) -> SmokeCheckResult:
    """Run a parsed v0 manifest and return a structured smoke-check result.

    Args:
        manifest: Parsed and validated manifest to execute.
        environment: Environment name copied into the result file.
        client: Preconfigured synchronous HTTP client used for network requests.

    Returns:
        Smoke-check result containing ordered manifest test steps.
    """
    started_at = datetime.now(UTC)
    steps: list[StepResult] = []

    for manifest_test in manifest.tests:
        test_step, response = _run_primary_test(manifest_test, client=client)
        steps.append(test_step)
        if test_step.status == "passed" and manifest_test.follow_up is not None and response is not None:
            steps.append(_run_follow_up(manifest_test, manifest_test.follow_up, response, client=client))

    return build_smoke_check_result(environment, steps, started_at=started_at)


def _run_primary_test(
    manifest_test: ManifestTest,
    *,
    client: httpx.Client,
) -> tuple[StepResult, JsonHttpResponse | None]:
    """Run the primary request for one manifest test.

    Issues an HTTP GET to the test's configured URL and evaluates the
    configured assertions against the response.

    Args:
        manifest_test: Parsed manifest test containing the URL and assertions.
        client: Preconfigured synchronous HTTP client.

    Returns:
        A tuple of the step result and the parsed HTTP response (or ``None``
        if the request failed before a response was received).
    """
    try:
        response = get_json(client, manifest_test.request.url)
    except JsonHttpClientError as error:
        return (
            StepResult(
                name=manifest_test.id,
                status="failed",
                message=str(error),
                url=manifest_test.request.url,
            ),
            None,
        )

    step = _build_assertion_step(
        name=manifest_test.id,
        success_message=f"{manifest_test.name} passed",
        failure_message=f"{manifest_test.name} failed",
        response=response,
        assertions=manifest_test.assertions,
    )
    return step, response


def _run_follow_up(
    manifest_test: ManifestTest,
    follow_up: ManifestFollowUp,
    response: JsonHttpResponse,
    *,
    client: httpx.Client,
) -> StepResult:
    """Run a manifest follow-up request derived from a primary response.

    Resolves a follow-up URL from the primary response body, validates it as
    HTTPS, issues an HTTP GET, and evaluates the follow-up assertions.

    Args:
        manifest_test: Parent manifest test (used for naming the step).
        follow_up: Follow-up specification with URL source and assertions.
        response: Parsed response from the primary request.
        client: Preconfigured synchronous HTTP client.

    Returns:
        Step result indicating pass/fail for the follow-up assertions.
    """
    follow_up_name = f"{manifest_test.id}.followUp"
    follow_up_url = _follow_up_url(response.body, follow_up)
    if follow_up_url is None:
        return StepResult(
            name=follow_up_name,
            status="failed",
            message=f"Unable to resolve follow-up URL from {follow_up.url_source}",
            url=response.url,
        )
    try:
        validate_https_url(follow_up_url, label=f"Follow-up URL from {follow_up.url_source}")
    except HttpsUrlValidationError as error:
        return StepResult(
            name=follow_up_name,
            status="failed",
            message=str(error),
            url=follow_up_url,
        )

    try:
        follow_up_response = get_json(client, follow_up_url)
    except JsonHttpClientError as error:
        return StepResult(
            name=follow_up_name,
            status="failed",
            message=str(error),
            url=follow_up_url,
        )

    return _build_assertion_step(
        name=follow_up_name,
        success_message=f"{manifest_test.name} follow-up passed",
        failure_message=f"{manifest_test.name} follow-up failed",
        response=follow_up_response,
        assertions=follow_up.assertions,
    )


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
    """Convert an assertion result to the step details JSON shape."""
    return {
        "status": "passed" if assertion_result.passed else "failed",
        "message": assertion_result.message,
    }


def _follow_up_url(body: JsonObject, follow_up: ManifestFollowUp) -> str | None:
    """Resolve the v0 follow-up URL source from the primary response body."""
    if follow_up.url_source != "response.body.jwks_uri":
        return None
    value: JsonValue | None = body.get("jwks_uri")
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()
