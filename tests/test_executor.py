import json
import secrets
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast
from urllib.parse import parse_qsl, urlsplit

import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from joserfc import jwk, jws, jwt

from conformance.api.auth_session_store import AuthSessionStore
from conformance.approved_releases import APPROVED_RELEASE_POLICY_SCHEMA_VERSION, ApprovedReleasePolicy
from conformance.context import ExecutionContext, RequestRecord, ResponseRecord, RuntimeConfig, record_step
from conformance.execution_log import BufferedExecutionLogger
from conformance.executor import _execute_v1_psu_step, run_manifest
from conformance.json_types import JsonObject, JsonValue
from conformance.manifest import GeneratedRequestObject, PsuAuthorizationStep, parse_manifest
from conformance.masking import MASKED_VALUE
from conformance.model_bank_config import FapiSigningConfig, TokenEndpointClientAuthMode
from conformance.signing_credentials import SigningCredentials, load_signing_credentials


def approved_policy(*approved_tool_versions: str) -> ApprovedReleasePolicy:
    """Build an approved-release policy fixture.

    Args:
        *approved_tool_versions: Tool versions to include in the policy.

    Returns:
        Approved-release policy for executor tests.
    """
    return ApprovedReleasePolicy(
        schema_version=APPROVED_RELEASE_POLICY_SCHEMA_VERSION,
        approved_tool_versions=tuple(approved_tool_versions),
    )


