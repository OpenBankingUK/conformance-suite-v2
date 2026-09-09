"""Parse-time validation of ``${...}`` placeholders and step dependencies."""

from typing import cast

import pytest

from conformance.json_types import JsonValue
from conformance.manifest import (
    FormBody,
    ManifestError,
    ManifestStep,
    parse_manifest,
)
from tests.support.manifest_documents import v1_manifest, v1_two_step_manifest

pytestmark = pytest.mark.unit


def test_parse_v1_manifest_accepts_resource_base_url_placeholder() -> None:
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "AIS resource placeholder",
        "steps": [
            {
                "id": "accounts",
                "name": "Accounts resource",
                "request": {
                    "method": "GET",
                    "url": "${config.oauth.resourceBaseUrl}/open-banking/v4.0/aisp/accounts",
                },
                "assertions": [{"type": "http_status", "expected": 200}],
            }
        ],
    }

    manifest = parse_manifest(raw_manifest)

    assert cast("ManifestStep", manifest.steps[0]).request.url == (
        "${config.oauth.resourceBaseUrl}/open-banking/v4.0/aisp/accounts"
    )


def test_parse_v1_manifest_accepts_array_index_step_placeholder() -> None:
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "AIS account resource placeholder",
        "steps": [
            {
                "id": "accounts-list",
                "name": "Accounts resource",
                "request": {
                    "method": "GET",
                    "url": "${config.oauth.resourceBaseUrl}/open-banking/v4.0/aisp/accounts",
                },
                "assertions": [{"type": "http_status", "expected": 200}],
            },
            {
                "id": "account-balances",
                "name": "Account balances resource",
                "request": {
                    "method": "GET",
                    "url": (
                        "${config.oauth.resourceBaseUrl}/open-banking/v4.0/aisp/accounts/"
                        "${steps.accounts-list.response.body.Data.Account.0.AccountId}"
                        "/balances"
                    ),
                },
                "assertions": [{"type": "http_status", "expected": 200}],
            },
        ],
    }

    manifest = parse_manifest(raw_manifest)

    assert cast("ManifestStep", manifest.steps[1]).request.url == (
        "${config.oauth.resourceBaseUrl}/open-banking/v4.0/aisp/accounts/"
        "${steps.accounts-list.response.body.Data.Account.0.AccountId}/balances"
    )


def test_parse_v1_manifest_accepts_safe_config_placeholders() -> None:
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "Config placeholder manifest",
        "steps": [
            {
                "id": "config-driven",
                "name": "Config-driven request",
                "request": {
                    "method": "POST",
                    "url": "${config.discoveryUrl}",
                    "headers": {"X-Discovery": "${config.discoveryUrl}"},
                    "body": {
                        "encoding": "json",
                        "value": {
                            "discovery": "${config.discoveryUrl}",
                        },
                    },
                },
                "assertions": [{"type": "http_status", "expected": 200}],
            }
        ],
    }

    manifest = parse_manifest(raw_manifest)

    step = cast("ManifestStep", manifest.steps[0])
    assert step.request.url == "${config.discoveryUrl}"
    assert step.request.headers == {"X-Discovery": "${config.discoveryUrl}"}


def test_parse_v1_manifest_defers_https_validation_for_placeholder_url() -> None:
    """URLs containing placeholders should not be validated at parse time."""
    raw_manifest = v1_manifest()
    manifest = parse_manifest(raw_manifest)

    # The second step has a placeholder URL — it should parse fine
    assert "${steps.openid-discovery.response.body.jwks_uri}" in cast("ManifestStep", manifest.steps[1]).request.url


def test_parse_v1_manifest_validates_placeholders_in_headers() -> None:
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "Header placeholders",
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
                },
                "assertions": [{"type": "http_status", "expected": 200}],
            },
        ],
    }
    manifest = parse_manifest(raw_manifest)
    headers = cast("ManifestStep", manifest.steps[1]).request.headers
    assert headers is not None
    assert "${steps.discovery.response.body.issuer}" in headers["X-Issuer"]


