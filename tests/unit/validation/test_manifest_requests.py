"""Parsing of v1 request documents: methods, headers, and typed request bodies."""

from typing import NamedTuple, cast

import pytest

from conformance.json_types import JsonValue
from conformance.manifest import (
    FormBody,
    JsonBody,
    ManifestBody,
    ManifestError,
    ManifestStep,
    RequestMethod,
    parse_manifest,
)
from tests.support.manifest_documents import v1_single_step_manifest

pytestmark = pytest.mark.unit


def test_parse_v1_manifest_accepts_headers() -> None:
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "With headers",
        "steps": [
            {
                "id": "step-a",
                "name": "Step A",
                "request": {
                    "method": "POST",
                    "url": "https://example.com/api",
                    "headers": {
                        "Authorization": "Bearer token123",
                        "X-Custom": "value",
                    },
                },
                "assertions": [{"type": "http_status", "expected": 200}],
            }
        ],
    }
    manifest = parse_manifest(raw_manifest)
    assert cast("ManifestStep", manifest.steps[0]).request.headers == {
        "Authorization": "Bearer token123",
        "X-Custom": "value",
    }


def test_parse_v1_manifest_accepts_headers_on_get() -> None:
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "GET with headers",
        "steps": [
            {
                "id": "step-a",
                "name": "Step A",
                "request": {
                    "method": "GET",
                    "url": "https://example.com/api",
                    "headers": {"Authorization": "Bearer token123"},
                },
                "assertions": [{"type": "http_status", "expected": 200}],
            }
        ],
    }
    manifest = parse_manifest(raw_manifest)
    assert cast("ManifestStep", manifest.steps[0]).request.headers == {"Authorization": "Bearer token123"}


@pytest.mark.parametrize(
    "bad_value",
    [
        "Bearer \U0001f600 token",  # emoji U+1F600
        "line\u2028separator",  # U+2028 line separator
        "value\u0100end",  # U+0100 (just above 0xFF)
    ],
    ids=["emoji", "line-separator", "U+0100"],
)
def test_parse_v1_manifest_rejects_header_value_above_0xff(bad_value: str) -> None:
    """Header values with characters above U+00FF are rejected (RFC 7230 §3.2.6)."""
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "Above 0xFF header",
        "steps": [
            {
                "id": "step-a",
                "name": "Step A",
                "request": {
                    "method": "POST",
                    "url": "https://example.com/api",
                    "headers": {"Authorization": bad_value},
                },
                "assertions": [{"type": "http_status", "expected": 200}],
            }
        ],
    }
    with pytest.raises(ManifestError, match="non-transportable character"):
        parse_manifest(raw_manifest)


def test_parse_v1_manifest_body_is_isolated_from_raw_dict() -> None:
    """Mutating the raw manifest dict after parsing must not change the parsed body.

    The parsed ``ManifestRequest`` is frozen, but its ``body`` field holds
    nested JSON structures. Without a deep copy at parse time, post-parse
    mutation of the input could bypass placeholder validation and change
    what the executor sends.
    """
    inner_body: dict[str, JsonValue] = {
        "credentials": {"client_id": "original"},
        "scopes": ["openid"],
    }
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "Mutation safety",
        "steps": [
            {
                "id": "step-a",
                "name": "Step A",
                "request": {
                    "method": "POST",
                    "url": "https://example.com/api",
                    "body": inner_body,
                },
                "assertions": [{"type": "http_status", "expected": 200}],
            }
        ],
    }

    manifest = parse_manifest(raw_manifest)

    # Mutate the original nested structures after parsing.
    inner_body["credentials"] = {"client_id": "tampered"}
    cast(list[JsonValue], inner_body["scopes"]).append("offline_access")

    parsed_body = cast("ManifestStep", manifest.steps[0]).request.body
    assert isinstance(parsed_body, JsonBody)
    assert parsed_body.value == {
        "credentials": {"client_id": "original"},
        "scopes": ["openid"],
    }


@pytest.mark.parametrize(
    "bad_value",
    [123, True, None, ["a"], {"nested": "x"}],
    ids=["int", "bool", "null", "list", "object"],
)
def test_parse_v1_manifest_rejects_non_string_form_value(bad_value: JsonValue) -> None:
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "Non-string form value",
        "steps": [
            {
                "id": "step-a",
                "name": "Step A",
                "request": {
                    "method": "POST",
                    "url": "https://example.com/api",
                    "body": {"encoding": "form", "fields": {"k": bad_value}},
                },
                "assertions": [{"type": "http_status", "expected": 200}],
            }
        ],
    }
    with pytest.raises(ManifestError, match="must be a string value"):
        parse_manifest(raw_manifest)


