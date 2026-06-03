import json
import secrets
from collections.abc import Callable
from typing import Any, cast

import httpx
import pytest

from conformance.api.auth_session_store import AuthSessionStore
from conformance.context import ExecutionContext, RequestRecord, ResponseRecord, record_step
from conformance.execution_log import BufferedExecutionLogger
from conformance.executor import _execute_v1_psu_step, run_manifest
from conformance.json_types import JsonValue
from conformance.manifest import PsuAuthorizationStep, parse_manifest


def manifest_config() -> dict[str, JsonValue]:
    return {
        "schemaVersion": "v0",
        "name": "Ozone OpenID discovery and JWKS smoke check",
        "tests": [
            {
                "id": "openid-discovery",
                "name": "OpenID discovery document",
                "request": {
                    "method": "GET",
                    "url": "https://modelbank.example.com/.well-known/openid-configuration",
                },
                "assertions": [
                    {"type": "http_status", "expected": 200},
                    {"type": "json_field", "path": "issuer", "rule": "https_url"},
                    {"type": "json_field", "path": "jwks_uri", "rule": "https_url"},
                ],
                "followUp": {
                    "type": "jwks",
                    "urlSource": "response.body.jwks_uri",
                    "request": {"method": "GET"},
                    "assertions": [
                        {"type": "http_status", "expected": 200},
                        {"type": "json_field", "path": "keys", "rule": "array"},
                    ],
                },
            }
        ],
    }


def first_test(raw_manifest: dict[str, JsonValue]) -> dict[str, JsonValue]:
    tests = cast("list[JsonValue]", raw_manifest["tests"])
    return cast("dict[str, JsonValue]", tests[0])


@pytest.mark.unit
def test_run_manifest_fetches_primary_request_and_follow_up() -> None:
    requested_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_urls.append(str(request.url))
        if str(request.url) == "https://modelbank.example.com/.well-known/openid-configuration":
            return httpx.Response(
                200,
                json={
                    "issuer": "https://modelbank.example.com",
                    "jwks_uri": "https://modelbank.example.com/jwks",
                },
            )
        return httpx.Response(200, json={"keys": [{"kid": "signing-key"}]})

    manifest = parse_manifest(manifest_config())
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = run_manifest(manifest, environment="ozone-model-bank", client=client)

    assert result.status == "passed"
    assert requested_urls == [
        "https://modelbank.example.com/.well-known/openid-configuration",
        "https://modelbank.example.com/jwks",
    ]
    assert [step.name for step in result.steps] == ["openid-discovery", "openid-discovery.followUp"]
    assert result.to_json_object()["summary"] == {"total": 2, "passed": 2, "failed": 0, "warn": 0, "skipped": 0}


@pytest.mark.unit
def test_run_manifest_skips_follow_up_when_primary_assertion_fails() -> None:
    requested_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_urls.append(str(request.url))
        return httpx.Response(
            201,
            json={
                "issuer": "https://modelbank.example.com",
                "jwks_uri": "https://modelbank.example.com/jwks",
            },
        )

    manifest = parse_manifest(manifest_config())
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = run_manifest(manifest, environment="ozone-model-bank", client=client)

    assert result.status == "failed"
    assert requested_urls == ["https://modelbank.example.com/.well-known/openid-configuration"]
    assert len(result.steps) == 1
    assert result.steps[0].status == "failed"


@pytest.mark.unit
def test_run_manifest_reports_missing_follow_up_url() -> None:
    raw_manifest = manifest_config()
    first_test(raw_manifest)["assertions"] = [{"type": "http_status", "expected": 200}]
    manifest = parse_manifest(raw_manifest)

    with httpx.Client(
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(200, json={"issuer": "https://modelbank.example.com"})
        )
    ) as client:
        result = run_manifest(manifest, environment="ozone-model-bank", client=client)

    assert result.status == "failed"
    assert [step.status for step in result.steps] == ["passed", "failed"]
    assert result.steps[1].name == "openid-discovery.followUp"
    assert result.steps[1].message == "Unable to resolve follow-up URL from response.body.jwks_uri"


@pytest.mark.unit
def test_run_manifest_rejects_unsafe_follow_up_url_before_fetching() -> None:
    raw_manifest = manifest_config()
    first_test(raw_manifest)["assertions"] = [{"type": "http_status", "expected": 200}]
    requested_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_urls.append(str(request.url))
        return httpx.Response(
            200,
            json={
                "issuer": "https://modelbank.example.com",
                "jwks_uri": "http://modelbank.example.com/jwks",
            },
        )

    manifest = parse_manifest(raw_manifest)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = run_manifest(manifest, environment="ozone-model-bank", client=client)

    assert result.status == "failed"
    assert requested_urls == ["https://modelbank.example.com/.well-known/openid-configuration"]
    assert [step.status for step in result.steps] == ["passed", "failed"]
    assert "must be an HTTPS URL" in result.steps[1].message


@pytest.mark.unit
def test_run_manifest_evaluates_expected_http_error_status() -> None:
    raw_manifest = manifest_config()
    first_test(raw_manifest).pop("followUp")
    first_test(raw_manifest)["assertions"] = [{"type": "http_status", "expected": 404}]
    manifest = parse_manifest(raw_manifest)

    with httpx.Client(
        transport=httpx.MockTransport(lambda _request: httpx.Response(404, json={"error": "missing"}))
    ) as client:
        result = run_manifest(manifest, environment="ozone-model-bank", client=client)

    assert result.status == "passed"
    assert result.steps[0].status == "passed"
    assert result.steps[0].status_code == 404
    assert result.steps[0].details == {"assertions": [{"status": "passed", "message": "HTTP status was 404"}]}


@pytest.mark.unit
def test_run_manifest_reports_unexpected_http_error_status_as_assertion_failure() -> None:
    raw_manifest = manifest_config()
    first_test(raw_manifest).pop("followUp")
    first_test(raw_manifest)["assertions"] = [{"type": "http_status", "expected": 200}]
    manifest = parse_manifest(raw_manifest)

    with httpx.Client(
        transport=httpx.MockTransport(lambda _request: httpx.Response(404, json={"error": "missing"}))
    ) as client:
        result = run_manifest(manifest, environment="ozone-model-bank", client=client)

    assert result.status == "failed"
    assert result.steps[0].status == "failed"
    assert result.steps[0].status_code == 404
    assert result.steps[0].details == {
        "assertions": [{"status": "failed", "message": "Expected HTTP status 200, got 404"}],
        "request": {
            "method": "GET",
            "url": "https://modelbank.example.com/.well-known/openid-configuration",
        },
        "response": {
            "statusCode": 404,
            "body": {"error": "missing"},
        },
    }


@pytest.mark.unit
def test_run_manifest_reports_request_error() -> None:
    manifest = parse_manifest(manifest_config())

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = run_manifest(manifest, environment="ozone-model-bank", client=client)

    assert result.status == "failed"
    assert len(result.steps) == 1
    assert result.steps[0].name == "openid-discovery"
    assert result.steps[0].status == "failed"
    assert result.steps[0].message.startswith("Request failed for https://modelbank.example.com")


@pytest.mark.unit
def test_run_manifest_reports_invalid_json_response() -> None:
    manifest = parse_manifest(manifest_config())
    with httpx.Client(transport=httpx.MockTransport(lambda _request: httpx.Response(200, text="not-json"))) as client:
        result = run_manifest(manifest, environment="ozone-model-bank", client=client)

    assert result.status == "failed"
    assert result.steps[0].message == (
        "Response from https://modelbank.example.com/.well-known/openid-configuration was not valid JSON"
    )


@pytest.mark.unit
def test_run_manifest_preserves_status_code_when_body_is_not_json() -> None:
    """DL-0011: a 4xx with a non-JSON body must surface the HTTP status on the StepResult."""
    manifest = parse_manifest(manifest_config())
    with httpx.Client(
        transport=httpx.MockTransport(lambda _request: httpx.Response(404, text="<html>Not Found</html>"))
    ) as client:
        result = run_manifest(manifest, environment="ozone-model-bank", client=client)

    assert result.status == "failed"
    assert len(result.steps) == 1
    step = result.steps[0]
    assert step.status == "failed"
    assert step.status_code == 404
    assert "was not valid JSON" in step.message


@pytest.mark.unit
def test_run_manifest_rejects_unsafe_primary_url() -> None:
    raw_manifest = manifest_config()
    first_test(raw_manifest)["request"] = {"method": "GET", "url": "http://modelbank.example.com/unsafe"}
    first_test(raw_manifest).pop("followUp")
    first_test(raw_manifest)["assertions"] = [{"type": "http_status", "expected": 200}]
    # Bypass the parser's URL validation to simulate programmatic construction.
    from conformance.manifest import HttpStatusAssertion, Manifest, ManifestRequest, ManifestTest

    unsafe_manifest = Manifest(
        schema_version="v0",
        name="unsafe",
        tests=(
            ManifestTest(
                id="unsafe-test",
                name="Unsafe test",
                request=ManifestRequest(method="GET", url="http://modelbank.example.com/unsafe"),
                assertions=(HttpStatusAssertion(type="http_status", expected=200),),
            ),
        ),
    )
    requested_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_urls.append(str(request.url))
        return httpx.Response(200, json={})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = run_manifest(unsafe_manifest, environment="test", client=client)

    assert result.status == "failed"
    assert requested_urls == []
    assert result.steps[0].status == "failed"
    assert "must be an HTTPS URL" in result.steps[0].message


@pytest.mark.unit
def test_run_manifest_rejects_unsupported_primary_method() -> None:
    """Defence-in-depth: fail the step if request method is outside the supported set."""
    from conformance.manifest import HttpStatusAssertion, Manifest, ManifestRequest, ManifestStep

    bad_manifest = Manifest(
        schema_version="v1",
        name="bad-method",
        steps=(
            ManifestStep(
                id="options-test",
                name="OPTIONS test",
                request=ManifestRequest(method=cast(Any, "OPTIONS"), url="https://example.com/api"),
                assertions=(HttpStatusAssertion(type="http_status", expected=200),),
            ),
        ),
    )
    requested_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_urls.append(str(request.url))
        return httpx.Response(200, json={})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = run_manifest(bad_manifest, environment="test", client=client)

    assert result.status == "failed"
    assert requested_urls == []
    assert result.steps[0].status == "failed"
    assert result.steps[0].message == "Unsupported request method: OPTIONS"


