from pathlib import Path
from typing import cast

import pytest

from conformance.json_types import JsonValue
from conformance.manifest import FormBody, JsonBody, ManifestError, load_manifest, parse_manifest

REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_MANIFEST_PATH = REPO_ROOT / "config" / "manifest-v0-openid-jwks-example.json"


def valid_manifest() -> dict[str, JsonValue]:
    return {
        "schemaVersion": "v0",
        "name": "Ozone OpenID discovery and JWKS smoke check",
        "tests": [
            {
                "id": "openid-discovery",
                "name": "OpenID discovery document",
                "request": {
                    "method": "GET",
                    "url": "https://auth1.obie.uk.ozoneapi.io/.well-known/openid-configuration",
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


def request_config(raw_manifest: dict[str, JsonValue]) -> dict[str, JsonValue]:
    return cast("dict[str, JsonValue]", first_test(raw_manifest)["request"])


def assertion_configs(raw_manifest: dict[str, JsonValue]) -> list[JsonValue]:
    return cast("list[JsonValue]", first_test(raw_manifest)["assertions"])


def follow_up_config(raw_manifest: dict[str, JsonValue]) -> dict[str, JsonValue]:
    return cast("dict[str, JsonValue]", first_test(raw_manifest)["followUp"])


@pytest.mark.unit
def test_load_example_manifest_returns_typed_manifest() -> None:
    manifest = load_manifest(EXAMPLE_MANIFEST_PATH)

    assert manifest.schema_version == "v0"
    assert manifest.name == "Ozone OpenID discovery and JWKS smoke check"
    assert len(manifest.tests) == 1
    test = manifest.tests[0]
    assert test.id == "openid-discovery"
    assert test.request.method == "GET"
    assert test.request.url == "https://auth1.obie.uk.ozoneapi.io/.well-known/openid-configuration"
    assert test.follow_up is not None
    assert test.follow_up.type == "jwks"
    assert test.follow_up.url_source == "response.body.jwks_uri"


@pytest.mark.unit
def test_load_manifest_rejects_malformed_json(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text('{"schemaVersion": "v0",', encoding="utf-8")

    with pytest.raises(ManifestError, match="Invalid JSON manifest"):
        load_manifest(manifest_path)


@pytest.mark.unit
def test_load_manifest_rejects_non_object_root(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text("[]", encoding="utf-8")

    with pytest.raises(ManifestError, match="Manifest root must be a JSON object"):
        load_manifest(manifest_path)


@pytest.mark.unit
def test_parse_manifest_accepts_valid_minimal_discovery_manifest() -> None:
    raw_manifest = valid_manifest()
    first_test(raw_manifest).pop("followUp")

    manifest = parse_manifest(raw_manifest)

    assert manifest.schema_version == "v0"
    assert manifest.tests[0].follow_up is None
    assert [assertion.type for assertion in manifest.tests[0].assertions] == [
        "http_status",
        "json_field",
        "json_field",
    ]


@pytest.mark.unit
def test_parse_manifest_rejects_unsupported_schema_version() -> None:
    raw_manifest = valid_manifest()
    raw_manifest["schemaVersion"] = "v99"

    with pytest.raises(ManifestError, match="schemaVersion must be v0 or v1"):
        parse_manifest(raw_manifest)


@pytest.mark.unit
def test_parse_manifest_rejects_missing_required_fields() -> None:
    raw_manifest = valid_manifest()
    raw_manifest.pop("tests")

    with pytest.raises(ManifestError, match="tests must be a non-empty array"):
        parse_manifest(raw_manifest)


@pytest.mark.unit
def test_parse_manifest_rejects_unknown_fields() -> None:
    raw_manifest = valid_manifest()
    raw_manifest["unexpected"] = "nope"

    with pytest.raises(ManifestError, match="Unknown manifest field"):
        parse_manifest(raw_manifest)


@pytest.mark.unit
def test_parse_manifest_rejects_non_https_request_url() -> None:
    raw_manifest = valid_manifest()
    request_config(raw_manifest)["url"] = "http://example.com/.well-known/openid-configuration"

    with pytest.raises(ManifestError, match=r"tests\[0\]\.request\.url must be an HTTPS URL"):
        parse_manifest(raw_manifest)


@pytest.mark.unit
@pytest.mark.parametrize(
    "url",
    [
        "https://example .com/.well-known/openid-configuration",
        "https://example.com\n.evil.test/.well-known/openid-configuration",
        "https://bad_host.example/.well-known/openid-configuration",
        "https://-example.com/.well-known/openid-configuration",
    ],
)
def test_parse_manifest_rejects_malformed_https_request_url(url: str) -> None:
    raw_manifest = valid_manifest()
    request_config(raw_manifest)["url"] = url

    with pytest.raises(ManifestError, match=r"tests\[0\]\.request\.url must be a valid HTTPS URL"):
        parse_manifest(raw_manifest)


@pytest.mark.unit
@pytest.mark.parametrize(
    "url",
    [
        "https://127.0.0.1/.well-known/openid-configuration",
        "https://10.0.0.1/.well-known/openid-configuration",
        "https://[::1]/.well-known/openid-configuration",
    ],
)
def test_parse_manifest_rejects_ip_literal_request_url(url: str) -> None:
    raw_manifest = valid_manifest()
    request_config(raw_manifest)["url"] = url

    with pytest.raises(ManifestError, match=r"tests\[0\]\.request\.url must use a DNS hostname"):
        parse_manifest(raw_manifest)


@pytest.mark.unit
def test_parse_manifest_rejects_non_get_request_method() -> None:
    raw_manifest = valid_manifest()
    request_config(raw_manifest)["method"] = "POST"

    with pytest.raises(ManifestError, match=r"tests\[0\]\.request\.method must be GET"):
        parse_manifest(raw_manifest)


@pytest.mark.unit
def test_parse_manifest_rejects_unsupported_assertion_type() -> None:
    raw_manifest = valid_manifest()
    assertion_configs(raw_manifest).append({"type": "token_claim", "claim": "iss"})

    with pytest.raises(ManifestError, match=r"tests\[0\]\.assertions\[3\]\.type must be one of"):
        parse_manifest(raw_manifest)


@pytest.mark.unit
def test_parse_manifest_rejects_unsupported_json_field_rule() -> None:
    raw_manifest = valid_manifest()
    json_field_assertion = cast("dict[str, JsonValue]", assertion_configs(raw_manifest)[1])
    json_field_assertion["rule"] = "non_empty"

    with pytest.raises(ManifestError, match=r"tests\[0\]\.assertions\[1\]\.rule must be one of"):
        parse_manifest(raw_manifest)


@pytest.mark.unit
@pytest.mark.parametrize("expected", [True, 99, 600])
def test_parse_manifest_rejects_invalid_http_status_code(expected: JsonValue) -> None:
    raw_manifest = valid_manifest()
    http_status_assertion = cast("dict[str, JsonValue]", assertion_configs(raw_manifest)[0])
    http_status_assertion["expected"] = expected

    with pytest.raises(ManifestError, match=r"tests\[0\]\.assertions\[0\]\.expected must be an HTTP status code"):
        parse_manifest(raw_manifest)


@pytest.mark.unit
def test_parse_manifest_rejects_unsupported_follow_up_shape() -> None:
    raw_manifest = valid_manifest()
    follow_up_config(raw_manifest)["type"] = "token_endpoint"

    with pytest.raises(ManifestError, match=r"tests\[0\]\.followUp\.type must be jwks"):
        parse_manifest(raw_manifest)


@pytest.mark.unit
def test_parse_manifest_rejects_unsupported_follow_up_url_source() -> None:
    raw_manifest = valid_manifest()
    follow_up_config(raw_manifest)["urlSource"] = "response.body.issuer"

    with pytest.raises(ManifestError, match=r"tests\[0\]\.followUp\.urlSource must be response\.body\.jwks_uri"):
        parse_manifest(raw_manifest)


@pytest.mark.unit
def test_parse_manifest_rejects_null_follow_up() -> None:
    raw_manifest = valid_manifest()
    first_test(raw_manifest)["followUp"] = None

    with pytest.raises(ManifestError, match=r"tests\[0\]\.followUp must be a JSON object"):
        parse_manifest(raw_manifest)


@pytest.mark.unit
def test_parse_manifest_rejects_non_get_follow_up_request_method() -> None:
    raw_manifest = valid_manifest()
    follow_up_request = cast("dict[str, JsonValue]", follow_up_config(raw_manifest)["request"])
    follow_up_request["method"] = "POST"

    with pytest.raises(ManifestError, match=r"tests\[0\]\.followUp\.request\.method must be GET"):
        parse_manifest(raw_manifest)


# --- v1 manifest parser tests ---


def valid_v1_manifest() -> dict[str, JsonValue]:
    return {
        "schemaVersion": "v1",
        "name": "Ozone OpenID discovery and JWKS (v1)",
        "steps": [
            {
                "id": "openid-discovery",
                "name": "OpenID discovery document",
                "request": {
                    "method": "GET",
                    "url": "https://auth1.obie.uk.ozoneapi.io/.well-known/openid-configuration",
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
def test_parse_v1_manifest_accepts_minimal_multi_step() -> None:
    raw_manifest = valid_v1_manifest()
    manifest = parse_manifest(raw_manifest)

    assert manifest.schema_version == "v1"
    assert manifest.name == "Ozone OpenID discovery and JWKS (v1)"
    assert len(manifest.steps) == 2
    assert manifest.steps[0].id == "openid-discovery"
    assert manifest.steps[0].request.url == "https://auth1.obie.uk.ozoneapi.io/.well-known/openid-configuration"
    assert manifest.steps[1].id == "jwks-fetch"
    assert manifest.steps[1].request.url == "${steps.openid-discovery.response.body.jwks_uri}"


@pytest.mark.unit
def test_parse_v1_manifest_accepts_single_step_without_placeholders() -> None:
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "Single step",
        "steps": [
            {
                "id": "health",
                "name": "Health check",
                "request": {
                    "method": "GET",
                    "url": "https://example.com/health",
                },
                "assertions": [{"type": "http_status", "expected": 200}],
            }
        ],
    }
    manifest = parse_manifest(raw_manifest)

    assert len(manifest.steps) == 1
    assert manifest.steps[0].id == "health"


@pytest.mark.unit
def test_parse_v1_manifest_rejects_duplicate_step_ids() -> None:
    raw_manifest = valid_v1_manifest()
    steps = cast("list[dict[str, JsonValue]]", raw_manifest["steps"])
    steps[1]["id"] = "openid-discovery"

    with pytest.raises(ManifestError, match=r"steps\[1\]\.id 'openid-discovery' is a duplicate"):
        parse_manifest(raw_manifest)


@pytest.mark.unit
def test_parse_v1_manifest_rejects_forward_reference() -> None:
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "Forward ref",
        "steps": [
            {
                "id": "step-a",
                "name": "Step A",
                "request": {
                    "method": "GET",
                    "url": "${steps.step-b.response.body.url}",
                },
                "assertions": [{"type": "http_status", "expected": 200}],
            },
            {
                "id": "step-b",
                "name": "Step B",
                "request": {
                    "method": "GET",
                    "url": "https://example.com/b",
                },
                "assertions": [{"type": "http_status", "expected": 200}],
            },
        ],
    }

    with pytest.raises(ManifestError, match=r"steps\[0\]\.request\.url references undefined step 'step-b'"):
        parse_manifest(raw_manifest)


@pytest.mark.unit
def test_parse_v1_manifest_rejects_malformed_placeholder() -> None:
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "Bad placeholder",
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
                    "url": "${invalid syntax}",
                },
                "assertions": [{"type": "http_status", "expected": 200}],
            },
        ],
    }

    with pytest.raises(ManifestError, match=r"steps\[1\]\.request\.url contains malformed placeholder"):
        parse_manifest(raw_manifest)


@pytest.mark.unit
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


@pytest.mark.unit
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


@pytest.mark.unit
def test_parse_v1_manifest_rejects_unknown_keys_in_step() -> None:
    raw_manifest = valid_v1_manifest()
    steps = cast("list[dict[str, JsonValue]]", raw_manifest["steps"])
    steps[0]["extra"] = "bad"

    with pytest.raises(ManifestError, match=r"Unknown steps\[0\] field"):
        parse_manifest(raw_manifest)


@pytest.mark.unit
def test_parse_v1_manifest_rejects_unknown_keys_at_root() -> None:
    raw_manifest = valid_v1_manifest()
    raw_manifest["tests"] = []

    with pytest.raises(ManifestError, match="Unknown manifest field"):
        parse_manifest(raw_manifest)


@pytest.mark.unit
def test_parse_v1_manifest_rejects_non_https_url_without_placeholder() -> None:
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "Non-HTTPS",
        "steps": [
            {
                "id": "bad",
                "name": "Bad URL",
                "request": {
                    "method": "GET",
                    "url": "http://example.com/api",
                },
                "assertions": [{"type": "http_status", "expected": 200}],
            }
        ],
    }

    with pytest.raises(ManifestError, match="must be an HTTPS URL"):
        parse_manifest(raw_manifest)


@pytest.mark.unit
def test_parse_v1_manifest_defers_https_validation_for_placeholder_url() -> None:
    """URLs containing placeholders should not be validated at parse time."""
    raw_manifest = valid_v1_manifest()
    manifest = parse_manifest(raw_manifest)

    # The second step has a placeholder URL — it should parse fine
    assert "${steps.openid-discovery.response.body.jwks_uri}" in manifest.steps[1].request.url


# --- v1 manifest parser tests: POST/PUT/PATCH/DELETE, headers, body ---


@pytest.mark.unit
@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_parse_v1_manifest_accepts_non_get_methods(method: str) -> None:
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "Non-GET method",
        "steps": [
            {
                "id": "step-a",
                "name": "Step A",
                "request": {
                    "method": method,
                    "url": "https://example.com/api",
                },
                "assertions": [{"type": "http_status", "expected": 200}],
            }
        ],
    }
    manifest = parse_manifest(raw_manifest)
    assert manifest.steps[0].request.method == method


@pytest.mark.unit
def test_parse_v1_manifest_rejects_unknown_method() -> None:
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "Bad method",
        "steps": [
            {
                "id": "step-a",
                "name": "Step A",
                "request": {
                    "method": "OPTIONS",
                    "url": "https://example.com/api",
                },
                "assertions": [{"type": "http_status", "expected": 200}],
            }
        ],
    }
    with pytest.raises(ManifestError, match="method must be one of"):
        parse_manifest(raw_manifest)


@pytest.mark.unit
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
    assert manifest.steps[0].request.headers == {
        "Authorization": "Bearer token123",
        "X-Custom": "value",
    }


@pytest.mark.unit
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
    assert manifest.steps[0].request.headers == {"Authorization": "Bearer token123"}


@pytest.mark.unit
def test_parse_v1_manifest_rejects_non_string_header_value() -> None:
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "Bad header",
        "steps": [
            {
                "id": "step-a",
                "name": "Step A",
                "request": {
                    "method": "POST",
                    "url": "https://example.com/api",
                    "headers": {"X-Count": 42},
                },
                "assertions": [{"type": "http_status", "expected": 200}],
            }
        ],
    }
    with pytest.raises(ManifestError, match="must be a string value"):
        parse_manifest(raw_manifest)