def _write_executor_signing_pair(certificate_root: Path, *, stem: str) -> tuple[Path, Path]:
    """Write a temporary RSA signing keypair for PSU executor tests.

    Args:
        certificate_root: Directory that will receive the generated PEM files.
        stem: File-stem prefix used for the certificate and key filenames.

    Returns:
        Tuple of ``(certificate_path, private_key_path)``.
    """
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, stem)])
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(UTC) - timedelta(minutes=1))
        .not_valid_after(datetime.now(UTC) + timedelta(days=1))
        .sign(private_key, hashes.SHA256())
    )

    certificate_path = certificate_root / f"{stem}.crt"
    private_key_path = certificate_root / f"{stem}.key"
    certificate_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    private_key_path.write_bytes(
        private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    return certificate_path, private_key_path


def _executor_signing_config(tmp_path: Path) -> FapiSigningConfig:
    """Build a valid FAPI signing config for PSU executor tests.

    Args:
        tmp_path: Pytest temporary directory used to hold generated PEM files.

    Returns:
        Parsed FAPI signing configuration pointing at the generated keypair.
    """
    certificate_root = tmp_path / "certs"
    certificate_root.mkdir()
    certificate_path, private_key_path = _write_executor_signing_pair(certificate_root, stem="executor-signing")
    return FapiSigningConfig(
        certificate_path_root=certificate_root,
        signing_certificate_path=certificate_path,
        signing_private_key_path=private_key_path,
        key_id="executor-signing-key",
        client_assertion_issuer="client-issuer",
        client_assertion_subject="client-subject",
        token_endpoint_auth_method="private_key_jwt",  # noqa: S106 - enum fixture, not a secret
    )


def _invalid_executor_signing_config(tmp_path: Path) -> FapiSigningConfig:
    """Build a signing config whose PEM files fail runtime credential loading.

    Args:
        tmp_path: Pytest temporary directory used to hold invalid PEM files.

    Returns:
        Parsed FAPI signing configuration pointing at invalid PEM content.
    """
    certificate_root = tmp_path / "invalid-certs"
    certificate_root.mkdir()
    certificate_path = certificate_root / "invalid-signing.crt"
    private_key_path = certificate_root / "invalid-signing.key"
    certificate_path.write_bytes(b"invalid certificate data")
    private_key_path.write_bytes(b"invalid private key data")
    return FapiSigningConfig(
        certificate_path_root=certificate_root,
        signing_certificate_path=certificate_path,
        signing_private_key_path=private_key_path,
        key_id="invalid-executor-signing-key",
        client_assertion_issuer="client-issuer",
        client_assertion_subject="client-subject",
        token_endpoint_auth_method="private_key_jwt",  # noqa: S106 - enum fixture, not a secret
    )


def manifest_config() -> dict[str, JsonValue]:
    """Build the legacy v0 discovery/JWKS smoke manifest fixture.

    Returns:
        Manifest dictionary accepted by the v0 parser tests.
    """
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
def test_run_manifest_v1_cross_group_placeholder_reference_fails_not_skips() -> None:
    """Cross-group placeholder references fail resolution rather than skipping.

    Execution groups run from isolated post-setup context snapshots. A step in
    one execution group therefore cannot resolve `${steps...}` placeholders
    from a sibling execution group, even when the referenced step appears
    earlier in manifest order.
    """
    requested_urls: list[str] = []
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "cross-group-placeholder",
        "steps": [
            {
                "id": "alpha-source",
                "name": "Alpha source",
                "group": "alpha",
                "request": {"method": "GET", "url": "https://example.com/alpha"},
                "assertions": [{"type": "http_status", "expected": 200}],
            },
            {
                "id": "beta-dependent",
                "name": "Beta dependent",
                "group": "beta",
                "request": {
                    "method": "GET",
                    "url": "${steps.alpha-source.response.body.next_url}",
                },
                "assertions": [{"type": "http_status", "expected": 200}],
            },
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        requested_urls.append(str(request.url))
        return httpx.Response(200, json={"next_url": "https://example.com/follow-up"})

    manifest = parse_manifest(raw_manifest)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = run_manifest(manifest, environment="test", client=client)

    assert requested_urls == ["https://example.com/alpha"]
    assert result.steps[0].status == "passed"
    assert result.steps[1].status == "failed"
    assert "Placeholder resolution failed" in result.steps[1].message
    assert "not found in execution context" in result.steps[1].message


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
def test_run_manifest_v1_schema_failure_records_masked_evidence() -> None:
    """Schema assertion failures are normal failed steps with masked evidence."""
    secret_sentinel = f"sentinel-{secrets.token_hex(16)}"
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "Schema failure evidence",
        "steps": [
            {
                "id": "accounts-list",
                "name": "Schema checked resource",
                "request": {
                    "method": "POST",
                    "url": "https://example.com/open-banking/v4.0/aisp/accounts",
                    "headers": {
                        "Authorization": f"Bearer {secret_sentinel}",
                        "X-FAPI-Financial-Id": secret_sentinel,
                    },
                    "body": {
                        "client_secret": secret_sentinel,
                        "Data": {"ConsentId": "consent-123"},
                    },
                },
                "assertions": [
                    {"type": "http_status", "expected": 200},
                    {
                        "type": "response_schema",
                        "source": "bundled_openapi",
                        "document": "ob-read-write-v4.0-account-info-openapi",
                        "schemaRef": "#/components/schemas/OBReadAccount6",
                    },
                ],
            }
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "application/json", "set-cookie": f"session={secret_sentinel}"},
            json={
                "Data": {"Account": [{}]},
                "Links": {"Self": "https://example.com/open-banking/v4.0/aisp/accounts"},
                "Meta": {"access_token": secret_sentinel},
            },
        )

    manifest = parse_manifest(raw_manifest)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = run_manifest(manifest, environment="test", client=client)

    rendered = result.to_json_object()
    rendered_json = json.dumps(rendered)
    assert result.status == "failed"
    assert result.steps[0].status == "failed"
    assert result.steps[0].message == "Schema checked resource failed"
    assert "Response body failed schema validation" in rendered_json
    assert "Data.Account[0].AccountId" in rendered_json

    steps = rendered["steps"]
    assert isinstance(steps, list)
    step = steps[0]
    assert isinstance(step, dict)
    details = step["details"]
    assert isinstance(details, dict)
    request = details["request"]
    assert isinstance(request, dict)
    request_headers = request["headers"]
    assert isinstance(request_headers, dict)
    assert request_headers["Authorization"] == MASKED_VALUE
    assert request_headers["X-FAPI-Financial-Id"] == MASKED_VALUE
    request_body = request["body"]
    assert isinstance(request_body, dict)
    assert request_body["client_secret"] == MASKED_VALUE

    response = details["response"]
    assert isinstance(response, dict)
    response_headers = response["headers"]
    assert isinstance(response_headers, dict)
    assert response_headers["set-cookie"] == MASKED_VALUE
    response_body = response["body"]
    assert isinstance(response_body, dict)
    response_meta = response_body["Meta"]
    assert isinstance(response_meta, dict)
    assert response_meta["access_token"] == MASKED_VALUE
    assert secret_sentinel not in rendered_json


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
    assert dict(result.steps[1].details) == {
        "request": {
            "method": "POST",
            "url": "https://example.com/api",
            "headers": {
                "Authorization": "***",
            },
        }
    }


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
    """Passed form-body steps retain evidence while masking sensitive values.

    Result evidence now includes request details for passed HTTP steps. For
    form payloads this means field names are retained for debugging value,
    while sensitive OAuth fields are replaced with the masking sentinel.
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
    # Scan the full step result (incl. details) for evidence retention and
    # sensitive-value masking. ``str`` covers nested
    # dataclasses/mappings without imposing a JSON-serialisable constraint
    # on the result.
    serialised = str(result.steps[0])
    assert secret_sentinel not in serialised
    assert "client_secret" in serialised
    assert MASKED_VALUE in serialised


@pytest.mark.unit
def test_run_manifest_v1_private_key_jwt_token_auth_policy_adds_client_assertion(tmp_path: Path) -> None:
    """Token-endpoint auth policy appends private-key JWT form fields."""
    captured_form_body: dict[str, str] = {}
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "private-key-jwt token auth",
        "steps": [
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
                            "code": "auth-code",
                            "redirect_uri": "https://app.example.com/callback",
                            "client_id": "client-123",
                            "scope": "accounts",
                        },
                    },
                },
                "assertions": [{"type": "http_status", "expected": 200}],
                "tokenEndpointAuthPolicy": {"source": "fapi-signing"},
            }
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured_form_body
        captured_form_body = dict(httpx.QueryParams(request.content.decode("utf-8")))
        return httpx.Response(200, json={"access_token": "access-token"})

    manifest = parse_manifest(raw_manifest)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = run_manifest(
            manifest,
            environment="test",
            client=client,
            fapi_signing_config=_executor_signing_config(tmp_path),
        )

    assert result.status == "passed"
    assert captured_form_body["grant_type"] == "authorization_code"
    assert captured_form_body["code"] == "auth-code"
    assert captured_form_body["redirect_uri"] == "https://app.example.com/callback"
    assert captured_form_body["client_id"] == "client-123"
    assert captured_form_body["scope"] == "accounts"
    assert captured_form_body["client_assertion_type"] == ("urn:ietf:params:oauth:client-assertion-type:jwt-bearer")
    assert captured_form_body["client_assertion"]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("conflicting_fields", "expected_reserved_fields"),
    [
        pytest.param(
            {"client_assertion": "manifest-client-assertion"},
            "client_assertion",
            id="client-assertion",
        ),
        pytest.param(
            {
                "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
            },
            "client_assertion_type",
            id="client-assertion-type",
        ),
        pytest.param(
            {
                "client_assertion": "manifest-client-assertion",
                "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
            },
            "client_assertion, client_assertion_type",
            id="both-fields",
        ),
    ],
)
def test_run_manifest_v1_private_key_jwt_token_auth_policy_rejects_manifest_client_assertion_fields(
    tmp_path: Path,
    conflicting_fields: dict[str, str],
    expected_reserved_fields: str,
) -> None:
    """Token-endpoint auth policy fails fast when manifests supply reserved assertion fields.

    Args:
        tmp_path: Pytest temporary directory used to hold signing credentials.
        conflicting_fields: Manifest-authored token form fields that must be
            rejected when runtime FAPI signing owns client authentication.
        expected_reserved_fields: Deterministic error-message suffix naming the
            conflicting form fields.
    """
    request_seen = False
    raw_form_fields: dict[str, JsonValue] = {
        "grant_type": "authorization_code",
        "code": "auth-code",
        "redirect_uri": "https://app.example.com/callback",
        "client_id": "client-123",
        **conflicting_fields,
    }
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "private-key-jwt token auth conflict",
        "steps": [
            {
                "id": "token",
                "name": "Token exchange",
                "request": {
                    "method": "POST",
                    "url": "https://auth.example.com/token",
                    "body": {
                        "encoding": "form",
                        "fields": raw_form_fields,
                    },
                },
                "assertions": [{"type": "http_status", "expected": 200}],
                "tokenEndpointAuthPolicy": {"source": "fapi-signing"},
            }
        ],
    }

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal request_seen
        request_seen = True
        return httpx.Response(200, json={"access_token": "access-token"})

    manifest = parse_manifest(raw_manifest)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = run_manifest(
            manifest,
            environment="test",
            client=client,
            fapi_signing_config=_executor_signing_config(tmp_path),
        )

    assert result.status == "failed"
    assert request_seen is False
    assert result.steps[0].message == (
        "Unable to apply token endpoint client authentication: "
        "Token endpoint auth policy reserves these form fields for runtime FAPI signing: "
        f"{expected_reserved_fields}"
    )
    assert dict(result.steps[0].details) == {
        "request": {
            "method": "POST",
            "url": "https://auth.example.com/token",
            "form": {
                "grant_type": "authorization_code",
                "code": "***",
                "redirect_uri": "https://app.example.com/callback",
                "client_id": "client-123",
                **{key: ("***" if key in {"client_assertion"} else value) for key, value in conflicting_fields.items()},
            },
        }
    }


@pytest.mark.unit
def test_run_manifest_v1_private_key_jwt_token_auth_masks_client_assertion(tmp_path: Path) -> None:
    """Generated client assertions are masked in step evidence and log events."""
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "private-key-jwt masking",
        "steps": [
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
                            "code": "auth-code",
                            "redirect_uri": "https://app.example.com/callback",
                            "client_id": "client-123",
                        },
                    },
                },
                "assertions": [{"type": "http_status", "expected": 200}],
                "tokenEndpointAuthPolicy": {"source": "fapi-signing"},
            }
        ],
    }
    execution_logger = BufferedExecutionLogger(run_id="token-auth-run", developer_mode=False)
    manifest = parse_manifest(raw_manifest)

    with httpx.Client(
        transport=httpx.MockTransport(lambda _request: httpx.Response(400, json={"error": "invalid_client"}))
    ) as client:
        result = run_manifest(
            manifest,
            environment="test",
            client=client,
            execution_logger=execution_logger,
            fapi_signing_config=_executor_signing_config(tmp_path),
        )

    assert result.status == "failed"
    request_details = cast("dict[str, Any]", result.steps[0].details["request"])
    assert request_details["form"] == {
        "grant_type": "authorization_code",
        "code": "***",
        "redirect_uri": "https://app.example.com/callback",
        "client_id": "client-123",
        "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
        "client_assertion": "***",
    }
    request_event = next(event for event in execution_logger.events() if event.type == "request-sent")
    assert request_event.payload["form"] == request_details["form"]


@pytest.mark.unit
def test_run_manifest_reuses_signing_credentials_across_signed_http_steps(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Executor loads runtime signing credentials once across signed HTTP steps.

    Args:
        tmp_path: Pytest temporary directory used to hold generated signing PEM files.
        monkeypatch: Fixture used to replace the credential loader with a counting wrapper.
    """
    load_count = 0
    real_loader = load_signing_credentials
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "shared signing credentials",
        "steps": [
            {
                "id": "consent",
                "name": "Consent",
                "request": {
                    "method": "POST",
                    "url": "https://resource.example.com/open-banking/v4.0/aisp/account-access-consents",
                    "detachedJws": {"source": "fapi-signing"},
                    "body": {
                        "Data": {"Permissions": ["ReadAccountsBasic", "ReadBalances"]},
                        "Risk": {},
                    },
                },
                "assertions": [{"type": "http_status", "expected": 201}],
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
                            "code": "auth-code",
                            "redirect_uri": "https://app.example.com/callback",
                            "client_id": "client-123",
                        },
                    },
                },
                "assertions": [{"type": "http_status", "expected": 200}],
                "tokenEndpointAuthPolicy": {"source": "fapi-signing"},
            },
        ],
    }

    def counting_loader(signing_config: FapiSigningConfig) -> SigningCredentials:
        """Count executor credential loads while delegating to the real loader.

        Args:
            signing_config: Validated signing configuration to load.

        Returns:
            Loaded runtime signing credentials.
        """
        nonlocal load_count
        load_count += 1
        return real_loader(signing_config)

    def handler(request: httpx.Request) -> httpx.Response:
        """Return passing responses for the signed consent and token steps.

        Args:
            request: Outbound HTTP request emitted by the executor.

        Returns:
            Passing mock response for the requested endpoint.
        """
        if str(request.url) == "https://resource.example.com/open-banking/v4.0/aisp/account-access-consents":
            return httpx.Response(201, json={"Data": {"ConsentId": "consent-123"}, "Risk": {}})
        return httpx.Response(200, json={"access_token": "access-token"})

    monkeypatch.setattr("conformance.executor.load_signing_credentials", counting_loader)
    manifest = parse_manifest(raw_manifest)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = run_manifest(
            manifest,
            environment="test",
            client=client,
            fapi_signing_config=_executor_signing_config(tmp_path),
        )

    assert result.status == "passed"
    assert [step.name for step in result.steps] == ["consent", "token"]
    assert load_count == 1