@pytest.mark.unit
def test_run_manifest_v0_rejects_non_get_primary_method() -> None:
    """v0 primary requests must be GET; non-GET methods are rejected before any HTTP call.

    The JSON parser already enforces GET-only at parse time, but ``ManifestRequest.method``
    is typed as the broader v1 ``RequestMethod`` union. A programmatically constructed v0
    manifest could therefore bypass the GET-only contract via the shared v1 executor.
    This guard closes that hole.
    """
    from conformance.manifest import HttpStatusAssertion, Manifest, ManifestRequest, ManifestTest

    bad_manifest = Manifest(
        schema_version="v0",
        name="v0-non-get",
        tests=(
            ManifestTest(
                id="post-test",
                name="POST test",
                request=ManifestRequest(method="POST", url="https://example.com/api"),
                assertions=(HttpStatusAssertion(type="http_status", expected=200),),
            ),
        ),
    )
    requested_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_urls.append(str(request.url))
        return httpx.Response(200, json={})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = run_manifest(bad_manifest, environment="test", client=client)

    assert result.status == "failed"
    assert requested_urls == []
    assert result.steps[0].status == "failed"
    assert result.steps[0].message == "v0 manifest primary requests must use GET, got: POST"


@pytest.mark.unit
def test_run_manifest_rejects_unsupported_follow_up_method() -> None:
    """Defence-in-depth: fail the follow-up step if its method is outside the supported set."""
    from conformance.manifest import (
        FollowUpRequest,
        HttpStatusAssertion,
        Manifest,
        ManifestFollowUp,
        ManifestRequest,
        ManifestTest,
    )

    bad_manifest = Manifest(
        schema_version="v0",
        name="bad-follow-up-method",
        tests=(
            ManifestTest(
                id="oidc",
                name="OIDC discovery",
                request=ManifestRequest(method="GET", url="https://example.com/.well-known/openid-configuration"),
                assertions=(HttpStatusAssertion(type="http_status", expected=200),),
                follow_up=ManifestFollowUp(
                    type="jwks",
                    url_source="response.body.jwks_uri",
                    request=FollowUpRequest(method=cast(Any, "OPTIONS")),
                    assertions=(HttpStatusAssertion(type="http_status", expected=200),),
                ),
            ),
        ),
    )
    requested_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_urls.append(str(request.url))
        return httpx.Response(
            200,
            json={"issuer": "https://example.com", "jwks_uri": "https://example.com/jwks"},
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = run_manifest(bad_manifest, environment="test", client=client)

    assert result.status == "failed"
    # Primary request should succeed; only the follow-up should fail
    assert requested_urls == ["https://example.com/.well-known/openid-configuration"]
    assert result.steps[0].status == "passed"
    assert result.steps[1].status == "failed"
    assert result.steps[1].message == "Unsupported request method: OPTIONS"


@pytest.mark.unit
def test_run_manifest_v0_strips_whitespace_from_follow_up_url() -> None:
    """v0 URL extraction strips surrounding whitespace before HTTPS validation."""
    requested_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_urls.append(str(request.url))
        if "openid-configuration" in str(request.url):
            return httpx.Response(
                200,
                json={
                    "issuer": "https://modelbank.example.com",
                    "jwks_uri": "  https://modelbank.example.com/jwks  ",
                },
            )
        return httpx.Response(200, json={"keys": [{"kid": "k1"}]})

    raw_manifest = manifest_config()
    first_test(raw_manifest)["assertions"] = [{"type": "http_status", "expected": 200}]
    manifest = parse_manifest(raw_manifest)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = run_manifest(manifest, environment="test", client=client)

    assert result.status == "passed"
    assert requested_urls == [
        "https://modelbank.example.com/.well-known/openid-configuration",
        "https://modelbank.example.com/jwks",
    ]


@pytest.mark.unit
def test_run_manifest_v0_rejects_non_string_follow_up_url() -> None:
    """v0 URL extraction fails explicitly when jwks_uri is not a string."""
    raw_manifest = manifest_config()
    first_test(raw_manifest)["assertions"] = [{"type": "http_status", "expected": 200}]
    manifest = parse_manifest(raw_manifest)

    with httpx.Client(
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                200,
                json={"issuer": "https://modelbank.example.com", "jwks_uri": 42},
            )
        )
    ) as client:
        result = run_manifest(manifest, environment="test", client=client)

    assert result.status == "failed"
    assert [step.status for step in result.steps] == ["passed", "failed"]
    assert result.steps[1].name == "openid-discovery.followUp"
    assert result.steps[1].message == "Unable to resolve follow-up URL from response.body.jwks_uri"


# --- v1 manifest executor tests ---


def v1_manifest_config() -> dict[str, JsonValue]:
    return {
        "schemaVersion": "v1",
        "name": "v1 discovery + JWKS",
        "steps": [
            {
                "id": "openid-discovery",
                "name": "OpenID discovery document",
                "request": {
                    "method": "GET",
                    "url": "https://modelbank.example.com/.well-known/openid-configuration",
                },
                "assertions": [
                    {"type": "http_status", "expected": 200},
                    {"type": "json_field", "path": "jwks_uri", "rule": "https_url"},
                ],
            },
            {
                "id": "jwks-fetch",
                "name": "JWKS endpoint",
                "request": {
                    "method": "GET",
                    "url": "${steps.openid-discovery.response.body.jwks_uri}",
                },
                "assertions": [
                    {"type": "http_status", "expected": 200},
                    {"type": "json_field", "path": "keys", "rule": "array"},
                ],
            },
        ],
    }


@pytest.mark.unit
def test_run_manifest_v1_multi_step_happy_path() -> None:
    requested_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_urls.append(str(request.url))
        if str(request.url) == "https://modelbank.example.com/.well-known/openid-configuration":
            return httpx.Response(
                200,
                json={
                    "issuer": "https://modelbank.example.com",
                    "jwks_uri": "https://modelbank.example.com/jwks",
                },
            )
        return httpx.Response(200, json={"keys": [{"kid": "signing-key"}]})

    manifest = parse_manifest(v1_manifest_config())
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = run_manifest(manifest, environment="ozone-model-bank", client=client)

    assert result.status == "passed"
    assert requested_urls == [
        "https://modelbank.example.com/.well-known/openid-configuration",
        "https://modelbank.example.com/jwks",
    ]
    assert [step.name for step in result.steps] == ["openid-discovery", "jwks-fetch"]
    assert result.to_json_object()["summary"] == {"total": 2, "passed": 2, "failed": 0, "warn": 0, "skipped": 0}


@pytest.mark.unit
def test_run_manifest_v1_substitution_resolves_across_steps() -> None:
    """Context carry-forward: second step URL comes from first step response."""

    def handler(request: httpx.Request) -> httpx.Response:
        if "openid-configuration" in str(request.url):
            return httpx.Response(
                200,
                json={"jwks_uri": "https://modelbank.example.com/certs"},
            )
        if str(request.url) == "https://modelbank.example.com/certs":
            return httpx.Response(200, json={"keys": [{"kid": "k1"}]})
        return httpx.Response(404, json={})

    manifest = parse_manifest(v1_manifest_config())
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = run_manifest(manifest, environment="test", client=client)

    assert result.status == "passed"
    assert result.steps[1].url == "https://modelbank.example.com/certs"


@pytest.mark.unit
def test_run_manifest_v1_failing_step_still_records_context() -> None:
    """A step whose assertions fail still provides context to later steps."""
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "fail-and-continue",
        "steps": [
            {
                "id": "discovery",
                "name": "Discovery",
                "request": {
                    "method": "GET",
                    "url": "https://example.com/.well-known/openid-configuration",
                },
                "assertions": [{"type": "http_status", "expected": 201}],  # Will fail (response is 200)
            },
            {
                "id": "jwks",
                "name": "JWKS",
                "request": {
                    "method": "GET",
                    "url": "${steps.discovery.response.body.jwks_uri}",
                },
                "assertions": [{"type": "http_status", "expected": 200}],
            },
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if "openid-configuration" in str(request.url):
            return httpx.Response(200, json={"jwks_uri": "https://example.com/jwks"})
        return httpx.Response(200, json={"keys": []})

    manifest = parse_manifest(raw_manifest)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = run_manifest(manifest, environment="test", client=client)

    # First step fails assertions but second step still resolves and passes
    assert result.steps[0].status == "failed"
    assert result.steps[1].status == "passed"
    assert result.steps[1].url == "https://example.com/jwks"


@pytest.mark.unit
def test_run_manifest_v1_unresolvable_placeholder_fails_cleanly() -> None:
    """Steps referencing a missing context field fail with a clear message."""
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "missing-ref",
        "steps": [
            {
                "id": "discovery",
                "name": "Discovery",
                "request": {
                    "method": "GET",
                    "url": "https://example.com/.well-known/openid-configuration",
                },
                "assertions": [{"type": "http_status", "expected": 200}],
            },
            {
                "id": "bad-ref",
                "name": "Bad reference",
                "request": {
                    "method": "GET",
                    "url": "${steps.discovery.response.body.nonexistent_field}",
                },
                "assertions": [{"type": "http_status", "expected": 200}],
            },
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"issuer": "https://example.com"})

    manifest = parse_manifest(raw_manifest)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = run_manifest(manifest, environment="test", client=client)

    assert result.status == "failed"
    assert result.steps[0].status == "passed"
    assert result.steps[1].status == "failed"
    assert "Placeholder resolution failed" in result.steps[1].message
    assert "nonexistent_field" in result.steps[1].message