@pytest.mark.unit
def test_parse_v1_manifest_rejects_empty_header_value() -> None:
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "Empty header",
        "steps": [
            {
                "id": "step-a",
                "name": "Step A",
                "request": {
                    "method": "POST",
                    "url": "https://example.com/api",
                    "headers": {"Authorization": "  "},
                },
                "assertions": [{"type": "http_status", "expected": 200}],
            }
        ],
    }
    with pytest.raises(ManifestError, match="must not be empty"):
        parse_manifest(raw_manifest)


@pytest.mark.unit
@pytest.mark.parametrize("bad_value", ["Bearer\r\nX-Injected: evil", "token\nfoo", "token\rfoo"])
def test_parse_v1_manifest_rejects_header_value_with_crlf(bad_value: str) -> None:
    """Header values containing CR or LF are rejected (RFC 7230 §3.2.6)."""
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "CRLF header",
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


@pytest.mark.unit
def test_parse_v1_manifest_rejects_invalid_header_name() -> None:
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "Bad header name",
        "steps": [
            {
                "id": "step-a",
                "name": "Step A",
                "request": {
                    "method": "POST",
                    "url": "https://example.com/api",
                    "headers": {"Invalid Header": "value"},
                },
                "assertions": [{"type": "http_status", "expected": 200}],
            }
        ],
    }
    with pytest.raises(ManifestError, match="not a valid HTTP header name"):
        parse_manifest(raw_manifest)