@pytest.mark.unit
def test_run_manifest_v1_unsigned_step_ignores_invalid_signing_credentials(tmp_path: Path) -> None:
    """Unsigned HTTP steps do not load invalid PEM credentials just because config exists.

    Args:
        tmp_path: Pytest temporary directory used to hold invalid signing PEM files.
    """
    request_seen = False
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "unsigned step with invalid signing config",
        "steps": [
            {
                "id": "accounts",
                "name": "Accounts list",
                "request": {
                    "method": "GET",
                    "url": "https://resource.example.com/open-banking/v4.0/aisp/accounts",
                },
                "assertions": [{"type": "http_status", "expected": 200}],
            }
        ],
    }

    def handler(_request: httpx.Request) -> httpx.Response:
        """Return a passing response for the unsigned request.

        Args:
            _request: Outbound request emitted by the executor.

        Returns:
            Passing unsigned HTTP response.
        """
        nonlocal request_seen
        request_seen = True
        return httpx.Response(200, json={"Data": []})

    manifest = parse_manifest(raw_manifest)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = run_manifest(
            manifest,
            environment="test",
            client=client,
            fapi_signing_config=_invalid_executor_signing_config(tmp_path),
        )

    assert result.status == "passed"
    assert request_seen is True


@pytest.mark.unit
def test_run_manifest_v1_detached_jws_invalid_signing_credentials_fail_the_step(tmp_path: Path) -> None:
    """Detached-JWS steps translate invalid PEM files into a failed step result.

    Args:
        tmp_path: Pytest temporary directory used to hold invalid signing PEM files.
    """
    request_seen = False
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "detached jws invalid signing credentials",
        "steps": [
            {
                "id": "consent",
                "name": "Consent",
                "request": {
                    "method": "POST",
                    "url": "https://resource.example.com/open-banking/v4.0/aisp/account-access-consents",
                    "detachedJws": {"source": "fapi-signing"},
                    "body": {
                        "Data": {"Permissions": ["ReadAccountsBasic"]},
                        "Risk": {},
                    },
                },
                "assertions": [{"type": "http_status", "expected": 201}],
            }
        ],
    }

    def handler(_request: httpx.Request) -> httpx.Response:
        """Fail the test if the signed request reaches the transport.

        Args:
            _request: Outbound request that should never be dispatched.

        Returns:
            Dummy response if executor misbehaves.
        """
        nonlocal request_seen
        request_seen = True
        return httpx.Response(201, json={"Data": {"ConsentId": "consent-123"}, "Risk": {}})

    manifest = parse_manifest(raw_manifest)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = run_manifest(
            manifest,
            environment="test",
            client=client,
            fapi_signing_config=_invalid_executor_signing_config(tmp_path),
        )

    assert result.status == "failed"
    assert request_seen is False
    assert result.steps[0].message == (
        "Unable to apply request signing: fapiSigning.signingCertificatePath must contain a valid PEM certificate"
    )