def test_parse_v1_manifest_form_body_is_immutable_after_parse() -> None:
    """Form fields are exposed as a read-only mapping to prevent post-parse tampering."""
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "Form body immutability",
        "steps": [
            {
                "id": "step-a",
                "name": "Step A",
                "request": {
                    "method": "POST",
                    "url": "https://example.com/api",
                    "body": {"encoding": "form", "fields": {"k": "v"}},
                },
                "assertions": [{"type": "http_status", "expected": 200}],
            }
        ],
    }
    manifest = parse_manifest(raw_manifest)
    parsed = cast("ManifestStep", manifest.steps[0]).request.body
    assert isinstance(parsed, FormBody)
    # Cast to a mutable mapping so mypy permits the assignment; the runtime
    # TypeError still fires from MappingProxyType.__setitem__, which is what
    # this test is verifying.
    with pytest.raises(TypeError):
        cast(dict[str, str], parsed.fields)["k"] = "tampered"


class RequestAcceptance(NamedTuple):
    """An accepted v1 request document and the parsed request it must yield.

    Attributes:
        request: Raw ``request`` document placed in a one-step manifest.
        expected_method: HTTP method the parser must record.
        expected_url: Request URL the parser must record verbatim.
        expected_headers: Parsed headers, or ``None`` when none were declared.
        expected_body: Parsed typed body, or ``None`` when none was declared.
    """

    request: dict[str, JsonValue]
    expected_method: RequestMethod
    expected_url: str
    expected_headers: dict[str, str] | None = None
    expected_body: ManifestBody | None = None


REQUEST_ACCEPTANCES = (
    pytest.param(
        RequestAcceptance(
            request={"method": "GET", "url": "https://example.com/health"},
            expected_method="GET",
            expected_url="https://example.com/health",
        ),
        id="get-without-placeholders",
    ),
    pytest.param(
        RequestAcceptance(
            request={"method": "POST", "url": "https://example.com/api"},
            expected_method="POST",
            expected_url="https://example.com/api",
        ),
        id="method-post",
    ),
    pytest.param(
        RequestAcceptance(
            request={"method": "PUT", "url": "https://example.com/api"},
            expected_method="PUT",
            expected_url="https://example.com/api",
        ),
        id="method-put",
    ),
    pytest.param(
        RequestAcceptance(
            request={"method": "PATCH", "url": "https://example.com/api"},
            expected_method="PATCH",
            expected_url="https://example.com/api",
        ),
        id="method-patch",
    ),
    pytest.param(
        RequestAcceptance(
            request={"method": "DELETE", "url": "https://example.com/api"},
            expected_method="DELETE",
            expected_url="https://example.com/api",
        ),
        id="method-delete",
    ),
    pytest.param(
        RequestAcceptance(
            request={
                "method": "POST",
                "url": "https://example.com/api",
                "headers": {"Authorization": "Bearer\ttoken"},
            },
            expected_method="POST",
            expected_url="https://example.com/api",
            expected_headers={"Authorization": "Bearer\ttoken"},
        ),
        id="header-value-with-htab",
    ),
    pytest.param(
        RequestAcceptance(
            request={
                "method": "POST",
                "url": "https://example.com/api",
                "body": {"grant_type": "authorization_code", "code": "abc123"},
            },
            expected_method="POST",
            expected_url="https://example.com/api",
            expected_body=JsonBody(value={"grant_type": "authorization_code", "code": "abc123"}),
        ),
        id="untagged-json-body",
    ),
    pytest.param(
        RequestAcceptance(
            request={
                "method": "POST",
                "url": "https://example.com/api",
                "body": {"encoding": "json", "value": {"k": "v"}},
            },
            expected_method="POST",
            expected_url="https://example.com/api",
            expected_body=JsonBody(value={"k": "v"}),
        ),
        id="tagged-json-body",
    ),
    pytest.param(
        RequestAcceptance(
            request={
                "method": "DELETE",
                "url": "https://example.com/api/resource",
                "body": {"reason": "test cleanup"},
            },
            expected_method="DELETE",
            expected_url="https://example.com/api/resource",
            expected_body=JsonBody(value={"reason": "test cleanup"}),
        ),
        id="json-body-on-delete",
    ),
    pytest.param(
        RequestAcceptance(
            request={
                "method": "POST",
                "url": "https://example.com/token",
                "body": {
                    "encoding": "form",
                    "fields": {
                        "grant_type": "authorization_code",
                        "code": "abc123",
                        "client_id": "test-client",
                    },
                },
            },
            expected_method="POST",
            expected_url="https://example.com/token",
            expected_body=FormBody(
                fields={
                    "grant_type": "authorization_code",
                    "code": "abc123",
                    "client_id": "test-client",
                }
            ),
        ),
        id="form-body",
    ),
)
"""Request documents the v1 parser accepts, with the parsed request they yield."""


@pytest.mark.parametrize("case", REQUEST_ACCEPTANCES)
def test_parse_v1_manifest_accepts_request_shape(case: RequestAcceptance) -> None:
    """Valid request documents parse into the declared ``ManifestRequest`` fields.

    Args:
        case: Request document under test and the parsed fields it must yield.
    """
    manifest = parse_manifest(v1_single_step_manifest(request=case.request))

    parsed = cast("ManifestStep", manifest.steps[0]).request
    assert parsed.method == case.expected_method
    assert parsed.url == case.expected_url
    assert parsed.headers == case.expected_headers
    assert parsed.body == case.expected_body