@pytest.mark.unit
def test_parse_v1_manifest_accepts_json_body() -> None:
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "With body",
        "steps": [
            {
                "id": "step-a",
                "name": "Step A",
                "request": {
                    "method": "POST",
                    "url": "https://example.com/api",
                    "body": {"grant_type": "authorization_code", "code": "abc123"},
                },
                "assertions": [{"type": "http_status", "expected": 200}],
            }
        ],
    }
    manifest = parse_manifest(raw_manifest)
    parsed = manifest.steps[0].request.body
    assert isinstance(parsed, JsonBody)
    assert parsed.value == {"grant_type": "authorization_code", "code": "abc123"}


@pytest.mark.unit
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

    parsed_body = manifest.steps[0].request.body
    assert isinstance(parsed_body, JsonBody)
    assert parsed_body.value == {
        "credentials": {"client_id": "original"},
        "scopes": ["openid"],
    }


@pytest.mark.unit
def test_parse_v1_manifest_rejects_body_on_get() -> None:
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "GET with body",
        "steps": [
            {
                "id": "step-a",
                "name": "Step A",
                "request": {
                    "method": "GET",
                    "url": "https://example.com/api",
                    "body": {"key": "value"},
                },
                "assertions": [{"type": "http_status", "expected": 200}],
            }
        ],
    }
    with pytest.raises(ManifestError, match="GET requests must not declare a body"):
        parse_manifest(raw_manifest)