@pytest.mark.unit
def test_run_manifest_v1_private_key_jwt_invalid_signing_credentials_fail_the_step(tmp_path: Path) -> None:
    """Private-key JWT token auth reports invalid PEM files as a failed step.

    Args:
        tmp_path: Pytest temporary directory used to hold invalid signing PEM files.
    """
    request_seen = False
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "token auth invalid signing credentials",
        "steps": [
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
                            "code": "auth-code",
                            "redirect_uri": "https://app.example.com/callback",
                            "client_id": "client-123",
                        },
                    },
                },
                "assertions": [{"type": "http_status", "expected": 200}],
                "tokenEndpointAuthPolicy": {"source": "fapi-signing"},
            }
        ],
    }

    def handler(_request: httpx.Request) -> httpx.Response:
        """Fail the test if the token request reaches the transport.

        Args:
            _request: Outbound request that should never be dispatched.

        Returns:
            Dummy response if executor misbehaves.
        """
        nonlocal request_seen
        request_seen = True
        return httpx.Response(200, json={"access_token": "access-token"})

    manifest = parse_manifest(raw_manifest)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = run_manifest(
            manifest,
            environment="test",
            client=client,
            fapi_signing_config=_invalid_executor_signing_config(tmp_path),
        )

    assert result.status == "failed"
    assert request_seen is False
    assert result.steps[0].message == (
        "Unable to apply token endpoint client authentication: "
        "fapiSigning.signingCertificatePath must contain a valid PEM certificate"
    )


@pytest.mark.unit
def test_run_manifest_v1_tls_client_auth_ignores_invalid_signing_credentials_when_mtls_is_configured(
    tmp_path: Path,
) -> None:
    """TLS client auth does not load PEM credentials when mTLS is already configured.

    Args:
        tmp_path: Pytest temporary directory used to hold invalid signing PEM files.
    """
    request_seen = False
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "tls client auth invalid signing credentials",
        "steps": [
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
                            "code": "auth-code",
                            "redirect_uri": "https://app.example.com/callback",
                            "client_id": "client-123",
                        },
                    },
                },
                "assertions": [{"type": "http_status", "expected": 200}],
                "tokenEndpointAuthPolicy": {"source": "fapi-signing"},
            }
        ],
    }

    def handler(_request: httpx.Request) -> httpx.Response:
        """Return a passing token response for the mTLS-authenticated request.

        Args:
            _request: Outbound request emitted by the executor.

        Returns:
            Passing token response.
        """
        nonlocal request_seen
        request_seen = True
        return httpx.Response(200, json={"access_token": "access-token"})

    tls_client_auth_mode: TokenEndpointClientAuthMode = "tls_client_auth"
    tls_signing_config = replace(
        _invalid_executor_signing_config(tmp_path),
        token_endpoint_auth_method=tls_client_auth_mode,
    )
    manifest = parse_manifest(raw_manifest)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = run_manifest(
            manifest,
            environment="test",
            client=client,
            fapi_signing_config=tls_signing_config,
            mtls_client_configured=True,
        )

    assert result.status == "passed"
    assert request_seen is True