def test_parse_v1_manifest_validates_placeholders_in_body() -> None:
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
                "name": "Token exchange",
                "request": {
                    "method": "POST",
                    "url": "${steps.discovery.response.body.token_endpoint}",
                    "body": {
                        "grant_type": "authorization_code",
                        "token_endpoint": "${steps.discovery.response.body.token_endpoint}",
                    },
                },
                "assertions": [{"type": "http_status", "expected": 200}],
            },
        ],
    }
    manifest = parse_manifest(raw_manifest)
    assert cast("ManifestStep", manifest.steps[1]).request.body is not None


def test_parse_v1_manifest_accepts_body_with_nested_arrays() -> None:
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "Nested body",
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
                "id": "step-a",
                "name": "Step A",
                "request": {
                    "method": "POST",
                    "url": "https://example.com/api",
                    "body": {
                        "items": [
                            {"url": "${steps.discovery.response.body.issuer}"},
                            "literal",
                        ],
                        "count": 2,
                        "active": True,
                        "meta": None,
                    },
                },
                "assertions": [{"type": "http_status", "expected": 200}],
            },
        ],
    }
    manifest = parse_manifest(raw_manifest)
    assert cast("ManifestStep", manifest.steps[1]).request.body is not None


def test_parse_v1_manifest_accepts_form_body_placeholders_in_values() -> None:
    """Placeholders inside form-field values are syntactically validated at parse time."""
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "Form body with placeholders",
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
                            "grant_type": "authorization_code",
                            "code": "${steps.consent.response.body.code}",
                        },
                    },
                },
                "assertions": [{"type": "http_status", "expected": 200}],
            },
        ],
    }
    manifest = parse_manifest(raw_manifest)
    parsed = cast("ManifestStep", manifest.steps[1]).request.body
    assert isinstance(parsed, FormBody)
    assert parsed.fields["code"] == "${steps.consent.response.body.code}"


def test_parse_v1_manifest_rejects_unknown_config_placeholder() -> None:
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "Unsafe config placeholder",
        "steps": [
            {
                "id": "step-a",
                "name": "Step A",
                "request": {
                    "method": "GET",
                    "url": "${config.tls.clientPrivateKeyPath}",
                },
                "assertions": [{"type": "http_status", "expected": 200}],
            }
        ],
    }

    with pytest.raises(ManifestError, match="unsupported config placeholder") as exc_info:
        parse_manifest(raw_manifest)
    message = str(exc_info.value)
    for placeholder in (
        "${config.oauth.authorizationEndpoint}",
        "${config.oauth.issuer}",
        "${config.oauth.tokenEndpoint}",
        "${config.oauth.responseType}",
        "${config.oauth.requestObjectSigningAlg}",
    ):
        assert placeholder in message


@pytest.mark.parametrize(
    "bad_placeholder",
    [
        # request: body is not a valid request field
        "${steps.step-a.request.body.key}",
        # request: status_code is not a valid request field
        "${steps.step-a.request.status_code}",
        # request: url with extra sub-segment
        "${steps.step-a.request.url.extra}",
        # request: method with extra sub-segment
        "${steps.step-a.request.method.extra}",
        # response: method is not a valid response field
        "${steps.step-a.response.method}",
        # response: url is not a valid response field
        "${steps.step-a.response.url}",
        # response: body with no sub-path
        "${steps.step-a.response.body}",
        # response: status_code with extra sub-segment
        "${steps.step-a.response.status_code.extra}",
    ],
)
def test_parse_v1_manifest_rejects_direction_invalid_placeholder(bad_placeholder: str) -> None:
    """Direction-specific placeholder shapes that pass the generic format but are
    not resolvable must be rejected at parse time."""
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "Direction-invalid placeholder",
        "steps": [
            {
                "id": "step-a",
                "name": "Step A",
                "request": {
                    "method": "GET",
                    "url": "https://example.com/path",
                },
                "assertions": [{"type": "http_status", "expected": 200}],
            },
            {
                "id": "step-b",
                "name": "Step B",
                "request": {
                    "method": "GET",
                    "url": bad_placeholder,
                },
                "assertions": [{"type": "http_status", "expected": 200}],
            },
        ],
    }

    with pytest.raises(ManifestError, match="malformed placeholder"):
        parse_manifest(raw_manifest)