@pytest.mark.unit
def test_run_manifest_v1_failed_request_skips_dependent_step() -> None:
    """A step with no response (transport error) marks dependent steps as SKIPPED.

    Per the PRD: SKIPPED — not FAILED — is the correct outcome when a test
    could not run because a prerequisite setup step failed.
    """
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "transport-fail",
        "steps": [
            {
                "id": "broken",
                "name": "Broken endpoint",
                "request": {
                    "method": "GET",
                    "url": "https://example.com/broken",
                },
                "assertions": [{"type": "http_status", "expected": 200}],
            },
            {
                "id": "dependent",
                "name": "Dependent step",
                "request": {
                    "method": "GET",
                    "url": "${steps.broken.response.body.next_url}",
                },
                "assertions": [{"type": "http_status", "expected": 200}],
            },
            {
                "id": "transitive",
                "name": "Transitive step",
                "request": {
                    "method": "GET",
                    "url": "${steps.dependent.response.body.next_url}",
                },
                "assertions": [{"type": "http_status", "expected": 200}],
            },
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    manifest = parse_manifest(raw_manifest)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = run_manifest(manifest, environment="test", client=client)

    # Aggregate failed because the first step failed.
    assert result.status == "failed"
    assert len(result.steps) == 3
    assert result.steps[0].status == "failed"
    assert result.steps[1].status == "skipped"
    assert result.steps[2].status == "skipped"
    assert "has no response" in result.steps[1].message
    assert result.steps[1].message.startswith("Skipped:")
    # Transitive skip: the skipped step is itself recorded with no response,
    # so steps referencing it also skip rather than fail.
    assert "has no response" in result.steps[2].message
    assert result.to_json_object()["summary"] == {
        "total": 3,
        "passed": 0,
        "failed": 1,
        "warn": 0,
        "skipped": 2,
    }


@pytest.mark.unit
def test_run_manifest_v1_skipped_step_triggered_by_header_placeholder() -> None:
    """A header placeholder referencing a no-response step yields SKIPPED, not FAILED."""
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "header-ref",
        "steps": [
            {
                "id": "broken",
                "name": "Broken endpoint",
                "request": {"method": "GET", "url": "https://example.com/broken"},
                "assertions": [{"type": "http_status", "expected": 200}],
            },
            {
                "id": "dependent",
                "name": "Dependent step",
                "request": {
                    "method": "POST",
                    "url": "https://example.com/ok",
                    "headers": {"X-Token": "${steps.broken.response.body.token}"},
                    "body": {"hello": "world"},
                },
                "assertions": [{"type": "http_status", "expected": 200}],
            },
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if "broken" in str(request.url):
            raise httpx.ConnectError("connection refused", request=request)
        return httpx.Response(200, json={})

    manifest = parse_manifest(raw_manifest)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = run_manifest(manifest, environment="test", client=client)

    assert result.steps[0].status == "failed"
    assert result.steps[1].status == "skipped"
    assert "has no response" in result.steps[1].message


@pytest.mark.unit
def test_run_manifest_v1_skipped_step_triggered_by_body_placeholder() -> None:
    """A body placeholder referencing a no-response step yields SKIPPED, not FAILED."""
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "body-ref",
        "steps": [
            {
                "id": "broken",
                "name": "Broken endpoint",
                "request": {"method": "GET", "url": "https://example.com/broken"},
                "assertions": [{"type": "http_status", "expected": 200}],
            },
            {
                "id": "dependent",
                "name": "Dependent step",
                "request": {
                    "method": "POST",
                    "url": "https://example.com/ok",
                    "body": {"token": "${steps.broken.response.body.token}"},
                },
                "assertions": [{"type": "http_status", "expected": 200}],
            },
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if "broken" in str(request.url):
            raise httpx.ConnectError("connection refused", request=request)
        return httpx.Response(200, json={})

    manifest = parse_manifest(raw_manifest)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = run_manifest(manifest, environment="test", client=client)

    assert result.steps[0].status == "failed"
    assert result.steps[1].status == "skipped"


@pytest.mark.unit
def test_run_manifest_v1_unresolvable_field_still_fails_not_skips() -> None:
    """Missing JSON field on a *successful* predecessor is FAILED, not SKIPPED.

    SKIPPED is reserved for the "prerequisite produced no response" case.
    A malformed path or missing field on an otherwise-successful step is a
    genuine resolution failure and must continue to be FAILED.
    """
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "missing-field",
        "steps": [
            {
                "id": "discovery",
                "name": "Discovery",
                "request": {"method": "GET", "url": "https://example.com/d"},
                "assertions": [{"type": "http_status", "expected": 200}],
            },
            {
                "id": "dependent",
                "name": "Dependent",
                "request": {
                    "method": "GET",
                    "url": "${steps.discovery.response.body.absent_field}",
                },
                "assertions": [{"type": "http_status", "expected": 200}],
            },
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"issuer": "https://example.com"})

    manifest = parse_manifest(raw_manifest)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = run_manifest(manifest, environment="test", client=client)

    assert result.steps[0].status == "passed"
    assert result.steps[1].status == "failed"
    assert "Placeholder resolution failed" in result.steps[1].message


@pytest.mark.unit
def test_run_manifest_v1_run_completes_all_steps_despite_failures() -> None:
    """The run continues through all steps even when some fail."""
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "three-steps",
        "steps": [
            {
                "id": "step-a",
                "name": "Step A",
                "request": {"method": "GET", "url": "https://example.com/a"},
                "assertions": [{"type": "http_status", "expected": 200}],
            },
            {
                "id": "step-b",
                "name": "Step B",
                "request": {"method": "GET", "url": "https://example.com/b"},
                "assertions": [{"type": "http_status", "expected": 201}],  # Will fail
            },
            {
                "id": "step-c",
                "name": "Step C",
                "request": {"method": "GET", "url": "https://example.com/c"},
                "assertions": [{"type": "http_status", "expected": 200}],
            },
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    manifest = parse_manifest(raw_manifest)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = run_manifest(manifest, environment="test", client=client)

    assert result.status == "failed"
    assert [s.status for s in result.steps] == ["passed", "failed", "passed"]


# --- v1 manifest executor tests: POST with headers and body ---


@pytest.mark.unit
def test_run_manifest_v1_post_with_body_and_headers() -> None:
    """POST step sends JSON body and custom headers."""
    captured_requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_requests.append(request)
        if "openid-configuration" in str(request.url):
            return httpx.Response(
                200,
                json={
                    "token_endpoint": "https://example.com/token",
                    "issuer": "https://example.com",
                },
            )
        return httpx.Response(200, json={"access_token": "tok_123"})

    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "POST with body",
        "steps": [
            {
                "id": "discovery",
                "name": "Discovery",
                "request": {
                    "method": "GET",
                    "url": "https://example.com/.well-known/openid-configuration",
                },
                "assertions": [{"type": "http_status", "expected": 200}],
            },
            {
                "id": "token",
                "name": "Token exchange",
                "request": {
                    "method": "POST",
                    "url": "${steps.discovery.response.body.token_endpoint}",
                    "headers": {
                        "X-Issuer": "${steps.discovery.response.body.issuer}",
                    },
                    "body": {
                        "grant_type": "authorization_code",
                        "code": "auth_code_123",
                    },
                },
                "assertions": [{"type": "http_status", "expected": 200}],
            },
        ],
    }

    manifest = parse_manifest(raw_manifest)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = run_manifest(manifest, environment="test", client=client)

    assert result.status == "passed"
    assert len(captured_requests) == 2
    # Second request is POST to the token endpoint
    token_request = captured_requests[1]
    assert token_request.method == "POST"
    assert str(token_request.url) == "https://example.com/token"
    assert token_request.headers["x-issuer"] == "https://example.com"

    assert json.loads(token_request.content) == {
        "grant_type": "authorization_code",
        "code": "auth_code_123",
    }


@pytest.mark.unit
def test_run_manifest_v1_post_resolves_body_placeholders() -> None:
    """Placeholders in body string leaves are resolved from context."""
    captured_requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_requests.append(request)
        if "openid-configuration" in str(request.url):
            return httpx.Response(
                200,
                json={"token_endpoint": "https://example.com/token"},
            )
        return httpx.Response(200, json={"access_token": "tok_abc"})

    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "Body placeholders",
        "steps": [
            {
                "id": "discovery",
                "name": "Discovery",
                "request": {
                    "method": "GET",
                    "url": "https://example.com/.well-known/openid-configuration",
                },
                "assertions": [{"type": "http_status", "expected": 200}],
            },
            {
                "id": "token",
                "name": "Token",
                "request": {
                    "method": "POST",
                    "url": "${steps.discovery.response.body.token_endpoint}",
                    "body": {
                        "redirect_uri": "${steps.discovery.response.body.token_endpoint}/callback",
                    },
                },
                "assertions": [{"type": "http_status", "expected": 200}],
            },
        ],
    }

    manifest = parse_manifest(raw_manifest)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = run_manifest(manifest, environment="test", client=client)

    assert result.status == "passed"

    token_body = json.loads(captured_requests[1].content)
    assert token_body["redirect_uri"] == "https://example.com/token/callback"


@pytest.mark.unit
def test_run_manifest_v1_get_unaffected_by_post_changes() -> None:
    """GET-only manifests continue to work exactly as before."""
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "GET only",
        "steps": [
            {
                "id": "health",
                "name": "Health",
                "request": {"method": "GET", "url": "https://example.com/health"},
                "assertions": [{"type": "http_status", "expected": 200}],
            }
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        return httpx.Response(200, json={"status": "ok"})

    manifest = parse_manifest(raw_manifest)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = run_manifest(manifest, environment="test", client=client)

    assert result.status == "passed"


@pytest.mark.unit
def test_run_manifest_v1_post_https_validation_on_resolved_url() -> None:
    """HTTPS validation applies to resolved URLs for POST steps."""
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "HTTP URL post-resolution",
        "steps": [
            {
                "id": "discovery",
                "name": "Discovery",
                "request": {
                    "method": "GET",
                    "url": "https://example.com/.well-known/openid-configuration",
                },
                "assertions": [{"type": "http_status", "expected": 200}],
            },
            {
                "id": "token",
                "name": "Token",
                "request": {
                    "method": "POST",
                    "url": "${steps.discovery.response.body.token_endpoint}",
                    "body": {"grant_type": "client_credentials"},
                },
                "assertions": [{"type": "http_status", "expected": 200}],
            },
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"token_endpoint": "http://insecure.example.com/token"},
        )

    manifest = parse_manifest(raw_manifest)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = run_manifest(manifest, environment="test", client=client)

    assert result.status == "failed"
    assert result.steps[1].status == "failed"
    assert "must be an HTTPS URL" in result.steps[1].message


@pytest.mark.unit
def test_run_manifest_v1_post_status_agnostic_4xx() -> None:
    """POST steps receiving 4xx are reported to assertions, not treated as errors."""
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "4xx on POST",
        "steps": [
            {
                "id": "create",
                "name": "Create resource",
                "request": {
                    "method": "POST",
                    "url": "https://example.com/api/resource",
                    "body": {"name": "test"},
                },
                "assertions": [{"type": "http_status", "expected": 400}],
            }
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "bad_request"})

    manifest = parse_manifest(raw_manifest)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = run_manifest(manifest, environment="test", client=client)

    assert result.status == "passed"
    assert result.steps[0].status == "passed"
    assert result.steps[0].status_code == 400