@pytest.mark.unit
def test_run_manifest_v1_account_access_consent_adds_masked_detached_jws_header(tmp_path: Path) -> None:
    """Consent creation signs exact JSON bytes and masks the detached JWS header."""
    observed_requests: list[httpx.Request] = []
    execution_logger = BufferedExecutionLogger(run_id="consent-signing-run", developer_mode=False)
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "account access consent signing",
        "steps": [
            {
                "id": "account-access-consent",
                "name": "Account access consent creation",
                "request": {
                    "method": "POST",
                    "url": "https://resource.example.com/open-banking/v4.0/aisp/account-access-consents",
                    "detachedJws": {"source": "fapi-signing"},
                    "headers": {"Authorization": "Bearer access-token"},
                    "body": {
                        "Data": {"Permissions": ["ReadAccountsBasic", "ReadBalances"]},
                        "Risk": {},
                    },
                },
                "assertions": [{"type": "http_status", "expected": 201}],
            }
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        """Capture the outbound consent request and force a failing assertion."""
        observed_requests.append(request)
        return httpx.Response(400, json={"error": "invalid_request"})

    manifest = parse_manifest(raw_manifest)
    signing_config = _executor_signing_config(tmp_path)
    expected_payload = b'{"Data":{"Permissions":["ReadAccountsBasic","ReadBalances"]},"Risk":{}}'
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = run_manifest(
            manifest,
            environment="test",
            client=client,
            execution_logger=execution_logger,
            fapi_signing_config=signing_config,
        )

    observed_request = observed_requests[0]
    detached_signature = observed_request.headers["x-jws-signature"]
    verified = jws.deserialize_compact(
        detached_signature,
        jwk.import_key(signing_config.signing_certificate_path.read_bytes(), key_type="RSA"),
        algorithms=["PS256"],
        payload=observed_request.content,
    )

    assert result.status == "failed"
    assert observed_request.content == expected_payload
    assert detached_signature.split(".")[1] == ""
    assert verified.headers() == {
        "alg": "PS256",
        "kid": "executor-signing-key",
        "b64": False,
        "crit": ["b64"],
    }
    request_details = cast("dict[str, Any]", result.steps[0].details["request"])
    assert request_details["body"] == {
        "Data": {"Permissions": ["ReadAccountsBasic", "ReadBalances"]},
        "Risk": {},
    }
    assert request_details["headers"] == {
        "Authorization": "***",
        "x-jws-signature": "***",
    }
    request_event = next(event for event in execution_logger.events() if event.type == "request-sent")
    assert request_event.payload["headers"] == request_details["headers"]


@pytest.mark.unit
def test_run_manifest_v1_account_access_consent_adds_detached_jws_with_doubled_path_separator(
    tmp_path: Path,
) -> None:
    """Consent creation still signs when the resolved URL path contains doubled slashes."""
    observed_requests: list[httpx.Request] = []
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "account access consent signing with doubled slash",
        "steps": [
            {
                "id": "account-access-consent",
                "name": "Account access consent creation",
                "request": {
                    "method": "POST",
                    "url": "https://resource.example.com//open-banking/v4.0/aisp/account-access-consents/",
                    "detachedJws": {"source": "fapi-signing"},
                    "body": {
                        "Data": {"Permissions": ["ReadAccountsBasic", "ReadBalances"]},
                        "Risk": {},
                    },
                },
                "assertions": [{"type": "http_status", "expected": 201}],
            }
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        """Capture the outbound consent request for doubled-slash URL coverage.

        Args:
            request: Outbound HTTP request emitted by the executor.

        Returns:
            Passing consent response.
        """
        observed_requests.append(request)
        return httpx.Response(201, json={"Data": {"ConsentId": "consent-123"}, "Risk": {}})

    manifest = parse_manifest(raw_manifest)
    expected_payload = b'{"Data":{"Permissions":["ReadAccountsBasic","ReadBalances"]},"Risk":{}}'
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = run_manifest(
            manifest,
            environment="test",
            client=client,
            fapi_signing_config=_executor_signing_config(tmp_path),
        )

    assert result.status == "passed"
    assert observed_requests[0].content == expected_payload
    assert "x-jws-signature" in observed_requests[0].headers


@pytest.mark.unit
def test_run_manifest_v1_account_access_consent_skips_detached_jws_without_manifest_opt_in(tmp_path: Path) -> None:
    """Consent creation stays unsigned unless the manifest request opts into detached JWS."""
    observed_requests: list[httpx.Request] = []
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "account access consent without detached-jws",
        "steps": [
            {
                "id": "account-access-consent",
                "name": "Account access consent creation",
                "request": {
                    "method": "POST",
                    "url": "https://resource.example.com/open-banking/v4.0/aisp/account-access-consents",
                    "headers": {"Authorization": "Bearer access-token"},
                    "body": {
                        "Data": {"Permissions": ["ReadAccountsBasic", "ReadBalances"]},
                        "Risk": {},
                    },
                },
                "assertions": [{"type": "http_status", "expected": 201}],
            }
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        """Capture the outbound consent request for detached-JWS absence checks.

        Args:
            request: Outbound HTTP request emitted by the executor.

        Returns:
            Passing consent response.
        """
        observed_requests.append(request)
        return httpx.Response(201, json={"Data": {"ConsentId": "consent-123"}, "Risk": {}})

    manifest = parse_manifest(raw_manifest)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = run_manifest(
            manifest,
            environment="test",
            client=client,
            fapi_signing_config=_executor_signing_config(tmp_path),
        )

    assert result.status == "passed"
    assert "x-jws-signature" not in observed_requests[0].headers


@pytest.mark.unit
def test_run_manifest_v1_detached_jws_policy_requires_signing_config() -> None:
    """Explicit detached-JWS opt-in fails before dispatch when signing config is absent."""
    request_seen = False
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "account access consent missing signing config",
        "steps": [
            {
                "id": "account-access-consent",
                "name": "Account access consent creation",
                "request": {
                    "method": "POST",
                    "url": "https://resource.example.com/open-banking/v4.0/aisp/account-access-consents",
                    "detachedJws": {"source": "fapi-signing"},
                    "body": {
                        "Data": {"Permissions": ["ReadAccountsBasic", "ReadBalances"]},
                        "Risk": {},
                    },
                },
                "assertions": [{"type": "http_status", "expected": 201}],
            }
        ],
    }

    def handler(_request: httpx.Request) -> httpx.Response:
        """Fail the test if an unsigned request reaches the transport.

        Args:
            _request: Outbound request that should never be sent.

        Returns:
            Dummy response if the executor misbehaves.
        """
        nonlocal request_seen
        request_seen = True
        return httpx.Response(201, json={"Data": {"ConsentId": "consent-123"}, "Risk": {}})

    manifest = parse_manifest(raw_manifest)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = run_manifest(manifest, environment="test", client=client)

    assert result.status == "failed"
    assert request_seen is False
    assert result.steps[0].message == (
        "Unable to apply request signing: Detached request signing requires fapiSigning configuration"
    )


@pytest.mark.unit
def test_run_manifest_v1_detached_jws_policy_rejects_unsupported_url(tmp_path: Path) -> None:
    """Explicit detached-JWS opt-in fails before dispatch on unsupported endpoints."""
    request_seen = False
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "accounts list with detached-jws",
        "steps": [
            {
                "id": "accounts-list",
                "name": "Accounts list",
                "request": {
                    "method": "POST",
                    "url": "https://resource.example.com/open-banking/v4.0/aisp/accounts",
                    "detachedJws": {"source": "fapi-signing"},
                    "body": {"Data": {"Example": True}},
                },
                "assertions": [{"type": "http_status", "expected": 200}],
            }
        ],
    }

    def handler(_request: httpx.Request) -> httpx.Response:
        """Fail the test if an unsupported signed request reaches transport.

        Args:
            _request: Outbound request that should never be sent.

        Returns:
            Dummy response if the executor misbehaves.
        """
        nonlocal request_seen
        request_seen = True
        return httpx.Response(200, json={"Data": {}})

    manifest = parse_manifest(raw_manifest)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = run_manifest(
            manifest,
            environment="test",
            client=client,
            fapi_signing_config=_executor_signing_config(tmp_path),
        )

    assert result.status == "failed"
    assert request_seen is False
    assert result.steps[0].message == (
        "Unable to apply request signing: "
        "Detached request signing is only supported for account-access-consents requests"
    )


@pytest.mark.unit
def test_run_manifest_v1_tls_client_auth_policy_requires_mtls_client(tmp_path: Path) -> None:
    """TLS client auth fails before dispatch when no mTLS client is configured."""
    request_seen = False
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "tls-client-auth missing mtls",
        "steps": [
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
                            "code": "auth-code",
                            "redirect_uri": "https://app.example.com/callback",
                            "client_id": "client-123",
                        },
                    },
                },
                "assertions": [{"type": "http_status", "expected": 200}],
                "tokenEndpointAuthPolicy": {"source": "fapi-signing"},
            }
        ],
    }

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal request_seen
        request_seen = True
        return httpx.Response(200, json={"access_token": "access-token"})

    tls_client_auth_mode: TokenEndpointClientAuthMode = "tls_client_auth"
    tls_signing_config = replace(_executor_signing_config(tmp_path), token_endpoint_auth_method=tls_client_auth_mode)
    manifest = parse_manifest(raw_manifest)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = run_manifest(
            manifest,
            environment="test",
            client=client,
            fapi_signing_config=tls_signing_config,
        )

    assert result.status == "failed"
    assert request_seen is False
    assert result.steps[0].message == (
        "Unable to apply token endpoint client authentication: "
        "Token endpoint auth policy requires a configured TLS client certificate and private key"
    )