class RequestRejection(NamedTuple):
    """A malformed v1 request document and the parse error it must raise.

    Attributes:
        request: Raw ``request`` document placed in a one-step manifest.
        message: Regular expression the ``ManifestError`` message must match.
    """

    request: dict[str, JsonValue]
    message: str


REQUEST_REJECTIONS = (
    pytest.param(
        RequestRejection(
            request={"method": "OPTIONS", "url": "https://example.com/api"},
            message="method must be one of",
        ),
        id="unknown-method",
    ),
    pytest.param(
        RequestRejection(
            request={"method": "GET", "url": "http://example.com/api"},
            message="must be an HTTPS URL",
        ),
        id="non-https-url-without-placeholder",
    ),
    pytest.param(
        RequestRejection(
            request={"method": "POST", "url": "https://example.com/api", "headers": {"X-Count": 42}},
            message="must be a string value",
        ),
        id="non-string-header-value",
    ),
    pytest.param(
        RequestRejection(
            request={"method": "POST", "url": "https://example.com/api", "headers": {"Authorization": "  "}},
            message="must not be empty",
        ),
        id="empty-header-value",
    ),
    pytest.param(
        RequestRejection(
            request={"method": "POST", "url": "https://example.com/api", "headers": {"Invalid Header": "value"}},
            message="not a valid HTTP header name",
        ),
        id="invalid-header-name",
    ),
    pytest.param(
        RequestRejection(
            request={"method": "GET", "url": "https://example.com/api", "body": {"key": "value"}},
            message="GET requests must not declare a body",
        ),
        id="json-body-on-get",
    ),
    pytest.param(
        RequestRejection(
            request={"method": "POST", "url": "https://example.com/api", "body": None},
            message="must not be null",
        ),
        id="null-body",
    ),
    pytest.param(
        RequestRejection(
            request={
                "method": "GET",
                "url": "https://example.com/api",
                "body": {"encoding": "form", "fields": {"k": "v"}},
            },
            message="GET requests must not declare a body",
        ),
        id="form-body-on-get",
    ),
    pytest.param(
        RequestRejection(
            request={"method": "POST", "url": "https://example.com/api", "body": {"encoding": "form", "fields": {}}},
            message="must not be empty",
        ),
        id="empty-form-fields",
    ),
    pytest.param(
        RequestRejection(
            request={"method": "POST", "url": "https://example.com/api", "body": {"encoding": "form"}},
            message="must include a 'fields' object",
        ),
        id="missing-form-fields",
    ),
    pytest.param(
        RequestRejection(
            request={
                "method": "POST",
                "url": "https://example.com/api",
                "body": {"encoding": "multipart", "fields": {"k": "v"}},
            },
            message="encoding must be one of: json, form",
        ),
        id="unknown-body-encoding",
    ),
)
"""Request documents the v1 parser rejects, with the message each must produce."""


@pytest.mark.parametrize("case", REQUEST_REJECTIONS)
def test_parse_v1_manifest_rejects_invalid_request_shape(case: RequestRejection) -> None:
    """Malformed request documents fail parse with a field-specific message.

    Args:
        case: Request document under test and the message it must produce.
    """
    with pytest.raises(ManifestError, match=case.message):
        parse_manifest(v1_single_step_manifest(request=case.request))


NON_TRANSPORTABLE_HEADER_VALUES = (
    pytest.param("Bearer\r\nX-Injected: evil", id="crlf-injection"),
    pytest.param("token\nfoo", id="lf"),
    pytest.param("token\rfoo", id="cr"),
    pytest.param("Bearer\x00token", id="control-nul"),
    pytest.param("value\x7ftrailing", id="control-del"),
    pytest.param("value\x01control", id="control-soh"),
    pytest.param("before\x1fafter", id="control-us"),
    pytest.param("caf\xe9", id="obs-text-e9"),
    pytest.param("token\x80rest", id="obs-text-80"),
    pytest.param("value\xffend", id="obs-text-ff"),
)
"""Header values RFC 7230 section 3.2.6 forbids, or that httpx cannot transport.

Covers header-injection sequences (CR, LF, CRLF), other C0/C1 control
characters, and obs-text bytes 0x80-0xFF.
"""


@pytest.mark.parametrize("bad_value", NON_TRANSPORTABLE_HEADER_VALUES)
def test_parse_v1_manifest_rejects_non_transportable_header_value(bad_value: str) -> None:
    """Header values carrying non-transportable characters fail parse.

    Args:
        bad_value: Header value that must be rejected.
    """
    request: dict[str, JsonValue] = {
        "method": "POST",
        "url": "https://example.com/api",
        "headers": {"Authorization": bad_value},
    }

    with pytest.raises(ManifestError, match="non-transportable character"):
        parse_manifest(v1_single_step_manifest(request=request))