@pytest.mark.unit
def test_run_manifest_v1_rejects_resolved_header_with_control_chars() -> None:
    """Resolved header values containing control characters fail the step gracefully."""

    def handler(request: httpx.Request) -> httpx.Response:
        if "openid-configuration" in str(request.url):
            # Return a value with DEL (0x7F) embedded — simulates bad upstream data
            return httpx.Response(200, json={"api_token": "evil\x7fvalue"})
        return httpx.Response(200, json={"ok": True})

    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "Resolved header control char",
        "steps": [
            {
                "id": "discovery",
                "name": "Discovery",
                "request": {
                    "method": "GET",
                    "url": "https://example.com/.well-known/openid-configuration",
                },
                "assertions": [{"type": "http_status", "expected": 200}],
            },
            {
                "id": "use-token",
                "name": "Use token",
                "request": {
                    "method": "POST",
                    "url": "https://example.com/api",
                    "headers": {
                        "Authorization": "Bearer ${steps.discovery.response.body.api_token}",
                    },
                    "body": {"action": "test"},
                },
                "assertions": [{"type": "http_status", "expected": 200}],
            },
        ],
    }

    manifest = parse_manifest(raw_manifest)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = run_manifest(manifest, environment="test", client=client)

    # First step passes, second step fails due to resolved header validation
    assert result.steps[0].status == "passed"
    assert result.steps[1].status == "failed"
    assert "Resolved header validation failed" in (result.steps[1].message or "")
    assert "non-transportable character" in (result.steps[1].message or "")


@pytest.mark.unit
def test_run_manifest_v1_delete_204_passes_http_status_assertion() -> None:
    """A DELETE step returning 204 No Content must reach assertion evaluation.

    Regresses against the prior behaviour where ``send_json`` raised
    ``JsonHttpClientError("not valid JSON")`` for any empty-bodied response,
    preventing the executor from evaluating the user's
    ``http_status: 204`` assertion. RFC 9110 defines 204 as carrying no
    message body, so the transport must not reject it.
    """
    captured_requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_requests.append(request)
        # 204 No Content with zero-length body, as a spec-compliant
        # endpoint would emit for a successful resource deletion.
        return httpx.Response(204)

    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "DELETE 204 smoke",
        "steps": [
            {
                "id": "revoke-consent",
                "name": "Revoke consent",
                "request": {
                    "method": "DELETE",
                    "url": "https://example.com/consents/consent-123",
                },
                "assertions": [{"type": "http_status", "expected": 204}],
            },
        ],
    }

    manifest = parse_manifest(raw_manifest)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = run_manifest(manifest, environment="test", client=client)

    assert result.status == "passed"
    assert len(captured_requests) == 1
    assert captured_requests[0].method == "DELETE"
    assert result.steps[0].status == "passed"


# --- v1 manifest executor tests: form body dispatch (DL-0014) ---


@pytest.mark.unit
def test_run_manifest_v1_post_form_body_sends_urlencoded() -> None:
    """A FormBody step dispatches application/x-www-form-urlencoded with resolved values."""
    captured_requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_requests.append(request)
        if "openid-configuration" in str(request.url):
            return httpx.Response(
                200,
                json={"token_endpoint": "https://example.com/token"},
            )
        return httpx.Response(200, json={"access_token": "tok_form"})

    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "Form-body token exchange",
        "steps": [
            {
                "id": "discovery",
                "name": "Discovery",
                "request": {
                    "method": "GET",
                    "url": "https://example.com/.well-known/openid-configuration",
                },
                "assertions": [{"type": "http_status", "expected": 200}],
            },
            {
                "id": "token",
                "name": "Token",
                "request": {
                    "method": "POST",
                    "url": "${steps.discovery.response.body.token_endpoint}",
                    "body": {
                        "encoding": "form",
                        "fields": {
                            "grant_type": "authorization_code",
                            "code": "code with spaces & special=chars",
                        },
                    },
                },
                "assertions": [{"type": "http_status", "expected": 200}],
            },
        ],
    }

    manifest = parse_manifest(raw_manifest)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = run_manifest(manifest, environment="test", client=client)

    assert result.status == "passed"
    token_request = captured_requests[1]
    assert token_request.method == "POST"
    assert token_request.headers["content-type"] == "application/x-www-form-urlencoded"
    # Body is form-urlencoded by httpx; assert percent-encoded fragments are
    # present rather than the exact byte sequence (httpx chooses how to encode
    # spaces — typically as '+').
    wire_body = token_request.content.decode("ascii")
    assert "grant_type=authorization_code" in wire_body
    assert "code=" in wire_body
    assert "%26" in wire_body  # '&' inside a value must be percent-encoded
    assert "%3D" in wire_body  # '=' inside a value must be percent-encoded


@pytest.mark.unit
def test_run_manifest_v1_form_body_resolves_placeholders_in_values() -> None:
    """Placeholders in form-field values are resolved from the execution context."""
    captured_requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_requests.append(request)
        if "consent" in str(request.url):
            return httpx.Response(200, json={"code": "resolved-auth-code"})
        return httpx.Response(200, json={"access_token": "tok"})

    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "Form placeholder resolution",
        "steps": [
            {
                "id": "consent",
                "name": "Consent",
                "request": {"method": "GET", "url": "https://example.com/consent"},
                "assertions": [{"type": "http_status", "expected": 200}],
            },
            {
                "id": "token",
                "name": "Token",
                "request": {
                    "method": "POST",
                    "url": "https://example.com/token",
                    "body": {
                        "encoding": "form",
                        "fields": {
                            "code": "${steps.consent.response.body.code}",
                            "grant_type": "authorization_code",
                        },
                    },
                },
                "assertions": [{"type": "http_status", "expected": 200}],
            },
        ],
    }

    manifest = parse_manifest(raw_manifest)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = run_manifest(manifest, environment="test", client=client)

    assert result.status == "passed"
    wire_body = captured_requests[1].content.decode("ascii")
    assert "code=resolved-auth-code" in wire_body


@pytest.mark.unit
def test_run_manifest_v1_form_body_respects_manifest_content_type() -> None:
    """A manifest-supplied Content-Type overrides the default form Content-Type."""
    captured_requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_requests.append(request)
        return httpx.Response(200, json={"ok": True})

    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "Custom Content-Type",
        "steps": [
            {
                "id": "token",
                "name": "Token",
                "request": {
                    "method": "POST",
                    "url": "https://example.com/token",
                    "headers": {
                        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                    },
                    "body": {"encoding": "form", "fields": {"k": "v"}},
                },
                "assertions": [{"type": "http_status", "expected": 200}],
            }
        ],
    }

    manifest = parse_manifest(raw_manifest)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        run_manifest(manifest, environment="test", client=client)

    assert captured_requests[0].headers["content-type"] == "application/x-www-form-urlencoded; charset=UTF-8"