@pytest.mark.unit
def test_parse_v1_manifest_rejects_null_body() -> None:
    """Explicit body: null is rejected — omit the key to send no body."""
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "Null body",
        "steps": [
            {
                "id": "step-a",
                "name": "Step A",
                "request": {
                    "method": "POST",
                    "url": "https://example.com/api",
                    "body": None,
                },
                "assertions": [{"type": "http_status", "expected": 200}],
            }
        ],
    }
    with pytest.raises(ManifestError, match="must not be null"):
        parse_manifest(raw_manifest)


@pytest.mark.unit
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
    assert manifest.steps[1].request.headers is not None
    assert "${steps.discovery.response.body.issuer}" in manifest.steps[1].request.headers["X-Issuer"]


@pytest.mark.unit
def test_parse_v1_manifest_rejects_forward_reference_in_header() -> None:
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "Forward ref in header",
        "steps": [
            {
                "id": "step-a",
                "name": "Step A",
                "request": {
                    "method": "POST",
                    "url": "https://example.com/api",
                    "headers": {
                        "Authorization": "${steps.step-b.response.body.token}",
                    },
                },
                "assertions": [{"type": "http_status", "expected": 200}],
            },
            {
                "id": "step-b",
                "name": "Step B",
                "request": {"method": "GET", "url": "https://example.com/b"},
                "assertions": [{"type": "http_status", "expected": 200}],
            },
        ],
    }
    with pytest.raises(ManifestError, match="references undefined step 'step-b'"):
        parse_manifest(raw_manifest)


