"""Parsing of runtime signing directives: token-endpoint auth, detached JWS, token bindings."""

from typing import NamedTuple, cast

import pytest

from conformance.json_types import JsonValue
from conformance.manifest import (
    DetachedJwsPolicy,
    ManifestError,
    ManifestStep,
    TokenEndpointAuthPolicy,
    parse_manifest,
)
from tests.support.manifest_documents import v1_single_step_manifest

pytestmark = pytest.mark.unit


def test_parse_v1_http_step_accepts_token_endpoint_auth_policy() -> None:
    """HTTP token-exchange steps may declare runtime token-endpoint auth policy."""
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "token-auth-policy",
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
                            "code": "abc",
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

    manifest = parse_manifest(raw_manifest)

    step = cast("ManifestStep", manifest.steps[0])
    assert step.token_endpoint_auth_policy == TokenEndpointAuthPolicy(source="fapi-signing")


def test_parse_v1_http_step_accepts_semantic_token_bindings() -> None:
    """HTTP steps may bind token production and consumption to semantic token ids."""
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "semantic-token-bindings",
        "steps": [
            {
                "id": "token-exchange",
                "name": "Token exchange",
                "request": {
                    "method": "POST",
                    "url": "https://auth.example.com/token",
                    "body": {
                        "encoding": "form",
                        "fields": {"grant_type": "authorization_code", "code": "abc", "client_id": "client-123"},
                    },
                },
                "assertions": [{"type": "http_status", "expected": 200}],
                "producesTokenId": "ais-resource-detail",
            },
            {
                "id": "accounts-list",
                "name": "Accounts",
                "request": {
                    "method": "GET",
                    "url": "https://resource.example.com/open-banking/v4.0/aisp/accounts",
                    "headers": {"Authorization": "Bearer ${tokens.ais-resource-detail.access_token}"},
                },
                "assertions": [{"type": "http_status", "expected": 200}],
                "requiredTokenId": "ais-resource-detail",
            },
        ],
    }

    manifest = parse_manifest(raw_manifest)

    token_step = cast("ManifestStep", manifest.steps[0])
    consumer_step = cast("ManifestStep", manifest.steps[1])
    assert token_step.produces_token_id == "ais-resource-detail"  # noqa: S105 - semantic token id fixture
    assert consumer_step.required_token_id == "ais-resource-detail"  # noqa: S105 - semantic token id fixture


def test_parse_v1_http_step_infers_required_token_id_from_authorization_header() -> None:
    """Required token id is inferred when Authorization uses a token placeholder."""
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "semantic-token-inference",
        "steps": [
            {
                "id": "accounts-list",
                "name": "Accounts",
                "request": {
                    "method": "GET",
                    "url": "https://resource.example.com/open-banking/v4.0/aisp/accounts",
                    "headers": {"Authorization": "Bearer ${tokens.ais-resource-basic.access_token}"},
                },
                "assertions": [{"type": "http_status", "expected": 200}],
            }
        ],
    }

    manifest = parse_manifest(raw_manifest)

    consumer_step = cast("ManifestStep", manifest.steps[0])
    assert consumer_step.required_token_id == "ais-resource-basic"  # noqa: S105 - semantic token id fixture


def test_parse_v1_http_step_rejects_mismatched_required_token_id() -> None:
    """requiredTokenId must match the Authorization token placeholder id."""
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "semantic-token-mismatch",
        "steps": [
            {
                "id": "accounts-list",
                "name": "Accounts",
                "request": {
                    "method": "GET",
                    "url": "https://resource.example.com/open-banking/v4.0/aisp/accounts",
                    "headers": {"Authorization": "Bearer ${tokens.ais-resource-basic.access_token}"},
                },
                "assertions": [{"type": "http_status", "expected": 200}],
                "requiredTokenId": "ais-resource-detail",
            }
        ],
    }

    with pytest.raises(
        ManifestError,
        match=r"steps\[0\]\.requiredTokenId must match Authorization token placeholder id 'ais-resource-basic'",
    ):
        parse_manifest(raw_manifest)