@pytest.mark.unit
def test_run_manifest_v1_form_body_step_record_omits_fields() -> None:
    """Form fields must not appear in the step result — masking is deferred (DL-0013).

    Locks in the DL-0013 deferral: until secrets masking lands, form-field
    values (which often carry OAuth2 secrets) are not recorded into the
    step result. The result still reports method, URL, and assertions, but
    no field names or values.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    # Generate the sentinel at runtime so no secret-shaped literal is
    # hardcoded in the test (Snyk: hardcoded non-cryptographic secret).
    # The test still verifies that whatever value flows through the
    # form-body field never appears in the recorded step result.
    secret_sentinel = f"sentinel-{secrets.token_hex(16)}"

    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "No field leak",
        "steps": [
            {
                "id": "token",
                "name": "Token",
                "request": {
                    "method": "POST",
                    "url": "https://example.com/token",
                    "body": {
                        "encoding": "form",
                        "fields": {
                            "client_secret": secret_sentinel,
                            "grant_type": "client_credentials",
                        },
                    },
                },
                "assertions": [{"type": "http_status", "expected": 200}],
            }
        ],
    }

    manifest = parse_manifest(raw_manifest)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = run_manifest(manifest, environment="test", client=client)

    assert result.steps[0].status == "passed"
    assert result.steps[0].url == "https://example.com/token"
    # Scan the full step result (incl. details) for any leak of the secret
    # or field name. ``str`` covers nested dataclasses/mappings without
    # imposing a JSON-serialisable constraint on the result.
    serialised = str(result.steps[0])
    assert secret_sentinel not in serialised
    assert "client_secret" not in serialised


@pytest.mark.unit
def test_run_manifest_v1_step_with_warning_emits_warn_when_assertions_pass() -> None:
    """A step declaring a ``warning`` is promoted to WARN when assertions pass.

    Implements the PRD outcome: ``WARN: test passed but a deprecation or
    risk signal applies. Does not block certification.`` The warning
    message is surfaced both in the step ``message`` and in ``details``.
    """
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "warn-on-pass",
        "steps": [
            {
                "id": "discovery",
                "name": "OpenID discovery",
                "request": {
                    "method": "GET",
                    "url": "https://example.com/.well-known/openid-configuration",
                },
                "assertions": [{"type": "http_status", "expected": 200}],
                "warning": "Field 'foo' is deprecated and will be removed in v4.1",
            },
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"issuer": "https://example.com"})

    manifest = parse_manifest(raw_manifest)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = run_manifest(manifest, environment="test", client=client)

    # Aggregate stays "passed" — WARN does not block certification (PRD).
    assert result.status == "passed"
    step = result.steps[0]
    assert step.status == "warn"
    assert "deprecated" in step.message
    assert step.details["warning"] == "Field 'foo' is deprecated and will be removed in v4.1"
    summary = result.to_json_object()["summary"]
    assert summary == {"total": 1, "passed": 0, "failed": 0, "warn": 1, "skipped": 0}


@pytest.mark.unit
def test_run_manifest_v1_step_with_warning_still_fails_when_assertion_fails() -> None:
    """A failing assertion produces FAILED regardless of any declared ``warning``.

    WARN is reserved for otherwise-passing steps; an assertion failure must
    not be downgraded to a non-blocking warning.
    """
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "warn-with-failure",
        "steps": [
            {
                "id": "discovery",
                "name": "OpenID discovery",
                "request": {"method": "GET", "url": "https://example.com/discovery"},
                "assertions": [{"type": "http_status", "expected": 200}],
                "warning": "deprecation notice",
            },
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={})

    manifest = parse_manifest(raw_manifest)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = run_manifest(manifest, environment="test", client=client)

    assert result.status == "failed"
    assert result.steps[0].status == "failed"
    assert "warning" not in result.steps[0].details


@pytest.mark.unit
def test_run_manifest_v1_warn_step_does_not_fail_aggregate_with_passed_step() -> None:
    """A run containing only PASS and WARN steps aggregates to ``passed``."""
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "mixed-pass-warn",
        "steps": [
            {
                "id": "plain",
                "name": "Plain step",
                "request": {"method": "GET", "url": "https://example.com/a"},
                "assertions": [{"type": "http_status", "expected": 200}],
            },
            {
                "id": "warned",
                "name": "Warned step",
                "request": {"method": "GET", "url": "https://example.com/b"},
                "assertions": [{"type": "http_status", "expected": 200}],
                "warning": "soon-to-be-removed",
            },
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    manifest = parse_manifest(raw_manifest)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = run_manifest(manifest, environment="test", client=client)

    assert result.status == "passed"
    assert [step.status for step in result.steps] == ["passed", "warn"]
    assert result.to_json_object()["summary"] == {
        "total": 2,
        "passed": 1,
        "failed": 0,
        "warn": 1,
        "skipped": 0,
    }


# ---------------------------------------------------------------------------
# Request/response evidence capture with sensitive-data masking
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_run_manifest_v1_omits_evidence_on_pass() -> None:
    """PRD: passing steps include summary only — no request/response evidence.

    Keeps reports lean and avoids broadening the surface area of accidental
    disclosure for runs where everything went right.
    """
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "evidence-pass",
        "steps": [
            {
                "id": "ok",
                "name": "OK",
                "request": {"method": "GET", "url": "https://example.com/ok"},
                "assertions": [{"type": "http_status", "expected": 200}],
            }
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"access_token": "should-not-leak"})

    manifest = parse_manifest(raw_manifest)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = run_manifest(manifest, environment="test", client=client)

    assert result.steps[0].status == "passed"
    details = dict(result.steps[0].details)
    assert "request" not in details
    assert "response" not in details


@pytest.mark.unit
def test_run_manifest_v1_failed_step_includes_masked_request_and_response_evidence() -> None:
    """FAIL step carries masked request body, headers, and response body.

    PRD: *"Full request and response captured on FAIL, WARN, and SKIPPED."*
    Sensitive credential fields and the Authorization header are masked.
    """
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "evidence-fail",
        "steps": [
            {
                "id": "token-exchange",
                "name": "Token exchange",
                "request": {
                    "method": "POST",
                    "url": "https://example.com/token",
                    "headers": {
                        "Authorization": "Bearer leaky-bearer",
                        "Accept": "application/json",
                    },
                    "body": {"client_secret": "very-secret", "scope": "accounts"},  # pragma: allowlist secret
                },
                "assertions": [{"type": "http_status", "expected": 200}],
            }
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "invalid_client", "access_token": "leaky"})

    manifest = parse_manifest(raw_manifest)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = run_manifest(manifest, environment="test", client=client)

    step = result.steps[0]
    assert step.status == "failed"
    details = dict(step.details)
    assert details["request"] == {
        "method": "POST",
        "url": "https://example.com/token",
        "headers": {"Authorization": "***", "Accept": "application/json"},
        "body": {"client_secret": "***", "scope": "accounts"},
    }
    assert details["response"] == {
        "statusCode": 400,
        "body": {"error": "invalid_client", "access_token": "***"},
    }


@pytest.mark.unit
def test_run_manifest_v1_failed_step_masks_form_body_credentials() -> None:
    """FAIL step with a form body masks credential fields in evidence."""
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "evidence-form-fail",
        "steps": [
            {
                "id": "token",
                "name": "Token",
                "request": {
                    "method": "POST",
                    "url": "https://example.com/token",
                    "body": {
                        "encoding": "form",
                        "fields": {
                            "grant_type": "authorization_code",
                            "code": "auth-code-secret",
                            "client_secret": "shh",  # pragma: allowlist secret
                        },
                    },
                },
                "assertions": [{"type": "http_status", "expected": 200}],
            }
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "invalid_grant"})

    manifest = parse_manifest(raw_manifest)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = run_manifest(manifest, environment="test", client=client)

    step = result.steps[0]
    assert step.status == "failed"
    details = dict(step.details)
    request = cast("dict[str, Any]", details["request"])
    assert request["form"] == {
        "grant_type": "authorization_code",
        "code": "***",
        "client_secret": "***",
    }
    assert "body" not in request


@pytest.mark.unit
def test_run_manifest_v1_warn_step_includes_evidence() -> None:
    """WARN step carries request/response evidence alongside the warning."""
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "evidence-warn",
        "steps": [
            {
                "id": "deprecated",
                "name": "Deprecated endpoint",
                "request": {"method": "GET", "url": "https://example.com/v1/deprecated"},
                "assertions": [{"type": "http_status", "expected": 200}],
                "warning": "Endpoint deprecated in v4",
            }
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True, "access_token": "leaky"})

    manifest = parse_manifest(raw_manifest)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = run_manifest(manifest, environment="test", client=client)

    step = result.steps[0]
    assert step.status == "warn"
    details = dict(step.details)
    assert "request" in details
    assert details["response"] == {
        "statusCode": 200,
        "body": {"ok": True, "access_token": "***"},
    }


@pytest.mark.unit
def test_run_manifest_v1_skipped_step_includes_request_evidence_without_response() -> None:
    """SKIPPED step carries request evidence but no response (none was received)."""
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "evidence-skipped",
        "steps": [
            {
                "id": "broken",
                "name": "Broken",
                "request": {"method": "GET", "url": "https://example.com/broken"},
                "assertions": [{"type": "http_status", "expected": 200}],
            },
            {
                "id": "dependent",
                "name": "Dependent",
                "request": {
                    "method": "GET",
                    "url": "https://example.com/${steps.broken.response.body.path}",
                },
                "assertions": [{"type": "http_status", "expected": 200}],
            },
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    manifest = parse_manifest(raw_manifest)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = run_manifest(manifest, environment="test", client=client)

    skipped = result.steps[1]
    assert skipped.status == "skipped"
    details = dict(skipped.details)
    request = cast("dict[str, Any]", details["request"])
    assert request["method"] == "GET"
    # URL still carries the unresolved placeholder because resolution failed.
    assert "${steps.broken.response.body.path}" in request["url"]
    assert "response" not in details


@pytest.mark.unit
def test_run_manifest_v1_threads_mandatory_into_step_result_and_eligibility() -> None:
    """Executor carries ``mandatory`` from manifest into StepResult and eligibility block."""
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "mandatory-mix",
        "steps": [
            {
                "id": "core",
                "name": "Mandatory core",
                "request": {"method": "GET", "url": "https://example.com/core"},
                "assertions": [{"type": "http_status", "expected": 200}],
                "mandatory": True,
            },
            {
                "id": "extra",
                "name": "Optional extra",
                "request": {"method": "GET", "url": "https://example.com/extra"},
                "assertions": [{"type": "http_status", "expected": 200}],
            },
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    manifest = parse_manifest(raw_manifest)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = run_manifest(manifest, environment="test", client=client)

    assert result.steps[0].mandatory is True
    assert result.steps[1].mandatory is False
    block = result.to_json_object()["certificationEligibility"]
    assert isinstance(block, dict)
    assert block["eligible"] is True
    assert block["mandatoryTotal"] == 1


@pytest.mark.unit
def test_run_manifest_v0_eligibility_block_reports_no_mandatory_steps() -> None:
    """v0 manifests have no mandatory concept and so are never eligible."""
    raw_manifest = manifest_config()
    requested_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_urls.append(str(request.url))
        if str(request.url) == "https://modelbank.example.com/.well-known/openid-configuration":
            return httpx.Response(
                200,
                json={
                    "issuer": "https://modelbank.example.com",
                    "jwks_uri": "https://modelbank.example.com/jwks",
                },
            )
        return httpx.Response(200, json={"keys": [{"kid": "k"}]})

    manifest = parse_manifest(raw_manifest)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = run_manifest(manifest, environment="ozone-model-bank", client=client)

    block = result.to_json_object()["certificationEligibility"]
    assert isinstance(block, dict)
    assert block["eligible"] is False
    assert block["mandatoryTotal"] == 0
    assert "No mandatory steps" in str(block["reason"])


@pytest.mark.unit
def test_run_manifest_emits_full_event_sequence_for_v0_success() -> None:
    """v0 manifest run emits run-started, step events for primary + follow-up, then run-completed."""
    from conformance.execution_log import BufferedExecutionLogger

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == "https://modelbank.example.com/.well-known/openid-configuration":
            return httpx.Response(
                200,
                json={
                    "issuer": "https://modelbank.example.com",
                    "jwks_uri": "https://modelbank.example.com/jwks",
                },
            )
        return httpx.Response(200, json={"keys": [{"kid": "k"}]})

    manifest = parse_manifest(manifest_config())
    execution_logger = BufferedExecutionLogger(run_id="run-x", developer_mode=False)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        run_manifest(manifest, environment="env", client=client, execution_logger=execution_logger)

    events = execution_logger.events()
    types = [event.type for event in events]
    assert types[0] == "run-started"
    assert types[-1] == "run-completed"
    assert "request-sent" in types
    assert "response-received" in types
    assert "assertion-evaluated" in types
    assert types.count("step-started") == 2
    assert types.count("step-completed") == 2


@pytest.mark.unit
def test_run_manifest_request_sent_masks_authorization_header() -> None:
    """request-sent event masks Authorization header values by default."""
    from conformance.execution_log import BufferedExecutionLogger
    from conformance.manifest import parse_manifest as parse_v1

    v1_manifest = parse_v1(
        {
            "schemaVersion": "v1",
            "name": "auth-header",
            "steps": [
                {
                    "id": "discovery",
                    "name": "discovery",
                    "request": {
                        "method": "GET",
                        "url": "https://modelbank.example.com/x",
                        "headers": {"Authorization": "Bearer super-secret"},
                    },
                    "assertions": [{"type": "http_status", "expected": 200}],
                }
            ],
        }
    )

    execution_logger = BufferedExecutionLogger(run_id="r", developer_mode=False)
    with httpx.Client(transport=httpx.MockTransport(lambda _r: httpx.Response(200, json={}))) as client:
        run_manifest(v1_manifest, environment="env", client=client, execution_logger=execution_logger)

    request_events = [event for event in execution_logger.events() if event.type == "request-sent"]
    assert len(request_events) == 1
    headers = request_events[0].payload["headers"]
    assert isinstance(headers, dict)
    assert headers["Authorization"] == "***"


@pytest.mark.unit
def test_run_manifest_emits_placeholder_error_event() -> None:
    """Unresolvable placeholder produces a placeholder-error event for the failing step."""
    from conformance.execution_log import BufferedExecutionLogger
    from conformance.manifest import parse_manifest as parse_v1

    v1_manifest = parse_v1(
        {
            "schemaVersion": "v1",
            "name": "ph",
            "steps": [
                {
                    "id": "discovery",
                    "name": "discovery",
                    "request": {"method": "GET", "url": "https://x.example.com/d"},
                    "assertions": [{"type": "http_status", "expected": 200}],
                },
                {
                    "id": "broken",
                    "name": "broken",
                    "request": {
                        "method": "GET",
                        "url": "https://x.example.com/${steps.discovery.response.body.missing}",
                    },
                    "assertions": [{"type": "http_status", "expected": 200}],
                },
            ],
        }
    )

    execution_logger = BufferedExecutionLogger(run_id="r", developer_mode=False)
    with httpx.Client(transport=httpx.MockTransport(lambda _r: httpx.Response(200, json={}))) as client:
        run_manifest(v1_manifest, environment="env", client=client, execution_logger=execution_logger)

    types = [event.type for event in execution_logger.events()]
    assert "placeholder-error" in types


@pytest.mark.unit
def test_run_manifest_emits_application_error_on_unexpected_engine_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unexpected engine exception emits application-error then re-raises.

    If an exception escapes before any step completes (e.g. the inner
    dispatch function raises unexpectedly), the log must still contain an
    application-error event so the NDJSON log is always self-terminating on
    crashes — run-started is always followed by a terminal event.
    """
    from conformance.execution_log import BufferedExecutionLogger
    from conformance.manifest import HttpStatusAssertion, Manifest, ManifestRequest, ManifestStep

    expected_error = RuntimeError("unexpected engine failure")

    def boom(*_args: object, **_kwargs: object) -> None:
        raise expected_error

    monkeypatch.setattr("conformance.executor._run_manifest_v1", boom)

    v1_manifest = Manifest(
        schema_version="v1",
        name="boom",
        steps=(
            ManifestStep(
                id="s1",
                name="s1",
                request=ManifestRequest(method="GET", url="https://example.com/"),
                assertions=(HttpStatusAssertion(type="http_status", expected=200),),
            ),
        ),
    )
    execution_logger = BufferedExecutionLogger(run_id="r", developer_mode=False)

    mock_transport = httpx.MockTransport(lambda _r: httpx.Response(200, json={}))
    with pytest.raises(RuntimeError) as exc_info, httpx.Client(transport=mock_transport) as client:
        run_manifest(v1_manifest, environment="env", client=client, execution_logger=execution_logger)

    assert exc_info.value is expected_error
    types = [event.type for event in execution_logger.events()]
    assert types[0] == "run-started"
    assert types[-1] == "application-error"