@pytest.mark.unit
def test_run_manifest_v1_tls_client_auth_policy_dispatches_with_configured_mtls(tmp_path: Path) -> None:
    """TLS client auth preserves the existing token form body when mTLS is present."""
    captured_form_body: dict[str, str] = {}
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "tls-client-auth configured",
        "steps": [
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
                            "code": "auth-code",
                            "redirect_uri": "https://app.example.com/callback",
                            "client_id": "client-123",
                        },
                    },
                },
                "assertions": [{"type": "http_status", "expected": 200}],
                "tokenEndpointAuthPolicy": {"source": "fapi-signing"},
            }
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured_form_body
        captured_form_body = dict(httpx.QueryParams(request.content.decode("utf-8")))
        return httpx.Response(200, json={"access_token": "access-token"})

    tls_client_auth_mode: TokenEndpointClientAuthMode = "tls_client_auth"
    tls_signing_config = replace(_executor_signing_config(tmp_path), token_endpoint_auth_method=tls_client_auth_mode)
    manifest = parse_manifest(raw_manifest)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = run_manifest(
            manifest,
            environment="test",
            client=client,
            fapi_signing_config=tls_signing_config,
            mtls_client_configured=True,
        )

    assert result.status == "passed"
    assert captured_form_body == {
        "grant_type": "authorization_code",
        "code": "auth-code",
        "redirect_uri": "https://app.example.com/callback",
        "client_id": "client-123",
    }


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
def test_run_manifest_v1_passed_step_includes_masked_request_and_response_evidence() -> None:
    """PASS step carries masked request/response evidence when available."""
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

    step = result.steps[0]
    assert step.status == "passed"
    details = dict(step.details)
    assert details["request"] == {"method": "GET", "url": "https://example.com/ok"}
    response = cast("dict[str, Any]", details["response"])
    assert response["statusCode"] == 200
    assert response["body"] == {"access_token": "***"}
    assert response["headers"]["content-type"] == "application/json"


@pytest.mark.unit
def test_run_manifest_v1_applies_header_and_json_assertions_with_pass_evidence() -> None:
    """Executor passes response headers into assertion evaluation for PASS steps."""
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "header-pass",
        "steps": [
            {
                "id": "discovery",
                "name": "Discovery",
                "request": {"method": "GET", "url": "https://example.com/discovery"},
                "assertions": [
                    {"type": "http_status", "expected": 200},
                    {"type": "header", "name": "x-fapi-interaction-id", "rule": "present"},
                    {"type": "header", "name": "content-type", "rule": "contains", "value": "application/json"},
                    {"type": "json_field", "path": "issuer", "rule": "equals", "value": "https://example.com"},
                ],
            }
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "X-FAPI-Interaction-Id": "trace-123",
            },
            json={"issuer": "https://example.com"},
        )

    manifest = parse_manifest(raw_manifest)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = run_manifest(manifest, environment="test", client=client)

    step = result.steps[0]
    assert step.status == "passed"
    details = dict(step.details)
    assert details["assertions"] == [
        {"status": "passed", "message": "HTTP status was 200"},
        {"status": "passed", "message": "Header x-fapi-interaction-id is present"},
        {"status": "passed", "message": "Header content-type contains the expected value"},
        {"status": "passed", "message": "JSON field issuer equals https://example.com"},
    ]
    assert details["request"] == {"method": "GET", "url": "https://example.com/discovery"}
    response = cast("dict[str, Any]", details["response"])
    assert response["statusCode"] == 200
    assert response["body"] == {"issuer": "https://example.com"}
    assert response["headers"]["x-fapi-interaction-id"] == "trace-123"


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
    response = cast("dict[str, Any]", details["response"])
    assert response["statusCode"] == 400
    assert response["body"] == {"error": "invalid_client", "access_token": "***"}
    assert response["headers"]["content-type"] == "application/json"


@pytest.mark.unit
def test_run_manifest_v1_masks_sensitive_request_and_response_evidence_by_default() -> None:
    """Default mode masks headers, tokens, codes, and client secrets in result evidence."""
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "evidence-sensitive-default",
        "steps": [
            {
                "id": "token",
                "name": "Token",
                "request": {
                    "method": "POST",
                    "url": "https://example.com/token",
                    "headers": {
                        "Authorization": "Bearer very-secret-token",
                        "Accept": "application/json",
                    },
                    "body": {
                        "encoding": "form",
                        "fields": {
                            "grant_type": "authorization_code",
                            "code": "super-secret-code",  # pragma: allowlist secret
                            "client_secret": "super-secret-client",  # pragma: allowlist secret
                        },
                    },
                },
                "assertions": [{"type": "http_status", "expected": 200}],
            }
        ],
    }

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"Set-Cookie": "session=very-secret-cookie"},
            json={"access_token": "very-secret-access", "code": "very-secret-code"},  # pragma: allowlist secret
        )

    manifest = parse_manifest(raw_manifest)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = run_manifest(manifest, environment="test", client=client)

    step = result.steps[0]
    assert step.status == "passed"
    details = dict(step.details)
    request = cast("dict[str, Any]", details["request"])
    assert request["headers"]["Authorization"] == MASKED_VALUE
    assert request["form"]["code"] == MASKED_VALUE
    assert request["form"]["client_secret"] == MASKED_VALUE

    response = cast("dict[str, Any]", details["response"])
    assert response["headers"]["set-cookie"] == MASKED_VALUE
    assert response["body"]["access_token"] == MASKED_VALUE
    assert response["body"]["code"] == MASKED_VALUE