def test_parse_v1_http_step_rejects_required_token_id_without_token_placeholder() -> None:
    """requiredTokenId requires an explicit token placeholder in Authorization."""
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "semantic-token-missing-header-binding",
        "steps": [
            {
                "id": "accounts-list",
                "name": "Accounts",
                "request": {
                    "method": "GET",
                    "url": "https://resource.example.com/open-banking/v4.0/aisp/accounts",
                    "headers": {"Authorization": "Bearer literal-token"},
                },
                "assertions": [{"type": "http_status", "expected": 200}],
                "requiredTokenId": "ais-resource-detail",
            }
        ],
    }

    expected_match = (
        r"steps\[0\]\.requiredTokenId requires Authorization header value "
        r"'Bearer \$\{tokens\.ais-resource-detail\.access_token\}'"
    )
    with pytest.raises(ManifestError, match=expected_match):
        parse_manifest(raw_manifest)


def test_parse_v1_http_step_rejects_unsupported_token_placeholder_shape() -> None:
    """Token placeholders only allow access_token field resolution."""
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "semantic-token-bad-placeholder",
        "steps": [
            {
                "id": "accounts-list",
                "name": "Accounts",
                "request": {
                    "method": "GET",
                    "url": "https://resource.example.com/open-banking/v4.0/aisp/accounts",
                    "headers": {"Authorization": "Bearer ${tokens.ais-resource-detail.id_token}"},
                },
                "assertions": [{"type": "http_status", "expected": 200}],
            }
        ],
    }

    with pytest.raises(
        ManifestError,
        match=r"steps\[0\]\.request\.headers\.Authorization contains unsupported token placeholder",
    ):
        parse_manifest(raw_manifest)


def test_parse_v1_http_step_accepts_detached_jws_policy() -> None:
    """HTTP consent requests may opt into runtime detached-JWS signing."""
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "detached-jws-policy",
        "steps": [
            {
                "id": "consent",
                "name": "Consent",
                "request": {
                    "method": "POST",
                    "url": "https://resource.example.com/open-banking/v4.0/aisp/account-access-consents",
                    "detachedJws": {"source": "fapi-signing"},
                    "body": {"Data": {"Permissions": ["ReadAccountsBasic"]}, "Risk": {}},
                },
                "assertions": [{"type": "http_status", "expected": 201}],
            }
        ],
    }

    manifest = parse_manifest(raw_manifest)

    step = cast("ManifestStep", manifest.steps[0])
    assert step.request.detached_jws == DetachedJwsPolicy(source="fapi-signing")


def test_parse_v1_http_step_accepts_detached_jws_omitted_headers() -> None:
    """Detached-JWS policies may omit OB protected headers for negative tests."""
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "detached-jws-policy",
        "steps": [
            {
                "id": "consent",
                "name": "Consent",
                "request": {
                    "method": "POST",
                    "url": "https://resource.example.com/open-banking/v4.0/pisp/domestic-payment-consents",
                    "detachedJws": {"source": "fapi-signing", "omitProtectedHeaders": ["iss"]},
                    "body": {"Data": {"Initiation": {}}, "Risk": {}},
                },
                "assertions": [{"type": "http_status", "expected": 400}],
            }
        ],
    }

    manifest = parse_manifest(raw_manifest)

    step = cast("ManifestStep", manifest.steps[0])
    assert step.request.detached_jws == DetachedJwsPolicy(source="fapi-signing", omit_protected_headers=("iss",))


TOKEN_REQUEST_FORM_BODY: dict[str, JsonValue] = {
    "encoding": "form",
    "fields": {
        "grant_type": "authorization_code",
        "code": "abc",
        "redirect_uri": "https://app.example.com/callback",
        "client_id": "client-123",
    },
}
"""OAuth 2.0 authorization-code token request body used by the policy matrices."""

CONSENT_REQUEST_BODY: dict[str, JsonValue] = {"Data": {"Permissions": ["ReadAccountsBasic"]}, "Risk": {}}
"""Account-access-consent body used by the detached-JWS policy matrices."""

CONSENT_URL = "https://resource.example.com/open-banking/v4.0/aisp/account-access-consents"
"""Open Banking account-access-consent endpoint used by the detached-JWS matrices."""


class PolicyRejection(NamedTuple):
    """A malformed signing/auth policy directive and the parse error it must raise.

    Attributes:
        request: Raw ``request`` document placed in a one-step manifest.
        step_fields: Extra raw step fields, such as ``tokenEndpointAuthPolicy``.
        message: Regular expression the ``ManifestError`` message must match.
    """

    request: dict[str, JsonValue]
    step_fields: dict[str, JsonValue]
    message: str


