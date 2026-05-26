from typing import cast

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
    assert result.steps[1].message == "Follow-up URL from response.body.jwks_uri must be an HTTPS URL"


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