@pytest.mark.unit
def test_run_manifest_v1_developer_mode_keeps_sensitive_result_evidence_unmasked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Developer mode intentionally bypasses masking for result evidence payloads."""
    monkeypatch.setenv("CONFORMANCE_DEVELOPER_MODE", "true")
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "evidence-sensitive-developer",
        "steps": [
            {
                "id": "token",
                "name": "Token",
                "request": {
                    "method": "POST",
                    "url": "https://example.com/token",
                    "headers": {
                        "Authorization": "Bearer very-secret-token",
                        "Accept": "application/json",
                    },
                    "body": {
                        "encoding": "form",
                        "fields": {
                            "grant_type": "authorization_code",
                            "code": "raw-auth-code",  # pragma: allowlist secret
                            "client_secret": "raw-client-secret",  # pragma: allowlist secret
                        },
                    },
                },
                "assertions": [{"type": "http_status", "expected": 200}],
            }
        ],
    }

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"Set-Cookie": "session=raw-cookie"},
            json={"access_token": "raw-access-token", "code": "raw-code"},  # pragma: allowlist secret
        )

    manifest = parse_manifest(raw_manifest)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = run_manifest(manifest, environment="test", client=client)

    step = result.steps[0]
    assert step.status == "passed"
    details = dict(step.details)
    request = cast("dict[str, Any]", details["request"])
    assert request["headers"]["Authorization"] == "Bearer very-secret-token"  # noqa: S105
    assert request["form"]["code"] == "raw-auth-code"  # noqa: S105  # pragma: allowlist secret
    assert request["form"]["client_secret"] == "raw-client-secret"  # noqa: S105  # pragma: allowlist secret

    response = cast("dict[str, Any]", details["response"])
    assert response["headers"]["set-cookie"] == "session=raw-cookie"  # noqa: S105
    assert response["body"]["access_token"] == "raw-access-token"  # noqa: S105
    assert response["body"]["code"] == "raw-code"  # noqa: S105


@pytest.mark.unit
def test_run_manifest_v1_failed_header_assertion_includes_masked_response_headers() -> None:
    """FAIL step includes masked response headers in evidence and assertion details."""
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "header-fail",
        "steps": [
            {
                "id": "token",
                "name": "Token",
                "request": {"method": "GET", "url": "https://example.com/token"},
                "assertions": [
                    {"type": "header", "name": "cache-control", "rule": "present"},
                    {"type": "header", "name": "set-cookie", "rule": "absent"},
                ],
            }
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={
                "Cache-Control": "no-store",
                "Set-Cookie": "session=super-secret",
                "X-Trace-Id": "trace-123",
            },
            json={"ok": True},
        )

    manifest = parse_manifest(raw_manifest)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = run_manifest(manifest, environment="test", client=client)

    step = result.steps[0]
    assert step.status == "failed"
    details = dict(step.details)
    assert details["assertions"] == [
        {"status": "passed", "message": "Header cache-control is present"},
        {"status": "failed", "message": "Header set-cookie must be absent"},
    ]
    assert details["request"] == {"method": "GET", "url": "https://example.com/token"}
    response = cast("dict[str, Any]", details["response"])
    assert response["statusCode"] == 200
    assert response["body"] == {"ok": True}
    assert response["headers"]["cache-control"] == "no-store"
    assert response["headers"]["set-cookie"] == "***"
    assert response["headers"]["x-trace-id"] == "trace-123"


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
    response = cast("dict[str, Any]", details["response"])
    assert response["statusCode"] == 200
    assert response["body"] == {"ok": True, "access_token": "***"}
    assert response["headers"]["content-type"] == "application/json"


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
def test_run_manifest_v1_threads_mandatory_into_step_result_and_eligibility(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Executor carries ``mandatory`` from manifest into StepResult and eligibility block."""
    monkeypatch.setattr("conformance.results.resolve_conformance_tool_version", lambda: "1.2.3")
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "mandatory-mix",
        "certificationCoverage": "complete",
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
        result = run_manifest(
            manifest,
            environment="test",
            client=client,
            approved_release_policy=approved_policy("1.2.3"),
        )

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