@pytest.mark.unit
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
    assert manifest.steps[1].request.body is not None


@pytest.mark.unit
def test_parse_v1_manifest_rejects_forward_reference_in_body() -> None:
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "Forward ref in body",
        "steps": [
            {
                "id": "step-a",
                "name": "Step A",
                "request": {
                    "method": "POST",
                    "url": "https://example.com/api",
                    "body": {"ref": "${steps.step-b.response.body.value}"},
                },
                "assertions": [{"type": "http_status", "expected": 200}],
            },
            {
                "id": "step-b",
                "name": "Step B",
                "request": {"method": "GET", "url": "https://example.com/b"},
                "assertions": [{"type": "http_status", "expected": 200}],
            },
        ],
    }
    with pytest.raises(ManifestError, match="references undefined step 'step-b'"):
        parse_manifest(raw_manifest)


@pytest.mark.unit
def test_parse_v1_manifest_rejects_malformed_placeholder_in_body() -> None:
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "Bad placeholder in body",
        "steps": [
            {
                "id": "step-a",
                "name": "Step A",
                "request": {
                    "method": "GET",
                    "url": "https://example.com/a",
                },
                "assertions": [{"type": "http_status", "expected": 200}],
            },
            {
                "id": "step-b",
                "name": "Step B",
                "request": {
                    "method": "POST",
                    "url": "https://example.com/b",
                    "body": {"data": "${invalid syntax}"},
                },
                "assertions": [{"type": "http_status", "expected": 200}],
            },
        ],
    }
    with pytest.raises(ManifestError, match="malformed placeholder"):
        parse_manifest(raw_manifest)


