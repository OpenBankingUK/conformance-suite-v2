"""Executor step outcomes and the masked request/response evidence they record."""

import json
import secrets
from typing import Any, cast

import httpx
import pytest

from conformance.execution_log import BufferedExecutionLogger
from conformance.executor import run_manifest
from conformance.json_types import JsonValue
from conformance.manifest import (
    parse_manifest,
)
from conformance.masking import MASKED_VALUE

pytestmark = pytest.mark.unit


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
        result = run_manifest(manifest, client=client)

    # Aggregate stays "passed" — WARN does not block certification (PRD).
    assert result.status == "passed"
    step = result.steps[0]
    assert step.status == "warn"
    assert "deprecated" in step.message
    assert step.details["warning"] == "Field 'foo' is deprecated and will be removed in v4.1"
    summary = result.to_json_object()["summary"]
    assert summary == {"total": 1, "passed": 0, "failed": 0, "warn": 1, "skipped": 0}


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
        result = run_manifest(manifest, client=client)

    assert result.status == "failed"
    assert result.steps[0].status == "failed"
    assert "warning" not in result.steps[0].details


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
        result = run_manifest(manifest, client=client)

    assert result.status == "passed"
    assert [step.status for step in result.steps] == ["passed", "warn"]
    assert result.to_json_object()["summary"] == {
        "total": 2,
        "passed": 1,
        "failed": 0,
        "warn": 1,
        "skipped": 0,
    }


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
        result = run_manifest(manifest, client=client)

    step = result.steps[0]
    assert step.status == "passed"
    details = dict(step.details)
    assert details["request"] == {"method": "GET", "url": "https://example.com/ok"}
    response = cast("dict[str, Any]", details["response"])
    assert response["statusCode"] == 200
    assert response["body"] == {"access_token": "***"}
    assert response["headers"]["content-type"] == "application/json"


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
        result = run_manifest(manifest, client=client)

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
        result = run_manifest(manifest, client=client)

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
        result = run_manifest(manifest, client=client)

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
        result = run_manifest(manifest, client=client)

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
        result = run_manifest(manifest, client=client)

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
        result = run_manifest(manifest, client=client)

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
        result = run_manifest(manifest, client=client)

    step = result.steps[0]
    assert step.status == "warn"
    details = dict(step.details)
    assert "request" in details
    response = cast("dict[str, Any]", details["response"])
    assert response["statusCode"] == 200
    assert response["body"] == {"ok": True, "access_token": "***"}
    assert response["headers"]["content-type"] == "application/json"


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
        result = run_manifest(manifest, client=client)

    skipped = result.steps[1]
    assert skipped.status == "skipped"
    details = dict(skipped.details)
    request = cast("dict[str, Any]", details["request"])
    assert request["method"] == "GET"
    # URL still carries the unresolved placeholder because resolution failed.
    assert "${steps.broken.response.body.path}" in request["url"]
    assert "response" not in details


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
        result = run_manifest(manifest, client=client)

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


def test_run_manifest_request_sent_masks_authorization_header() -> None:
    """request-sent event masks Authorization header values by default."""
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
        run_manifest(v1_manifest, client=client, execution_logger=execution_logger)

    request_events = [event for event in execution_logger.events() if event.type == "request-sent"]
    assert len(request_events) == 1
    headers = request_events[0].payload["headers"]
    assert isinstance(headers, dict)
    assert headers["Authorization"] == "***"