POLICY_REJECTIONS = (
    pytest.param(
        PolicyRejection(
            request={"method": "POST", "url": "https://auth.example.com/token", "body": "x"},
            step_fields={"tokenEndpointAuthPolicy": "fapi-signing"},
            message=r"steps\[0\]\.tokenEndpointAuthPolicy must be a JSON object when present",
        ),
        id="token-endpoint-auth-policy-not-an-object",
    ),
    pytest.param(
        PolicyRejection(
            request={
                "method": "DELETE",
                "url": "https://auth.example.com/token",
                "body": TOKEN_REQUEST_FORM_BODY,
            },
            step_fields={"tokenEndpointAuthPolicy": {"source": "fapi-signing"}},
            message=r"steps\[0\]\.tokenEndpointAuthPolicy is only valid on POST requests with a form body",
        ),
        id="token-endpoint-auth-policy-on-non-post",
    ),
    pytest.param(
        PolicyRejection(
            request={
                "method": "POST",
                "url": "https://auth.example.com/token",
                "body": {"encoding": "json", "value": {"grant_type": "authorization_code"}},
            },
            step_fields={"tokenEndpointAuthPolicy": {"source": "fapi-signing"}},
            message=r"steps\[0\]\.tokenEndpointAuthPolicy is only valid on POST requests with a form body",
        ),
        id="token-endpoint-auth-policy-without-form-body",
    ),
    pytest.param(
        PolicyRejection(
            request={"method": "POST", "url": "https://auth.example.com/token", "body": "x"},
            step_fields={"tokenEndpointAuthPolicy": {"source": "${config.fapiSigning.kid}"}},
            message=r"steps\[0\]\.tokenEndpointAuthPolicy\.source contains unsupported config placeholder",
        ),
        id="token-endpoint-auth-policy-secret-bearing-placeholder",
    ),
    pytest.param(
        PolicyRejection(
            request={
                "method": "POST",
                "url": CONSENT_URL,
                "detachedJws": "fapi-signing",
                "body": CONSENT_REQUEST_BODY,
            },
            step_fields={},
            message=r"steps\[0\]\.request\.detachedJws must be a JSON object when present",
        ),
        id="detached-jws-not-an-object",
    ),
    pytest.param(
        PolicyRejection(
            request={
                "method": "POST",
                "url": CONSENT_URL,
                "detachedJws": {"source": "hand-rolled"},
                "body": CONSENT_REQUEST_BODY,
            },
            step_fields={},
            message=r"steps\[0\]\.request\.detachedJws\.source must be 'fapi-signing'",
        ),
        id="detached-jws-unknown-source",
    ),
    pytest.param(
        PolicyRejection(
            request={
                "method": "POST",
                "url": CONSENT_URL,
                "detachedJws": {"source": "${config.fapiSigning.kid}"},
                "body": CONSENT_REQUEST_BODY,
            },
            step_fields={},
            message=r"steps\[0\]\.request\.detachedJws\.source contains unsupported config placeholder",
        ),
        id="detached-jws-secret-bearing-placeholder",
    ),
    pytest.param(
        PolicyRejection(
            request={
                "method": "DELETE",
                "url": "https://resource.example.com/open-banking/v4.0/aisp/accounts",
                "detachedJws": {"source": "fapi-signing"},
            },
            step_fields={},
            message=r"steps\[0\]\.request\.detachedJws is only valid on POST, PUT, or PATCH requests",
        ),
        id="detached-jws-on-unsupported-method",
    ),
)
"""Policy directives the v1 parser rejects, with the message each must produce.

Both directives select runtime FAPI signing material, so their shapes stay
closed: a manifest may not name a different source, widen the safe config
placeholder allow-list, or attach signing to a request the profile does not
sign.
"""


@pytest.mark.parametrize("case", POLICY_REJECTIONS)
def test_parse_v1_http_step_rejects_invalid_signing_policy(case: PolicyRejection) -> None:
    """Malformed token-auth and detached-JWS directives fail parse.

    Args:
        case: Policy directive under test and the message it must produce.
    """
    document = v1_single_step_manifest(request=case.request, **case.step_fields)

    with pytest.raises(ManifestError, match=case.message):
        parse_manifest(document)