@pytest.mark.unit
def test_parse_v1_manifest_accepts_body_on_delete() -> None:
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "DELETE with body",
        "steps": [
            {
                "id": "step-a",
                "name": "Step A",
                "request": {
                    "method": "DELETE",
                    "url": "https://example.com/api/resource",
                    "body": {"reason": "test cleanup"},
                },
                "assertions": [{"type": "http_status", "expected": 204}],
            }
        ],
    }
    manifest = parse_manifest(raw_manifest)
    parsed = manifest.steps[0].request.body
    assert isinstance(parsed, JsonBody)
    assert parsed.value == {"reason": "test cleanup"}


@pytest.mark.unit
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
    assert manifest.steps[1].request.body is not None


@pytest.mark.unit
@pytest.mark.parametrize(
    "bad_value",
    [
        "Bearer\x00token",  # NUL
        "value\x7ftrailing",  # DEL
        "value\x01control",  # SOH
        "before\x1fafter",  # US (unit separator)
    ],
    ids=["NUL", "DEL", "SOH", "US"],
)
def test_parse_v1_manifest_rejects_header_value_with_control_chars(bad_value: str) -> None:
    """Header values with non-CR/LF control characters are rejected (RFC 7230 §3.2.6)."""
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "Control char header",
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


@pytest.mark.unit
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


@pytest.mark.unit
def test_parse_v1_manifest_accepts_header_value_with_htab() -> None:
    """HTAB (0x09) is permitted in header field values per RFC 7230 §3.2.6."""
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "HTAB header",
        "steps": [
            {
                "id": "step-a",
                "name": "Step A",
                "request": {
                    "method": "POST",
                    "url": "https://example.com/api",
                    "headers": {"Authorization": "Bearer\ttoken"},
                },
                "assertions": [{"type": "http_status", "expected": 200}],
            }
        ],
    }
    manifest = parse_manifest(raw_manifest)
    assert manifest.steps[0].request.headers == {"Authorization": "Bearer\ttoken"}


@pytest.mark.unit
@pytest.mark.parametrize(
    "bad_value",
    [
        "caf\xe9",  # U+00E9 (obs-text, not ASCII-transportable)
        "token\x80rest",  # U+0080 (lowest obs-text)
        "value\xffend",  # U+00FF (highest obs-text)
    ],
    ids=["obs-text-e9", "obs-text-80", "obs-text-ff"],
)
def test_parse_v1_manifest_rejects_header_value_with_obs_text(bad_value: str) -> None:
    """Obs-text characters (0x80-0xFF) are rejected because httpx cannot transport them."""
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "Obs-text header",
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


# --- v1 manifest parser tests: tagged form body (DL-0014) ---


@pytest.mark.unit
def test_parse_v1_manifest_accepts_form_body() -> None:
    """A valid tagged form body parses into a FormBody with the declared fields."""
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "Form body",
        "steps": [
            {
                "id": "token",
                "name": "Token exchange",
                "request": {
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
                "assertions": [{"type": "http_status", "expected": 200}],
            }
        ],
    }
    manifest = parse_manifest(raw_manifest)
    parsed = manifest.steps[0].request.body
    assert isinstance(parsed, FormBody)
    assert dict(parsed.fields) == {
        "grant_type": "authorization_code",
        "code": "abc123",
        "client_id": "test-client",
    }


