"""Executor validation of Open Banking detached response signatures."""

import httpx
import pytest

from conformance.context import RuntimeConfig
from conformance.executor import run_manifest
from conformance.manifest import (
    Manifest,
    ManifestRequest,
    ManifestStep,
    ResponseSignaturePolicy,
)
from tests.support.executor_signing import signed_response_header

pytestmark = pytest.mark.unit


def test_run_manifest_v1_validates_required_response_signature() -> None:
    """A step with response-signature policy validates against discovery JWKS."""
    payload = b'{"Data":{"Status":"ACSP"}}'
    signature, jwks_document = signed_response_header(payload)
    requested_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        """Serve the protected resource, discovery document, and JWKS.

        Args:
            request: Incoming mock HTTP request.

        Returns:
            Mock response for the requested URL.
        """
        requested_urls.append(str(request.url))
        if str(request.url) == "https://rs.example.com/payment":
            return httpx.Response(
                201,
                content=payload,
                headers={"Content-Type": "application/json", "x-jws-signature": signature},
            )
        if str(request.url) == "https://auth.example.com/.well-known/openid-configuration":
            return httpx.Response(
                200,
                json={"issuer": "https://auth.example.com", "jwks_uri": "https://auth.example.com/jwks"},
            )
        if str(request.url) == "https://auth.example.com/jwks":
            return httpx.Response(200, json=jwks_document)
        return httpx.Response(404, json={"error": "not found"})

    manifest = Manifest(
        schema_version="v1",
        name="response signature",
        certification_coverage="complete",
        steps=(
            ManifestStep(
                id="signed-response",
                name="Signed response",
                request=ManifestRequest(method="POST", url="https://rs.example.com/payment"),
                assertions=(),
                response_signature_policy=ResponseSignaturePolicy(source="discovery-jwks"),
            ),
        ),
    )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = run_manifest(
            manifest,
            client=client,
            runtime_config=RuntimeConfig(discovery_url="https://auth.example.com/.well-known/openid-configuration"),
        )

    assert result.status == "passed"
    assert requested_urls == [
        "https://rs.example.com/payment",
        "https://auth.example.com/.well-known/openid-configuration",
        "https://auth.example.com/jwks",
    ]
    response_evidence = result.steps[0].details["response"]
    assert isinstance(response_evidence, dict)
    assert response_evidence["responseSignature"] == {
        "status": "passed",
        "kid": "response-key",
        "issuer": "0015800001041RHAAY",
        "trustAnchor": "openbanking.org.uk",
    }


def test_run_manifest_v1_fails_when_required_response_signature_missing() -> None:
    """A step with response-signature policy fails when the response is unsigned."""

    def handler(request: httpx.Request) -> httpx.Response:
        """Serve an unsigned protected-resource response.

        Args:
            request: Incoming mock HTTP request.

        Returns:
            Mock JSON response for the requested URL.
        """
        if str(request.url) == "https://rs.example.com/payment":
            return httpx.Response(201, json={"Data": {"Status": "ACSP"}})
        return httpx.Response(404, json={"error": "not found"})

    manifest = Manifest(
        schema_version="v1",
        name="missing response signature",
        certification_coverage="complete",
        steps=(
            ManifestStep(
                id="signed-response",
                name="Signed response",
                request=ManifestRequest(method="POST", url="https://rs.example.com/payment"),
                assertions=(),
                response_signature_policy=ResponseSignaturePolicy(source="discovery-jwks"),
            ),
        ),
    )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = run_manifest(
            manifest,
            client=client,
            runtime_config=RuntimeConfig(discovery_url="https://auth.example.com/.well-known/openid-configuration"),
        )

    assert result.status == "failed"
    assert result.steps[0].message == "Response signature validation failed: x-jws-signature header is missing"