def test_parse_v1_manifest_rejects_unterminated_placeholder() -> None:
    """An unclosed ``${`` token must be rejected at parse time, not deferred to execution."""
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "Unterminated placeholder",
        "steps": [
            {
                "id": "step-a",
                "name": "Step A",
                "request": {
                    "method": "GET",
                    "url": "https://example.com/path",
                },
                "assertions": [{"type": "http_status", "expected": 200}],
            },
            {
                "id": "step-b",
                "name": "Step B",
                "request": {
                    "method": "GET",
                    "url": "${steps.step-a.response.body.x",
                },
                "assertions": [{"type": "http_status", "expected": 200}],
            },
        ],
    }

    with pytest.raises(ManifestError, match="unterminated placeholder"):
        parse_manifest(raw_manifest)


FORWARD_REFERENCE_REQUESTS = (
    pytest.param(
        {"method": "GET", "url": "${steps.step-b.response.body.url}"},
        r"steps\[0\]\.request\.url references undefined step 'step-b'",
        id="url",
    ),
    pytest.param(
        {
            "method": "POST",
            "url": "https://example.com/api",
            "headers": {"Authorization": "${steps.step-b.response.body.token}"},
        },
        r"references undefined step 'step-b'",
        id="header",
    ),
    pytest.param(
        {
            "method": "POST",
            "url": "https://example.com/api",
            "body": {"ref": "${steps.step-b.response.body.value}"},
        },
        r"references undefined step 'step-b'",
        id="body",
    ),
)
"""First-step requests that reference the later ``step-b``, which is not yet defined."""


@pytest.mark.parametrize(("request_document", "message"), FORWARD_REFERENCE_REQUESTS)
def test_parse_v1_manifest_rejects_forward_reference(
    request_document: dict[str, JsonValue],
    message: str,
) -> None:
    """Placeholders may only reference steps declared earlier in the manifest.

    Args:
        request_document: Raw request document for the first step.
        message: Regular expression the parse error must match.
    """
    document = v1_two_step_manifest(
        first_request=request_document,
        second_request={"method": "GET", "url": "https://example.com/b"},
    )

    with pytest.raises(ManifestError, match=message):
        parse_manifest(document)


MALFORMED_PLACEHOLDER_REQUESTS = (
    pytest.param(
        {"method": "GET", "url": "${invalid syntax}"},
        r"steps\[1\]\.request\.url contains malformed placeholder",
        id="url",
    ),
    pytest.param(
        {"method": "POST", "url": "https://example.com/b", "body": {"data": "${invalid syntax}"}},
        "malformed placeholder",
        id="json-body",
    ),
    pytest.param(
        {
            "method": "POST",
            "url": "https://example.com/b",
            "body": {"encoding": "form", "fields": {"code": "${invalid syntax}"}},
        },
        "malformed placeholder",
        id="form-field",
    ),
)
"""Second-step requests carrying a syntactically invalid ``${...}`` placeholder."""


@pytest.mark.parametrize(("request_document", "message"), MALFORMED_PLACEHOLDER_REQUESTS)
def test_parse_v1_manifest_rejects_malformed_placeholder(
    request_document: dict[str, JsonValue],
    message: str,
) -> None:
    """Malformed placeholders fail at parse time rather than during execution.

    Args:
        request_document: Raw request document for the second step.
        message: Regular expression the parse error must match.
    """
    document = v1_two_step_manifest(
        first_request={"method": "GET", "url": "https://example.com/a"},
        second_request=request_document,
    )

    with pytest.raises(ManifestError, match=message):
        parse_manifest(document)