@pytest.mark.unit
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
    parsed = manifest.steps[1].request.body
    assert isinstance(parsed, FormBody)
    assert parsed.fields["code"] == "${steps.consent.response.body.code}"


@pytest.mark.unit
def test_parse_v1_manifest_rejects_form_body_on_get() -> None:
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "GET with form body",
        "steps": [
            {
                "id": "step-a",
                "name": "Step A",
                "request": {
                    "method": "GET",
                    "url": "https://example.com/api",
                    "body": {"encoding": "form", "fields": {"k": "v"}},
                },
                "assertions": [{"type": "http_status", "expected": 200}],
            }
        ],
    }
    with pytest.raises(ManifestError, match="GET requests must not declare a body"):
        parse_manifest(raw_manifest)


@pytest.mark.unit
def test_parse_v1_manifest_rejects_empty_form_fields() -> None:
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "Empty form fields",
        "steps": [
            {
                "id": "step-a",
                "name": "Step A",
                "request": {
                    "method": "POST",
                    "url": "https://example.com/api",
                    "body": {"encoding": "form", "fields": {}},
                },
                "assertions": [{"type": "http_status", "expected": 200}],
            }
        ],
    }
    with pytest.raises(ManifestError, match="must not be empty"):
        parse_manifest(raw_manifest)


@pytest.mark.unit
def test_parse_v1_manifest_rejects_missing_form_fields() -> None:
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "Missing form fields key",
        "steps": [
            {
                "id": "step-a",
                "name": "Step A",
                "request": {
                    "method": "POST",
                    "url": "https://example.com/api",
                    "body": {"encoding": "form"},
                },
                "assertions": [{"type": "http_status", "expected": 200}],
            }
        ],
    }
    with pytest.raises(ManifestError, match="must include a 'fields' object"):
        parse_manifest(raw_manifest)


@pytest.mark.unit
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


@pytest.mark.unit
def test_parse_v1_manifest_rejects_unknown_encoding() -> None:
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "Bad encoding",
        "steps": [
            {
                "id": "step-a",
                "name": "Step A",
                "request": {
                    "method": "POST",
                    "url": "https://example.com/api",
                    "body": {"encoding": "multipart", "fields": {"k": "v"}},
                },
                "assertions": [{"type": "http_status", "expected": 200}],
            }
        ],
    }
    with pytest.raises(ManifestError, match="encoding must be one of: json, form"):
        parse_manifest(raw_manifest)


@pytest.mark.unit
def test_parse_v1_manifest_rejects_malformed_placeholder_in_form_field() -> None:
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "Bad placeholder in form field",
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
                "request": {
                    "method": "POST",
                    "url": "https://example.com/b",
                    "body": {"encoding": "form", "fields": {"code": "${invalid syntax}"}},
                },
                "assertions": [{"type": "http_status", "expected": 200}],
            },
        ],
    }
    with pytest.raises(ManifestError, match="malformed placeholder"):
        parse_manifest(raw_manifest)


@pytest.mark.unit
def test_parse_v1_manifest_accepts_tagged_json_body() -> None:
    """Explicit ``{"encoding": "json", "value": ...}`` shape is accepted and parses to JsonBody."""
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "Tagged JSON body",
        "steps": [
            {
                "id": "step-a",
                "name": "Step A",
                "request": {
                    "method": "POST",
                    "url": "https://example.com/api",
                    "body": {"encoding": "json", "value": {"k": "v"}},
                },
                "assertions": [{"type": "http_status", "expected": 200}],
            }
        ],
    }
    manifest = parse_manifest(raw_manifest)
    parsed = manifest.steps[0].request.body
    assert isinstance(parsed, JsonBody)
    assert parsed.value == {"k": "v"}


@pytest.mark.unit
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
    parsed = manifest.steps[0].request.body
    assert isinstance(parsed, FormBody)
    # Cast to a mutable mapping so mypy permits the assignment; the runtime
    # TypeError still fires from MappingProxyType.__setitem__, which is what
    # this test is verifying.
    with pytest.raises(TypeError):
        cast(dict[str, str], parsed.fields)["k"] = "tampered"