@pytest.mark.integration
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
            environment="test",
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
        fapi_signing_config: FapiSigningConfig | None = None,
        fapi_signing_service: Any = None,
        mtls_client_configured: bool = False,
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
            fapi_signing_config=fapi_signing_config,
            fapi_signing_service=fapi_signing_service,
            mtls_client_configured=mtls_client_configured,
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
        fapi_signing_config: FapiSigningConfig | None = None,
        fapi_signing_service: Any = None,
        mtls_client_configured: bool = False,
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
            fapi_signing_config=fapi_signing_config,
            fapi_signing_service=fapi_signing_service,
            mtls_client_configured=mtls_client_configured,
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
def test_psu_manual_step_prefers_runtime_authorization_endpoint_override() -> None:
    """Manual mode can target a config-pinned auth endpoint instead of discovery."""
    store = AuthSessionStore()
    step = _psu_manual_step(authorization_endpoint="https://discovered.example.com/branded-auth")

    def capture_once() -> None:
        if store.get("run-override", "s" * 32) is not None:
            store.capture_code("s" * 32, "auth-code-123")

    fake_clock = _FakeClock(on_sleep=capture_once)
    execution_logger = BufferedExecutionLogger(run_id="run-override", developer_mode=False)

    result, context = _execute_v1_psu_step(
        step,
        context=ExecutionContext(
            config=RuntimeConfig(
                discovery_url="https://auth.example.com/.well-known/openid-configuration",
                environment="sandbox",
                oauth_authorization_endpoint="https://auth.example.com/auth",
            )
        ),
        client=httpx.Client(transport=httpx.MockTransport(lambda _request: httpx.Response(500))),
        run_id="run-override",
        auth_session_store=store,
        execution_logger=execution_logger,
        clock=fake_clock.monotonic,
        sleep=fake_clock.sleep,
    )

    assert result.status == "passed"
    assert result.url is not None
    assert result.url.startswith("https://auth.example.com/auth?")
    assert context.steps["psu"].request.url.startswith("https://auth.example.com/auth?")


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
def test_psu_manual_timeout_developer_mode_keeps_authorization_url_query_unmasked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Developer mode keeps credential-bearing PSU URL query values in result evidence."""
    monkeypatch.setenv("CONFORMANCE_DEVELOPER_MODE", "true")
    fake_clock = _FakeClock()
    result, context = _execute_v1_psu_step(
        _psu_manual_step(
            authorization_endpoint="https://auth.example.com/authorize?client_assertion=inline.assertion",
            request_object="signed.request.jwt",
            timeout_seconds=1,
        ),
        context=ExecutionContext(),
        client=httpx.Client(transport=httpx.MockTransport(lambda _request: httpx.Response(500))),
        run_id="run-timeout-unmasked-url",
        auth_session_store=AuthSessionStore(),
        execution_logger=BufferedExecutionLogger(run_id="run-timeout-unmasked-url", developer_mode=False),
        clock=fake_clock.monotonic,
        sleep=fake_clock.sleep,
    )

    request_details = cast("dict[str, Any]", result.details["request"])
    request_url = cast("str", request_details["url"])
    assert result.status == "failed"
    assert result.url == request_url
    assert "client_assertion=inline.assertion" in request_url
    assert "request=signed.request.jwt" in request_url
    assert context.steps["psu"].request.url == request_url


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
def test_psu_manual_step_generates_and_masks_signed_request_object(tmp_path: Path) -> None:
    """Manual PSU mode signs a generated request object and masks persisted artifacts.

    Args:
        tmp_path: Pytest temporary directory used to hold generated signing PEM files.
    """
    store = AuthSessionStore()
    step = _psu_manual_step(request_object=GeneratedRequestObject(source="fapi-signing"))

    def capture_once() -> None:
        if store.get("run-psu-signed-manual", "s" * 32) is not None:
            store.capture_code("s" * 32, "auth-code-123")

    fake_clock = _FakeClock(on_sleep=capture_once)
    execution_logger = BufferedExecutionLogger(run_id="run-psu-signed-manual", developer_mode=False)

    result, context = _execute_v1_psu_step(
        step,
        context=ExecutionContext(),
        client=httpx.Client(transport=httpx.MockTransport(lambda _request: httpx.Response(500))),
        run_id="run-psu-signed-manual",
        auth_session_store=store,
        execution_logger=execution_logger,
        clock=fake_clock.monotonic,
        sleep=fake_clock.sleep,
        fapi_signing_config=_executor_signing_config(tmp_path),
    )

    assert result.status == "passed"
    assert result.url is not None
    assert "request=***" in result.url
    assert context.steps["psu"].request.url == result.url

    psu_url_events = [event for event in execution_logger.events() if event.type == "psu-authorization-url"]
    assert len(psu_url_events) == 1
    event_payload = psu_url_events[0].payload
    assert event_payload["request_object"] == "***"
    raw_url = cast(str, event_payload["url"])
    assert "request=***" in raw_url
    assert "response_type=code+id_token" in raw_url


@pytest.mark.unit
def test_psu_manual_step_invalid_signing_credentials_fail_the_step(tmp_path: Path) -> None:
    """Generated PSU request objects translate invalid PEM files into a failed step.

    Args:
        tmp_path: Pytest temporary directory used to hold invalid signing PEM files.
    """
    store = AuthSessionStore()
    step = _psu_manual_step(request_object=GeneratedRequestObject(source="fapi-signing"))

    result, context = _execute_v1_psu_step(
        step,
        context=ExecutionContext(),
        client=httpx.Client(transport=httpx.MockTransport(lambda _request: httpx.Response(500))),
        run_id="run-psu-invalid-signing-manual",
        auth_session_store=store,
        execution_logger=BufferedExecutionLogger(run_id="run-psu-invalid-signing-manual", developer_mode=False),
        fapi_signing_config=_invalid_executor_signing_config(tmp_path),
    )

    assert result.status == "failed"
    assert result.message == (
        "Unable to build PSU request object: fapiSigning.signingCertificatePath must contain a valid PEM certificate"
    )
    assert context.steps["psu"].response is None

    rendered = json.dumps(result.to_json_object())
    assert "request=ey" not in rendered


@pytest.mark.unit
def test_psu_headless_step_uses_signed_request_object_for_authorization_redirect(tmp_path: Path) -> None:
    """Headless PSU mode sends a generated JAR request and masks persisted evidence.

    Args:
        tmp_path: Pytest temporary directory used to hold generated signing PEM files.
    """
    state = "h" * 32
    observed_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        observed_urls.append(str(request.url))
        return httpx.Response(
            302,
            headers={"Location": f"https://conformance.example.com/callback?state={state}&code=headless-code"},
        )

    execution_logger = BufferedExecutionLogger(run_id="run-psu-signed-headless", developer_mode=False)
    with httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True) as client:
        result, context = _execute_v1_psu_step(
            _psu_headless_step(state=state, request_object=GeneratedRequestObject(source="fapi-signing")),
            context=ExecutionContext(),
            client=client,
            run_id="run-psu-signed-headless",
            auth_session_store=AuthSessionStore(),
            execution_logger=execution_logger,
            clock=_FakeClock().monotonic,
            sleep=_FakeClock().sleep,
            fapi_signing_config=_executor_signing_config(tmp_path),
        )

    assert result.status == "passed"
    assert result.url is not None
    assert "request=***" in result.url
    assert context.steps["psu"].response is not None
    assert context.steps["psu"].response.body["code"] == "headless-code"
    assert len(observed_urls) == 1
    assert "request=ey" in observed_urls[0]
    assert "request=***" not in observed_urls[0]

    psu_url_events = [event for event in execution_logger.events() if event.type == "psu-authorization-url"]
    assert len(psu_url_events) == 1
    assert psu_url_events[0].payload["request_object"] == "***"
    assert "request=***" in cast(str, psu_url_events[0].payload["url"])


@pytest.mark.unit
def test_psu_headless_step_resolves_openbanking_intent_id_into_generated_request_object(tmp_path: Path) -> None:
    """Generated PSU request objects embed a resolved Open Banking consent id.

    Args:
        tmp_path: Pytest temporary directory used to hold generated signing PEM files.
    """
    state = "h" * 32
    observed_urls: list[str] = []
    signing_config = _executor_signing_config(tmp_path)
    context = record_step(
        ExecutionContext(),
        "account-access-consent",
        RequestRecord(method="POST", url="https://rs.example.com/account-access-consents"),
        ResponseRecord(status_code=201, body={"Data": {"ConsentId": "consent-456"}}),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        """Capture the outbound PSU authorisation URL for later JWT inspection.

        Args:
            request: Browser-like authorisation redirect emitted by the executor.

        Returns:
            Redirect response completing the headless PSU flow.
        """
        observed_urls.append(str(request.url))
        return httpx.Response(
            302,
            headers={"Location": f"https://conformance.example.com/callback?state={state}&code=headless-code"},
        )

    with httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True) as client:
        result, _ = _execute_v1_psu_step(
            _psu_headless_step(
                state=state,
                request_object=GeneratedRequestObject(
                    source="fapi-signing",
                    openbanking_intent_id="${steps.account-access-consent.response.body.Data.ConsentId}",
                ),
            ),
            context=context,
            client=client,
            run_id="run-psu-signed-intent-headless",
            auth_session_store=AuthSessionStore(),
            execution_logger=BufferedExecutionLogger(run_id="run-psu-signed-intent-headless", developer_mode=False),
            clock=_FakeClock().monotonic,
            sleep=_FakeClock().sleep,
            fapi_signing_config=signing_config,
        )

    request_params = dict(parse_qsl(urlsplit(observed_urls[0]).query))
    public_key = jwk.import_key(signing_config.signing_certificate_path.read_bytes(), key_type="RSA")
    decoded_request_object = jwt.decode(request_params["request"], public_key, algorithms=["PS256"])
    claims = decoded_request_object.claims

    assert result.status == "passed"
    assert len(observed_urls) == 1
    assert isinstance(claims, dict)
    assert claims["claims"] == {
        "id_token": {
            "openbanking_intent_id": {
                "essential": True,
                "value": "consent-456",
            }
        }
    }


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
    assert len(requested_urls) == 1
    requested_url = urlsplit(requested_urls[0])
    assert requested_url.scheme == "https"
    assert requested_url.netloc == "auth.example.com"
    assert requested_url.path == "/authorize"
    query = dict(parse_qsl(requested_url.query, keep_blank_values=True))
    assert query["existing"] == "1"
    assert query["client_id"] == "client-123"
    assert query["redirect_uri"] == "https://conformance.example.com/callback"
    assert query["response_type"] == "code id_token"
    assert query["scope"] == "openid accounts"
    assert query["state"] == state
    assert len(query["nonce"]) >= 32
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
