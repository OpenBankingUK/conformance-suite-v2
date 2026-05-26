from typing import Any, cast

import httpx
import pytest

from conformance.executor import run_manifest
from conformance.json_types import JsonValue
from conformance.manifest import parse_manifest


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
    assert result.to_json_object()["summary"] == {"total": 2, "passed": 2, "failed": 0}


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
    assert "Path segment 'jwks_uri' not found" in result.steps[1].message


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
        "assertions": [{"status": "failed", "message": "Expected HTTP status 200, got 404"}]
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
    """Defence-in-depth: fail the step if request method is not GET."""
    from conformance.manifest import HttpStatusAssertion, Manifest, ManifestRequest, ManifestTest

    bad_manifest = Manifest(
        schema_version="v0",
        name="bad-method",
        tests=(
            ManifestTest(
                id="post-test",
                name="POST test",
                request=ManifestRequest(method=cast(Any, "POST"), url="https://example.com/api"),
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
    assert result.steps[0].message == "Unsupported request method: POST"


@pytest.mark.unit
def test_run_manifest_rejects_unsupported_follow_up_method() -> None:
    """Defence-in-depth: fail the follow-up step if its method is not GET."""
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
                    request=FollowUpRequest(method=cast(Any, "POST")),
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
    assert result.steps[1].message == "Unsupported request method: POST"


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
    assert result.to_json_object()["summary"] == {"total": 2, "passed": 2, "failed": 0}


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
def test_run_manifest_v1_failed_request_prevents_later_resolution() -> None:
    """A step with no response (transport error) fails later steps that reference it."""
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
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    manifest = parse_manifest(raw_manifest)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = run_manifest(manifest, environment="test", client=client)

    assert result.status == "failed"
    assert len(result.steps) == 2
    assert result.steps[0].status == "failed"
    assert result.steps[1].status == "failed"
    assert "has no response" in result.steps[1].message


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