# ─── TestPlan deselection ────────────────────────────────────────────────────


def _plan_v1_manifest() -> dict[str, JsonValue]:
    """Return a small v1 manifest with one mandatory and one non-mandatory step."""
    return cast(
        "dict[str, JsonValue]",
        {
            "schemaVersion": "v1",
            "name": "plan-test",
            "steps": [
                {
                    "id": "mandatory-step",
                    "name": "Mandatory step",
                    "mandatory": True,
                    "request": {"method": "GET", "url": "https://example.com/a"},
                    "assertions": [{"type": "http_status", "expected": 200}],
                },
                {
                    "id": "optional-step",
                    "name": "Optional step",
                    "request": {"method": "GET", "url": "https://example.com/b"},
                    "assertions": [{"type": "http_status", "expected": 200}],
                },
            ],
        },
    )


@pytest.mark.unit
def test_run_manifest_deselected_step_does_not_run_or_produce_result() -> None:
    """A deselected step is silently absent from results and never fetched."""
    from conformance.test_plan import TestPlan

    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        return httpx.Response(200, json={})

    manifest = parse_manifest(_plan_v1_manifest())
    plan = TestPlan.default_plan_from_manifest(manifest).with_deselection(["optional-step"])

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = run_manifest(manifest, environment="env", client=client, plan=plan)

    assert requested == ["https://example.com/a"]
    assert [step.name for step in result.steps] == ["mandatory-step"]


@pytest.mark.unit
def test_run_manifest_emits_step_deselected_before_step_started() -> None:
    """One ``step-deselected`` event per deselected step, before any ``step-started``."""
    from conformance.execution_log import BufferedExecutionLogger
    from conformance.test_plan import TestPlan

    manifest = parse_manifest(_plan_v1_manifest())
    plan = TestPlan.default_plan_from_manifest(manifest).with_deselection(["optional-step"])
    execution_logger = BufferedExecutionLogger(run_id="r", developer_mode=False)

    with httpx.Client(transport=httpx.MockTransport(lambda _r: httpx.Response(200, json={}))) as client:
        run_manifest(manifest, environment="env", client=client, execution_logger=execution_logger, plan=plan)

    types = [event.type for event in execution_logger.events()]
    assert types[0] == "run-started"
    assert types[-1] == "run-completed"
    deselected_index = types.index("step-deselected")
    step_started_index = types.index("step-started")
    assert deselected_index < step_started_index

    deselected_events = [event for event in execution_logger.events() if event.type == "step-deselected"]
    assert len(deselected_events) == 1
    assert deselected_events[0].step_id == "optional-step"
    assert deselected_events[0].payload == {"mandatory": False}


@pytest.mark.unit
def test_run_manifest_default_plan_when_none_passed_preserves_legacy_behaviour() -> None:
    """Omitting ``plan`` runs every step (the default plan), unchanged from before."""
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        return httpx.Response(200, json={})

    manifest = parse_manifest(_plan_v1_manifest())
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = run_manifest(manifest, environment="env", client=client)

    assert requested == ["https://example.com/a", "https://example.com/b"]
    assert [step.name for step in result.steps] == ["mandatory-step", "optional-step"]


@pytest.mark.unit
def test_run_manifest_deselected_mandatory_flips_eligibility() -> None:
    """Deselecting a mandatory step surfaces in certificationEligibility."""
    from conformance.test_plan import TestPlan

    manifest = parse_manifest(_plan_v1_manifest())
    plan = TestPlan.default_plan_from_manifest(manifest).with_deselection(["mandatory-step"])

    with httpx.Client(transport=httpx.MockTransport(lambda _r: httpx.Response(200, json={}))) as client:
        result = run_manifest(manifest, environment="env", client=client, plan=plan)

    eligibility = result.to_json_object()["certificationEligibility"]
    assert isinstance(eligibility, dict)
    assert eligibility["eligible"] is False
    assert eligibility["reason"] == "Mandatory steps were deselected from the plan"
    assert eligibility["mandatoryDeselected"] == 1
    assert eligibility["mandatoryDeselectedStepIds"] == ["mandatory-step"]


@pytest.mark.unit
def test_run_manifest_plan_block_present_when_plan_supplied() -> None:
    """The result file gains a top-level ``plan`` block when a plan ran."""
    from conformance.test_plan import TestPlan

    manifest = parse_manifest(_plan_v1_manifest())
    plan = TestPlan.default_plan_from_manifest(manifest)

    with httpx.Client(transport=httpx.MockTransport(lambda _r: httpx.Response(200, json={}))) as client:
        result = run_manifest(manifest, environment="env", client=client, plan=plan)

    rendered = result.to_json_object()
    assert rendered["plan"] == {
        "totalSteps": 2,
        "selectedSteps": 2,
        "deselectedSteps": 0,
        "mandatorySelected": 1,
        "mandatoryDeselected": 0,
    }


# --- PSU plumbing: run_id + AuthSessionStore wiring (Phase 1) ---


def _trivial_v1_manifest() -> dict[str, JsonValue]:
    """Return a single-step v1 manifest used by Phase 1 plumbing tests."""
    return {
        "schemaVersion": "v1",
        "name": "plumbing",
        "steps": [
            {
                "id": "ping",
                "name": "Ping",
                "request": {"method": "GET", "url": "https://modelbank.example.com/ping"},
                "assertions": [{"type": "http_status", "expected": 200}],
            }
        ],
    }


class _RecordingAuthSessionStore(AuthSessionStore):
    """Sentinel :class:`AuthSessionStore` subclass for identity assertions.

    Used by Phase 1 wiring tests to prove that the store passed by the caller
    is the very same instance threaded through to the per-step executor — not
    a freshly-constructed default.
    """


@pytest.mark.unit
def test_run_manifest_generates_run_id_when_caller_supplies_none() -> None:
    """``run_manifest`` must Just Work without a ``run_id`` (CLI/legacy callers)."""
    manifest = parse_manifest(_trivial_v1_manifest())

    with httpx.Client(transport=httpx.MockTransport(lambda _r: httpx.Response(200, json={}))) as client:
        # No run_id, no auth_session_store — must not raise and must execute the step.
        result = run_manifest(manifest, environment="env", client=client)

    assert result.status == "passed"
    assert [step.status for step in result.steps] == ["passed"]


@pytest.mark.unit
def test_run_manifest_reuses_logger_run_id_when_caller_supplies_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """A stateful logger's run ID is reused for per-step PSU correlation."""
    from conformance import executor as executor_module
    from conformance.execution_log import BufferedExecutionLogger

    captured: dict[str, object] = {}
    real_execute_v1_step = executor_module._execute_v1_step

    def fake_execute_v1_step(
        manifest_step: Any,
        *,
        context: Any,
        client: Any,
        execution_logger: Any,
        run_id: str,
        auth_session_store: AuthSessionStore,
    ) -> tuple[Any, Any]:
        """Capture the threaded run ID before delegating to the real executor."""
        captured["run_id"] = run_id
        captured["store"] = auth_session_store
        return real_execute_v1_step(
            manifest_step,
            context=context,
            client=client,
            execution_logger=execution_logger,
            run_id=run_id,
            auth_session_store=auth_session_store,
        )

    sentinel_store = _RecordingAuthSessionStore()
    manifest = parse_manifest(_trivial_v1_manifest())
    execution_logger = BufferedExecutionLogger(run_id="logger-run", developer_mode=False)
    monkeypatch.setattr(executor_module, "_execute_v1_step", fake_execute_v1_step)

    with httpx.Client(transport=httpx.MockTransport(lambda _r: httpx.Response(200, json={}))) as client:
        run_manifest(
            manifest,
            environment="env",
            client=client,
            execution_logger=execution_logger,
            auth_session_store=sentinel_store,
        )

    assert captured["run_id"] == "logger-run"
    assert captured["store"] is sentinel_store


@pytest.mark.unit
def test_run_manifest_threads_caller_supplied_store_to_steps() -> None:
    """The caller's store instance must reach the per-step executor unchanged."""
    captured: dict[str, object] = {}

    def fake_execute_v1_step(
        manifest_step: Any,
        *,
        context: Any,
        client: Any,
        execution_logger: Any,
        run_id: str,
        auth_session_store: AuthSessionStore,
    ) -> tuple[Any, Any]:
        """Capture the threaded run_id and store, then delegate to the real impl."""
        captured["run_id"] = run_id
        captured["store"] = auth_session_store
        return _real_execute_v1_step(
            manifest_step,
            context=context,
            client=client,
            execution_logger=execution_logger,
            run_id=run_id,
            auth_session_store=auth_session_store,
        )

    from conformance import executor as executor_module

    sentinel_store = _RecordingAuthSessionStore()
    manifest = parse_manifest(_trivial_v1_manifest())
    _real_execute_v1_step = executor_module._execute_v1_step
    try:
        executor_module._execute_v1_step = fake_execute_v1_step
        with httpx.Client(transport=httpx.MockTransport(lambda _r: httpx.Response(200, json={}))) as client:
            run_manifest(
                manifest,
                environment="env",
                client=client,
                run_id="run-abc",
                auth_session_store=sentinel_store,
            )
    finally:
        executor_module._execute_v1_step = _real_execute_v1_step

    assert captured["run_id"] == "run-abc"
    assert captured["store"] is sentinel_store


# --- PSU authorisation executor: manual mode (Phase 3) ---


class _FakeClock:
    """Deterministic monotonic clock for PSU polling tests."""

    def __init__(self, *, on_sleep: Callable[[], None] | None = None) -> None:
        """Initialise at time zero.

        Args:
            on_sleep: Optional callback invoked after each fake sleep.
        """
        self.now = 0.0
        self.sleep_calls = 0
        self._on_sleep = on_sleep

    def monotonic(self) -> float:
        """Return the current fake monotonic time.

        Returns:
            Current fake time in seconds.
        """
        return self.now

    def sleep(self, seconds: float) -> None:
        """Advance fake time and invoke the optional hook.

        Args:
            seconds: Number of fake seconds to advance.
        """
        self.now += seconds
        self.sleep_calls += 1
        if self._on_sleep is not None:
            self._on_sleep()


def _psu_manual_step(**overrides: Any) -> PsuAuthorizationStep:
    """Build a parsed PSU manual step for executor unit tests.

    Args:
        overrides: Dataclass field overrides applied to the default step.

    Returns:
        Parsed :class:`PsuAuthorizationStep` instance.
    """
    data: dict[str, Any] = {
        "id": "psu",
        "name": "PSU authorisation",
        "mode": "manual",
        "authorization_endpoint": "https://auth.example.com/authorize?existing=1",
        "client_id": "client-123",
        "redirect_uri": "https://conformance.example.com/callback",
        "response_type": "code id_token",
        "scope": "openid accounts",
        "state": "s" * 32,
        "request_object": None,
        "timeout_seconds": 2,
        "mandatory": False,
        "optional": False,
    }
    data.update(overrides)
    return PsuAuthorizationStep(**data)


def _psu_headless_step(**overrides: Any) -> PsuAuthorizationStep:
    """Build a parsed PSU headless step for executor unit tests.

    Args:
        overrides: Dataclass field overrides applied to the default step.

    Returns:
        Parsed :class:`PsuAuthorizationStep` instance in headless mode.
    """
    return _psu_manual_step(mode="headless", **overrides)


@pytest.mark.unit
def test_psu_manual_step_captures_code_into_context() -> None:
    """Manual mode polls until callback capture and records code for placeholders."""
    store = AuthSessionStore()
    step = _psu_manual_step()

    def capture_once() -> None:
        if store.get("run-1", "s" * 32) is not None:
            store.capture_code("s" * 32, "auth-code-123")

    fake_clock = _FakeClock(on_sleep=capture_once)
    execution_logger = BufferedExecutionLogger(run_id="run-1", developer_mode=False)

    result, context = _execute_v1_psu_step(
        step,
        context=ExecutionContext(),
        client=httpx.Client(transport=httpx.MockTransport(lambda _request: httpx.Response(500))),
        run_id="run-1",
        auth_session_store=store,
        execution_logger=execution_logger,
        clock=fake_clock.monotonic,
        sleep=fake_clock.sleep,
    )

    assert result.status == "passed"
    assert result.url is not None
    assert "existing=1" in result.url
    assert "response_type=code+id_token" in result.url
    assert context.steps["psu"].response is not None
    assert context.steps["psu"].response.body["code"] == "auth-code-123"
    url_events = [event for event in execution_logger.events() if event.type == "psu-authorization-url"]
    assert len(url_events) == 1
    assert url_events[0].payload["client_id"] == "***"  # noqa: S105 — masked sentinel, not a real secret


@pytest.mark.unit
def test_psu_manual_step_records_authorization_error() -> None:
    """Manual mode converts an ASPSP error redirect into a failed step."""
    store = AuthSessionStore()
    step = _psu_manual_step()

    def capture_error_once() -> None:
        if store.get("run-err", "s" * 32) is not None:
            store.capture_error("s" * 32, "access_denied", "PSU declined consent")

    fake_clock = _FakeClock(on_sleep=capture_error_once)
    result, context = _execute_v1_psu_step(
        step,
        context=ExecutionContext(),
        client=httpx.Client(transport=httpx.MockTransport(lambda _request: httpx.Response(500))),
        run_id="run-err",
        auth_session_store=store,
        execution_logger=BufferedExecutionLogger(run_id="run-err", developer_mode=False),
        clock=fake_clock.monotonic,
        sleep=fake_clock.sleep,
    )

    assert result.status == "failed"
    assert result.details["error"] == "access_denied"
    assert result.details["error_description"] == "PSU declined consent"
    assert result.details["request"] != {}
    assert context.steps["psu"].response is None


@pytest.mark.unit
def test_psu_manual_step_times_out() -> None:
    """Manual mode fails when no callback resolves before the deadline."""
    fake_clock = _FakeClock()
    result, context = _execute_v1_psu_step(
        _psu_manual_step(timeout_seconds=1),
        context=ExecutionContext(),
        client=httpx.Client(transport=httpx.MockTransport(lambda _request: httpx.Response(500))),
        run_id="run-timeout",
        auth_session_store=AuthSessionStore(),
        execution_logger=BufferedExecutionLogger(run_id="run-timeout", developer_mode=False),
        clock=fake_clock.monotonic,
        sleep=fake_clock.sleep,
    )

    assert result.status == "failed"
    assert result.details["timeoutSeconds"] == 1
    assert result.details["request"] != {}
    assert context.steps["psu"].response is None


@pytest.mark.unit
def test_psu_manual_timeout_masks_authorization_url_query_evidence() -> None:
    """Non-PASS PSU results mask credential-bearing authorization URL params."""
    fake_clock = _FakeClock()
    result, context = _execute_v1_psu_step(
        _psu_manual_step(
            authorization_endpoint="https://auth.example.com/authorize?client_assertion=inline.assertion",
            request_object="signed.request.jwt",
            timeout_seconds=1,
        ),
        context=ExecutionContext(),
        client=httpx.Client(transport=httpx.MockTransport(lambda _request: httpx.Response(500))),
        run_id="run-timeout-masked-url",
        auth_session_store=AuthSessionStore(),
        execution_logger=BufferedExecutionLogger(run_id="run-timeout-masked-url", developer_mode=False),
        clock=fake_clock.monotonic,
        sleep=fake_clock.sleep,
    )

    rendered = json.dumps(result.to_json_object())
    request_details = cast("dict[str, Any]", result.details["request"])
    request_url = cast("str", request_details["url"])
    assert result.status == "failed"
    assert result.url == request_url
    assert "client_assertion=***" in request_url
    assert "request=***" in request_url
    assert context.steps["psu"].request.url == request_url
    assert "inline.assertion" not in rendered
    assert "signed.request.jwt" not in rendered


@pytest.mark.unit
def test_psu_manual_step_records_masked_request_url_for_placeholders() -> None:
    """PSU request URLs stored in context are safe for downstream placeholders."""
    store = AuthSessionStore()
    step = _psu_manual_step(
        authorization_endpoint="https://auth.example.com/authorize?client_assertion=inline.assertion",
        request_object="signed.request.jwt",
    )

    def capture_once() -> None:
        if store.get("run-masked-context", "s" * 32) is not None:
            store.capture_code("s" * 32, "auth-code-123")

    fake_clock = _FakeClock(on_sleep=capture_once)
    result, context = _execute_v1_psu_step(
        step,
        context=ExecutionContext(),
        client=httpx.Client(transport=httpx.MockTransport(lambda _request: httpx.Response(500))),
        run_id="run-masked-context",
        auth_session_store=store,
        execution_logger=BufferedExecutionLogger(run_id="run-masked-context", developer_mode=False),
        clock=fake_clock.monotonic,
        sleep=fake_clock.sleep,
    )

    request_url = context.steps["psu"].request.url
    assert result.status == "passed"
    assert result.url == request_url
    assert "client_assertion=***" in request_url
    assert "request=***" in request_url
    assert "inline.assertion" not in request_url
    assert "signed.request.jwt" not in request_url


@pytest.mark.unit
def test_psu_manual_step_placeholder_failure_records_masked_request_url() -> None:
    """PSU placeholder failures store the masked endpoint template in context."""
    result, context = _execute_v1_psu_step(
        _psu_manual_step(
            authorization_endpoint="https://auth.example.com/authorize?client_assertion=inline.assertion",
            client_id="${steps.missing.response.body.client_id}",
        ),
        context=ExecutionContext(),
        client=httpx.Client(transport=httpx.MockTransport(lambda _request: httpx.Response(500))),
        run_id="run-placeholder-failure",
        auth_session_store=AuthSessionStore(),
        execution_logger=BufferedExecutionLogger(run_id="run-placeholder-failure", developer_mode=False),
        clock=_FakeClock().monotonic,
        sleep=_FakeClock().sleep,
    )

    assert result.status == "failed"
    assert context.steps["psu"].request.url == "https://auth.example.com/authorize?client_assertion=***"
    assert "inline.assertion" not in json.dumps(result.to_json_object())


@pytest.mark.unit
def test_psu_manual_step_register_failure_records_masked_request_url() -> None:
    """PSU auth-session failures store the masked resolved endpoint in context."""
    store = AuthSessionStore()
    store.register("other-run", state="d" * 32)

    result, context = _execute_v1_psu_step(
        _psu_manual_step(
            authorization_endpoint="https://auth.example.com/authorize?client_assertion=inline.assertion",
            state="d" * 32,
        ),
        context=ExecutionContext(),
        client=httpx.Client(transport=httpx.MockTransport(lambda _request: httpx.Response(500))),
        run_id="run-duplicate-masked-url",
        auth_session_store=store,
        execution_logger=BufferedExecutionLogger(run_id="run-duplicate-masked-url", developer_mode=False),
        clock=_FakeClock().monotonic,
        sleep=_FakeClock().sleep,
    )

    assert result.status == "failed"
    assert context.steps["psu"].request.url == "https://auth.example.com/authorize?client_assertion=***"
    assert "inline.assertion" not in json.dumps(result.to_json_object())


@pytest.mark.unit
def test_run_manifest_mandatory_psu_timeout_counts_as_mandatory_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    """A mandatory PSU timeout is counted as failed mandatory coverage, not skipped."""
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "mandatory psu timeout",
        "steps": [
            {
                "kind": "psu-authorization",
                "id": "psu",
                "name": "PSU authorisation",
                "mode": "manual",
                "authorizationEndpoint": "https://auth.example.com/authorize",
                "clientId": "client-123",
                "redirectUri": "https://conformance.example.com/callback",
                "state": "t" * 32,
                "timeoutSeconds": 1,
                "mandatory": True,
            }
        ],
    }
    fake_clock = _FakeClock()
    monkeypatch.setattr("conformance.executor.time.monotonic", fake_clock.monotonic)
    monkeypatch.setattr("conformance.executor.time.sleep", fake_clock.sleep)

    manifest = parse_manifest(raw_manifest)
    with httpx.Client(transport=httpx.MockTransport(lambda _request: httpx.Response(500))) as client:
        result = run_manifest(
            manifest,
            environment="env",
            client=client,
            run_id="run-mandatory-psu-timeout",
            auth_session_store=AuthSessionStore(),
        )

    assert result.steps[0].status == "failed"
    assert result.steps[0].mandatory is True
    eligibility = result.to_json_object()["certificationEligibility"]
    assert isinstance(eligibility, dict)
    assert eligibility["eligible"] is False
    assert eligibility["mandatoryFailed"] == 1
    assert eligibility["mandatorySkipped"] == 0
    assert eligibility["reason"] == "1 mandatory step(s) failed"


@pytest.mark.unit
def test_psu_manual_step_fails_on_invalid_resolved_state_length() -> None:
    """A placeholder-resolved short state is rejected by the auth-session store."""
    context = record_step(
        ExecutionContext(),
        "state-source",
        RequestRecord(method="GET", url="https://example.com/state"),
        ResponseRecord(status_code=200, body={"state": "short"}),
    )

    result, new_context = _execute_v1_psu_step(
        _psu_manual_step(state="${steps.state-source.response.body.state}"),
        context=context,
        client=httpx.Client(transport=httpx.MockTransport(lambda _request: httpx.Response(500))),
        run_id="run-short",
        auth_session_store=AuthSessionStore(),
        execution_logger=BufferedExecutionLogger(run_id="run-short", developer_mode=False),
        clock=_FakeClock().monotonic,
        sleep=_FakeClock().sleep,
    )

    assert result.status == "failed"
    assert "at least 32 characters" in result.message
    assert new_context.steps["psu"].response is None


@pytest.mark.unit
def test_psu_manual_step_fails_on_duplicate_state() -> None:
    """A state collision in the store fails the PSU step cleanly."""
    store = AuthSessionStore()
    store.register("other-run", state="d" * 32)

    result, context = _execute_v1_psu_step(
        _psu_manual_step(state="d" * 32),
        context=ExecutionContext(),
        client=httpx.Client(transport=httpx.MockTransport(lambda _request: httpx.Response(500))),
        run_id="run-dup",
        auth_session_store=store,
        execution_logger=BufferedExecutionLogger(run_id="run-dup", developer_mode=False),
        clock=_FakeClock().monotonic,
        sleep=_FakeClock().sleep,
    )

    assert result.status == "failed"
    assert "already registered" in result.message
    assert context.steps["psu"].response is None


@pytest.mark.unit
def test_run_manifest_psu_manual_step_feeds_downstream_token_exchange(monkeypatch: pytest.MonkeyPatch) -> None:
    """A downstream form step can claim ``${steps.psu.response.body.code}``."""
    state = "m" * 32
    store = AuthSessionStore()

    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "manual psu to token",
        "steps": [
            {
                "kind": "psu-authorization",
                "id": "psu",
                "name": "PSU authorisation",
                "mode": "manual",
                "authorizationEndpoint": "https://auth.example.com/authorize",
                "clientId": "client-123",
                "redirectUri": "https://conformance.example.com/callback",
                "state": state,
            },
            {
                "id": "token",
                "name": "Token exchange",
                "request": {
                    "method": "POST",
                    "url": "https://auth.example.com/token",
                    "body": {
                        "encoding": "form",
                        "fields": {
                            "grant_type": "authorization_code",
                            "code": "${steps.psu.response.body.code}",
                        },
                    },
                },
                "assertions": [{"type": "http_status", "expected": 200}],
            },
        ],
    }
    fake_clock = _FakeClock()

    def fake_sleep(seconds: float) -> None:
        fake_clock.sleep(seconds)
        if store.get("run-manual", state) is not None:
            store.capture_code(state, "downstream-code")

    captured_token_body: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured_token_body
        captured_token_body = dict(httpx.QueryParams(request.content.decode("utf-8")))
        return httpx.Response(200, json={"access_token": "masked-by-result-layer"})

    monkeypatch.setattr("conformance.executor.time.monotonic", fake_clock.monotonic)
    monkeypatch.setattr("conformance.executor.time.sleep", fake_sleep)

    manifest = parse_manifest(raw_manifest)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = run_manifest(
            manifest,
            environment="env",
            client=client,
            run_id="run-manual",
            auth_session_store=store,
        )

    assert result.status == "passed"
    assert [step.status for step in result.steps] == ["passed", "passed"]
    assert captured_token_body["code"] == "downstream-code"


# --- PSU authorisation executor: headless mode (Phase 4) ---


@pytest.mark.unit
def test_psu_headless_step_captures_code_from_redirect() -> None:
    """Headless mode parses a 3xx Location and records code for placeholders."""
    state = "h" * 32
    store = AuthSessionStore()
    requested_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_urls.append(str(request.url))
        return httpx.Response(
            302,
            headers={"Location": f"https://conformance.example.com/callback?state={state}&code=headless-code"},
        )

    execution_logger = BufferedExecutionLogger(run_id="run-headless", developer_mode=False)
    with httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True) as client:
        result, context = _execute_v1_psu_step(
            _psu_headless_step(state=state),
            context=ExecutionContext(),
            client=client,
            run_id="run-headless",
            auth_session_store=store,
            execution_logger=execution_logger,
            clock=_FakeClock().monotonic,
            sleep=_FakeClock().sleep,
        )

    assert result.status == "passed"
    assert context.steps["psu"].response is not None
    assert context.steps["psu"].response.body["code"] == "headless-code"
    assert requested_urls == [
        "https://auth.example.com/authorize?existing=1&client_id=client-123"
        "&redirect_uri=https%3A%2F%2Fconformance.example.com%2Fcallback"
        "&response_type=code+id_token&scope=openid+accounts&state=" + state
    ]
    redirect_events = [
        event for event in execution_logger.events() if event.type == "psu-authorization-redirect-received"
    ]
    assert len(redirect_events) == 1
    assert redirect_events[0].payload == {"state": state, "status": 302}


@pytest.mark.unit
def test_psu_headless_step_records_authorization_error_redirect() -> None:
    """Headless mode converts an ASPSP error redirect into a failed step."""
    state = "e" * 32

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            303,
            headers={
                "Location": (
                    "https://conformance.example.com/callback"
                    f"?state={state}&error=access_denied&error_description=PSU+declined"
                )
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result, context = _execute_v1_psu_step(
            _psu_headless_step(state=state),
            context=ExecutionContext(),
            client=client,
            run_id="run-headless-error",
            auth_session_store=AuthSessionStore(),
            execution_logger=BufferedExecutionLogger(run_id="run-headless-error", developer_mode=False),
            clock=_FakeClock().monotonic,
            sleep=_FakeClock().sleep,
        )

    assert result.status == "failed"
    assert result.details["error"] == "access_denied"
    assert result.details["error_description"] == "PSU declined"
    assert context.steps["psu"].response is None


@pytest.mark.unit
def test_psu_headless_step_fails_when_redirect_location_missing() -> None:
    """Headless mode fails cleanly when a 3xx response omits Location."""
    with httpx.Client(transport=httpx.MockTransport(lambda _request: httpx.Response(302))) as client:
        result, context = _execute_v1_psu_step(
            _psu_headless_step(state="l" * 32),
            context=ExecutionContext(),
            client=client,
            run_id="run-headless-missing-location",
            auth_session_store=AuthSessionStore(),
            execution_logger=BufferedExecutionLogger(run_id="run-headless-missing-location", developer_mode=False),
            clock=_FakeClock().monotonic,
            sleep=_FakeClock().sleep,
        )

    assert result.status == "failed"
    assert result.status_code == 302
    assert "Location" in result.message
    assert context.steps["psu"].response is None


@pytest.mark.unit
def test_psu_headless_step_fails_on_mismatched_redirect_target() -> None:
    """Headless mode rejects redirects to any host/path other than redirectUri."""
    state = "x" * 32

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            302,
            headers={"Location": f"https://evil.example.com/callback?state={state}&code=bad-code"},
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result, context = _execute_v1_psu_step(
            _psu_headless_step(state=state),
            context=ExecutionContext(),
            client=client,
            run_id="run-headless-mismatch",
            auth_session_store=AuthSessionStore(),
            execution_logger=BufferedExecutionLogger(run_id="run-headless-mismatch", developer_mode=False),
            clock=_FakeClock().monotonic,
            sleep=_FakeClock().sleep,
        )

    rendered = json.dumps(result.to_json_object())
    assert result.status == "failed"
    assert "redirect target" in result.message
    assert "evil.example.com" not in rendered
    assert context.steps["psu"].response is None


@pytest.mark.unit
def test_psu_headless_step_accepts_redirect_with_explicit_default_https_port() -> None:
    """Headless redirect matching treats omitted HTTPS port and ``:443`` as equivalent."""
    state = "p" * 32

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            302,
            headers={"Location": f"https://conformance.example.com:443/callback?state={state}&code=headless-code"},
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result, context = _execute_v1_psu_step(
            _psu_headless_step(state=state),
            context=ExecutionContext(),
            client=client,
            run_id="run-headless-default-port",
            auth_session_store=AuthSessionStore(),
            execution_logger=BufferedExecutionLogger(run_id="run-headless-default-port", developer_mode=False),
            clock=_FakeClock().monotonic,
            sleep=_FakeClock().sleep,
        )

    assert result.status == "passed"
    assert context.steps["psu"].response is not None


@pytest.mark.unit
def test_psu_headless_step_fails_when_authorization_endpoint_returns_ok() -> None:
    """Headless mode fails on 200 OK rather than attempting consent automation."""
    with httpx.Client(transport=httpx.MockTransport(lambda _request: httpx.Response(200, json={}))) as client:
        result, context = _execute_v1_psu_step(
            _psu_headless_step(state="o" * 32),
            context=ExecutionContext(),
            client=client,
            run_id="run-headless-ok",
            auth_session_store=AuthSessionStore(),
            execution_logger=BufferedExecutionLogger(run_id="run-headless-ok", developer_mode=False),
            clock=_FakeClock().monotonic,
            sleep=_FakeClock().sleep,
        )

    assert result.status == "failed"
    assert result.status_code == 200
    assert "did not return a redirect" in result.message
    assert context.steps["psu"].response is None
