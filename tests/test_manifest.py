import re
from pathlib import Path
from typing import cast

import pytest

from conformance.json_types import JsonValue
from conformance.manifest import (
    CertificationCoverage,
    FormBody,
    JsonBody,
    ManifestError,
    ManifestStep,
    PsuAuthorizationStep,
    load_manifest,
    parse_manifest,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_MANIFEST_PATH = REPO_ROOT / "config" / "manifest-v0-openid-jwks-example.json"
PSU_EXAMPLE_MANIFEST_PATH = REPO_ROOT / "config" / "manifest-v1-psu-authorization-example.json"


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
def test_parse_v1_manifest_accepts_extended_assertion_vocabulary() -> None:
    raw_manifest = valid_v1_manifest()
    step = cast("dict[str, JsonValue]", cast("list[JsonValue]", raw_manifest["steps"])[0])
    step["assertions"] = [
        {"type": "http_status", "expected": 200},
        {"type": "json_field", "path": "issuer", "rule": "required"},
        {"type": "json_field", "path": "issuer", "rule": "absent"},
        {"type": "json_field", "path": "issuer", "rule": "string"},
        {"type": "json_field", "path": "expires_in", "rule": "number"},
        {"type": "json_field", "path": "tls", "rule": "boolean"},
        {"type": "json_field", "path": "metadata", "rule": "object"},
        {"type": "json_field", "path": "keys", "rule": "array"},
        {"type": "json_field", "path": "keys", "rule": "non_empty_array"},
        {"type": "json_field", "path": "keys", "rule": "min_items", "minItems": 1},
        {"type": "json_field", "path": "issuer", "rule": "equals", "value": "https://example.com"},
        {
            "type": "json_field",
            "path": "token_endpoint_auth_method",
            "rule": "one_of",
            "values": ["private_key_jwt", "tls_client_auth"],
        },
        {"type": "json_field", "path": "keys", "rule": "all_items_have_field", "field": "kid"},
        {"type": "header", "name": "content-type", "rule": "present"},
        {"type": "header", "name": "set-cookie", "rule": "absent"},
        {"type": "header", "name": "cache-control", "rule": "equals", "value": "no-store"},
        {"type": "header", "name": "x-fapi-interaction-id", "rule": "contains", "value": "abc"},
    ]

    manifest = parse_manifest(raw_manifest)

    parsed_step = cast("ManifestStep", manifest.steps[0])
    assert len(parsed_step.assertions) == 17
    assert parsed_step.assertions[13].type == "header"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("raw_assertion", "message"),
    [
        (
            {"type": "json_field", "path": "issuer", "rule": "equals"},
            r"steps\[0\]\.assertions\[0\]\.value must be present for json_field rule equals",
        ),
        (
            {"type": "json_field", "path": "issuer", "rule": "one_of"},
            r"steps\[0\]\.assertions\[0\]\.values must be a non-empty array",
        ),
        (
            {"type": "json_field", "path": "issuer", "rule": "min_items"},
            r"steps\[0\]\.assertions\[0\]\.minItems must be an integer greater than or equal to 1",
        ),
        (
            {"type": "json_field", "path": "keys", "rule": "all_items_have_field"},
            r"steps\[0\]\.assertions\[0\]\.field must be a non-empty string",
        ),
        (
            {"type": "header", "name": "content-type", "rule": "equals"},
            r"steps\[0\]\.assertions\[0\]\.value must be a non-empty string for header rule equals",
        ),
        (
            {"type": "header", "name": "cache-control", "rule": "contains"},
            r"steps\[0\]\.assertions\[0\]\.value must be a non-empty string for header rule contains",
        ),
    ],
)
def test_parse_v1_manifest_rejects_missing_rule_specific_fields(
    raw_assertion: dict[str, JsonValue],
    message: str,
) -> None:
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "Rule specific field validation",
        "steps": [
            {
                "id": "step-a",
                "name": "Step A",
                "request": {
                    "method": "GET",
                    "url": "https://example.com/resource",
                },
                "assertions": [raw_assertion],
            }
        ],
    }

    with pytest.raises(ManifestError, match=message):
        parse_manifest(raw_manifest)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("raw_assertion", "message"),
    [
        (
            {"type": "json_field", "path": "issuer", "rule": "equals", "value": cast(JsonValue, {"bad": {1, 2}})},
            r"steps\[0\]\.assertions\[0\]\.value must be valid JSON-compatible data",
        ),
        (
            {
                "type": "json_field",
                "path": "issuer",
                "rule": "one_of",
                "values": ["https://example.com", cast(JsonValue, {"bad": {1, 2}})],
            },
            r"steps\[0\]\.assertions\[0\]\.values\[1\] must be valid JSON-compatible data",
        ),
        (
            {"type": "json_field", "path": "issuer", "rule": "equals", "value": float("nan")},
            r"steps\[0\]\.assertions\[0\]\.value must be valid JSON-compatible data",
        ),
        (
            {"type": "json_field", "path": "issuer", "rule": "one_of", "values": ["https://example.com", float("inf")]},
            r"steps\[0\]\.assertions\[0\]\.values\[1\] must be valid JSON-compatible data",
        ),
        (
            {"type": "json_field", "path": "keys", "rule": "min_items", "minItems": 0},
            r"steps\[0\]\.assertions\[0\]\.minItems must be an integer greater than or equal to 1",
        ),
        (
            {"type": "json_field", "path": "keys", "rule": "min_items", "minItems": True},
            r"steps\[0\]\.assertions\[0\]\.minItems must be an integer greater than or equal to 1",
        ),
        (
            {"type": "json_field", "path": "keys", "rule": "min_items", "minItems": "2"},
            r"steps\[0\]\.assertions\[0\]\.minItems must be an integer greater than or equal to 1",
        ),
        (
            {"type": "header", "name": "content-type", "rule": "present", "value": "application/json"},
            r"Unknown steps\[0\]\.assertions\[0\] field: value",
        ),
    ],
)
def test_parse_v1_manifest_rejects_invalid_assertion_value_shapes(
    raw_assertion: dict[str, JsonValue],
    message: str,
) -> None:
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "Assertion value validation",
        "steps": [
            {
                "id": "step-a",
                "name": "Step A",
                "request": {
                    "method": "GET",
                    "url": "https://example.com/resource",
                },
                "assertions": [raw_assertion],
            }
        ],
    }

    with pytest.raises(ManifestError, match=message):
        parse_manifest(raw_manifest)


@pytest.mark.unit
def test_parse_v1_manifest_rejects_unsupported_header_rule() -> None:
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "Unsupported header rule",
        "steps": [
            {
                "id": "step-a",
                "name": "Step A",
                "request": {
                    "method": "GET",
                    "url": "https://example.com/resource",
                },
                "assertions": [
                    {"type": "header", "name": "content-type", "rule": "matches"},
                ],
            }
        ],
    }

    error_pattern = (
        r"steps\[0\]\.assertions\[0\]\.rule must be one of: "
        r"present, absent, equals, contains"
    )

    with pytest.raises(ManifestError, match=error_pattern):
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
    assert (
        cast("ManifestStep", manifest.steps[0]).request.url
        == "https://auth1.obie.uk.ozoneapi.io/.well-known/openid-configuration"
    )
    assert manifest.steps[1].id == "jwks-fetch"
    assert cast("ManifestStep", manifest.steps[1]).request.url == "${steps.openid-discovery.response.body.jwks_uri}"


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


@pytest.mark.unit
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
                    "headers": {"X-Environment": "${config.environment}"},
                    "body": {
                        "encoding": "json",
                        "value": {
                            "discovery": "${config.discoveryUrl}",
                            "environment": "${config.environment}",
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
    assert step.request.headers == {"X-Environment": "${config.environment}"}


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

    with pytest.raises(ManifestError, match="unsupported config placeholder"):
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


# --- v1 manifest: certificationCoverage metadata ---


@pytest.mark.unit
def test_parse_v1_manifest_defaults_coverage_to_partial_when_absent() -> None:
    """Omitting certificationCoverage must default to partial for safety."""
    raw_manifest = valid_v1_manifest()
    assert "certificationCoverage" not in raw_manifest

    manifest = parse_manifest(raw_manifest)

    assert manifest.certification_coverage == "partial"


@pytest.mark.unit
def test_parse_v1_manifest_accepts_explicit_partial_coverage() -> None:
    """An explicit certificationCoverage: partial is accepted and round-trips correctly."""
    raw_manifest = valid_v1_manifest()
    raw_manifest["certificationCoverage"] = "partial"

    manifest = parse_manifest(raw_manifest)

    assert manifest.certification_coverage == "partial"


@pytest.mark.unit
def test_parse_v1_manifest_accepts_complete_coverage() -> None:
    """certificationCoverage: complete is accepted for a manifest declaring full coverage."""
    raw_manifest = valid_v1_manifest()
    raw_manifest["certificationCoverage"] = "complete"

    manifest = parse_manifest(raw_manifest)

    assert manifest.certification_coverage == "complete"


@pytest.mark.unit
@pytest.mark.parametrize("bad_value", ["", "full", "none", "PARTIAL", 1, True, None])
def test_parse_v1_manifest_rejects_invalid_coverage_value(bad_value: JsonValue) -> None:
    """certificationCoverage must be exactly partial or complete — any other value is rejected."""
    raw_manifest = valid_v1_manifest()
    raw_manifest["certificationCoverage"] = bad_value

    with pytest.raises(ManifestError, match="certificationCoverage must be one of: partial, complete"):
        parse_manifest(raw_manifest)


@pytest.mark.unit
def test_parse_v0_manifest_always_has_partial_coverage() -> None:
    """v0 manifests always default to partial — the field is absent from the schema."""
    manifest = parse_manifest(valid_manifest())

    assert manifest.certification_coverage == "partial"


@pytest.mark.unit
def test_parse_v1_manifest_coverage_type_is_literal() -> None:
    """The certification_coverage field carries a properly typed CertificationCoverage value."""
    raw_manifest = valid_v1_manifest()
    raw_manifest["certificationCoverage"] = "complete"

    manifest = parse_manifest(raw_manifest)

    coverage: CertificationCoverage = manifest.certification_coverage
    assert coverage in ("partial", "complete")


@pytest.mark.unit
def test_load_bundled_discovery_jwks_suite_is_partial(
    tmp_path: pytest.TempPathFactory,
) -> None:
    """Bundled discovery-jwks suite manifests must be explicitly marked as partial."""
    suites_dir = REPO_ROOT / "conformance" / "suites"
    for suite_path in suites_dir.glob("*discovery-jwks*.json"):
        manifest = load_manifest(suite_path)
        assert manifest.certification_coverage == "partial", (
            f"{suite_path.name} must have certificationCoverage: partial"
        )


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
    assert "${steps.openid-discovery.response.body.jwks_uri}" in cast("ManifestStep", manifest.steps[1]).request.url


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
    assert cast("ManifestStep", manifest.steps[0]).request.method == method


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
    assert cast("ManifestStep", manifest.steps[0]).request.headers == {
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
    assert cast("ManifestStep", manifest.steps[0]).request.headers == {"Authorization": "Bearer token123"}


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
    parsed = cast("ManifestStep", manifest.steps[0]).request.body
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

    parsed_body = cast("ManifestStep", manifest.steps[0]).request.body
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
    headers = cast("ManifestStep", manifest.steps[1]).request.headers
    assert headers is not None
    assert "${steps.discovery.response.body.issuer}" in headers["X-Issuer"]


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
    assert cast("ManifestStep", manifest.steps[1]).request.body is not None


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
    parsed = cast("ManifestStep", manifest.steps[0]).request.body
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
    assert cast("ManifestStep", manifest.steps[1]).request.body is not None


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
    assert cast("ManifestStep", manifest.steps[0]).request.headers == {"Authorization": "Bearer\ttoken"}


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
    parsed = cast("ManifestStep", manifest.steps[0]).request.body
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
    parsed = cast("ManifestStep", manifest.steps[1]).request.body
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
    parsed = cast("ManifestStep", manifest.steps[0]).request.body
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
    parsed = cast("ManifestStep", manifest.steps[0]).request.body
    assert isinstance(parsed, FormBody)
    # Cast to a mutable mapping so mypy permits the assignment; the runtime
    # TypeError still fires from MappingProxyType.__setitem__, which is what
    # this test is verifying.
    with pytest.raises(TypeError):
        cast(dict[str, str], parsed.fields)["k"] = "tampered"


@pytest.mark.unit
def test_parse_v1_step_accepts_optional_warning() -> None:
    """A v1 step accepts an optional ``warning`` field carried into the dataclass."""
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "warn-aware",
        "steps": [
            {
                "id": "first",
                "name": "First step",
                "request": {"method": "GET", "url": "https://example.com/a"},
                "assertions": [{"type": "http_status", "expected": 200}],
                "warning": "Use endpoint /b instead (deprecated in v4.1)",
            }
        ],
    }
    manifest = parse_manifest(raw_manifest)
    assert cast("ManifestStep", manifest.steps[0]).warning == "Use endpoint /b instead (deprecated in v4.1)"


@pytest.mark.unit
def test_parse_v1_step_warning_defaults_to_none() -> None:
    """When ``warning`` is omitted the parsed step exposes ``warning is None``."""
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "no-warn",
        "steps": [
            {
                "id": "first",
                "name": "First step",
                "request": {"method": "GET", "url": "https://example.com/a"},
                "assertions": [{"type": "http_status", "expected": 200}],
            }
        ],
    }
    manifest = parse_manifest(raw_manifest)
    assert cast("ManifestStep", manifest.steps[0]).warning is None


@pytest.mark.unit
@pytest.mark.parametrize("bad_warning", ["", "   ", 42, None, []])
def test_parse_v1_step_warning_rejects_non_string_or_empty(bad_warning: JsonValue) -> None:
    """A ``warning`` field that is not a non-empty string fails parse."""
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "bad-warn",
        "steps": [
            {
                "id": "first",
                "name": "First step",
                "request": {"method": "GET", "url": "https://example.com/a"},
                "assertions": [{"type": "http_status", "expected": 200}],
                "warning": bad_warning,
            }
        ],
    }
    with pytest.raises(ManifestError, match=r"steps\[0\]\.warning must be a non-empty string"):
        parse_manifest(raw_manifest)


@pytest.mark.unit
def test_parse_v1_step_accepts_mandatory_true() -> None:
    """A v1 step accepts an optional ``mandatory: true`` flag."""
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "with-mandatory",
        "steps": [
            {
                "id": "first",
                "name": "First step",
                "request": {"method": "GET", "url": "https://example.com/a"},
                "assertions": [{"type": "http_status", "expected": 200}],
                "mandatory": True,
            }
        ],
    }
    manifest = parse_manifest(raw_manifest)
    assert manifest.steps[0].mandatory is True


@pytest.mark.unit
def test_parse_v1_step_mandatory_defaults_to_false() -> None:
    """When ``mandatory`` is omitted, the parsed step defaults to ``False``."""
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "no-mandatory",
        "steps": [
            {
                "id": "first",
                "name": "First step",
                "request": {"method": "GET", "url": "https://example.com/a"},
                "assertions": [{"type": "http_status", "expected": 200}],
            }
        ],
    }
    manifest = parse_manifest(raw_manifest)
    assert manifest.steps[0].mandatory is False


@pytest.mark.unit
def test_parse_v1_step_group_and_phase_default_values() -> None:
    """When omitted, v1 HTTP steps default to group=default and phase=execution."""
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "default-group-phase",
        "steps": [
            {
                "id": "first",
                "name": "First step",
                "request": {"method": "GET", "url": "https://example.com/a"},
                "assertions": [{"type": "http_status", "expected": 200}],
            }
        ],
    }
    manifest = parse_manifest(raw_manifest)
    step = cast("ManifestStep", manifest.steps[0])

    assert step.group == "default"
    assert step.phase == "execution"


@pytest.mark.unit
def test_parse_v1_step_accepts_explicit_group_and_setup_phase() -> None:
    """HTTP steps accept explicit scheduling metadata."""
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "explicit-group-phase",
        "steps": [
            {
                "id": "bootstrap",
                "name": "Bootstrap",
                "phase": "setup",
                "group": "bank_a",
                "request": {"method": "GET", "url": "https://example.com/a"},
                "assertions": [{"type": "http_status", "expected": 200}],
            }
        ],
    }
    manifest = parse_manifest(raw_manifest)
    step = cast("ManifestStep", manifest.steps[0])

    assert step.group == "bank_a"
    assert step.phase == "setup"


@pytest.mark.unit
def test_parse_v1_step_rejects_invalid_phase() -> None:
    """HTTP steps reject unsupported phase values."""
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "bad-phase",
        "steps": [
            {
                "id": "first",
                "name": "First",
                "phase": "parallel",
                "request": {"method": "GET", "url": "https://example.com/a"},
                "assertions": [{"type": "http_status", "expected": 200}],
            }
        ],
    }

    with pytest.raises(ManifestError, match=r"steps\[0\]\.phase must be one of: setup, execution"):
        parse_manifest(raw_manifest)


@pytest.mark.unit
def test_parse_v1_step_rejects_invalid_group() -> None:
    """HTTP steps reject group ids that violate the step-id character shape."""
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "bad-group",
        "steps": [
            {
                "id": "first",
                "name": "First",
                "group": "bad.group",
                "request": {"method": "GET", "url": "https://example.com/a"},
                "assertions": [{"type": "http_status", "expected": 200}],
            }
        ],
    }

    with pytest.raises(ManifestError, match=r"steps\[0\]\.group 'bad\.group' contains invalid characters"):
        parse_manifest(raw_manifest)


@pytest.mark.unit
@pytest.mark.parametrize("bad_value", [1, 0, "true", "false", None, []])
def test_parse_v1_step_mandatory_rejects_non_boolean(bad_value: JsonValue) -> None:
    """``mandatory`` must be a JSON boolean; truthy coercion is rejected."""
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "bad-mandatory",
        "steps": [
            {
                "id": "first",
                "name": "First step",
                "request": {"method": "GET", "url": "https://example.com/a"},
                "assertions": [{"type": "http_status", "expected": 200}],
                "mandatory": bad_value,
            }
        ],
    }
    with pytest.raises(ManifestError, match=r"steps\[0\]\.mandatory must be a JSON boolean"):
        parse_manifest(raw_manifest)


# --- v1 manifest parser tests: psu-authorization step ---


def valid_psu_step() -> dict[str, JsonValue]:
    """Return a minimally-valid raw PSU authorisation step entry."""
    return {
        "kind": "psu-authorization",
        "id": "psu",
        "name": "PSU authorisation",
        "mode": "manual",
        "authorizationEndpoint": "https://auth.example.com/authorize",
        "clientId": "synthetic-client-id-00000000",
        "redirectUri": "https://conformance.example.com/callback",
    }


def valid_psu_manifest() -> dict[str, JsonValue]:
    """Return a minimally-valid v1 manifest containing a single PSU step."""
    return {
        "schemaVersion": "v1",
        "name": "PSU authorisation only",
        "steps": [valid_psu_step()],
    }


@pytest.mark.unit
def test_parse_v1_manifest_loads_psu_authorization_example_file() -> None:
    """The bundled v1 PSU example manifest parses and exposes the PSU step."""
    manifest = load_manifest(PSU_EXAMPLE_MANIFEST_PATH)
    assert manifest.schema_version == "v1"
    assert len(manifest.steps) == 3
    psu_step = manifest.steps[1]
    assert isinstance(psu_step, PsuAuthorizationStep)
    assert psu_step.id == "psu-authorization"
    assert psu_step.mode == "manual"
    assert psu_step.mandatory is True


@pytest.mark.unit
def test_parse_v1_psu_step_applies_defaults() -> None:
    """Optional fields fall back to their documented defaults."""
    manifest = parse_manifest(valid_psu_manifest())
    psu_step = manifest.steps[0]
    assert isinstance(psu_step, PsuAuthorizationStep)
    assert psu_step.response_type == "code id_token"
    assert psu_step.scope == "openid"
    assert psu_step.state is None
    assert psu_step.request_object is None
    assert psu_step.timeout_seconds == 120
    assert psu_step.mandatory is False
    assert psu_step.optional is False
    assert psu_step.group == "default"
    assert psu_step.phase == "execution"


@pytest.mark.unit
def test_parse_v1_psu_step_accepts_all_fields() -> None:
    """Every optional PSU field round-trips when populated."""
    raw = valid_psu_manifest()
    step = cast("list[dict[str, JsonValue]]", raw["steps"])[0]
    step["responseType"] = "code"
    step["scope"] = "openid accounts"
    step["state"] = "x" * 64
    step["requestObject"] = "eyJhbGciOiJQUzI1NiJ9.synthetic.signature"
    step["timeoutSeconds"] = 60
    step["mandatory"] = True
    step["group"] = "consent"
    step["phase"] = "setup"
    manifest = parse_manifest(raw)
    psu_step = manifest.steps[0]
    assert isinstance(psu_step, PsuAuthorizationStep)
    assert psu_step.response_type == "code"
    assert psu_step.scope == "openid accounts"
    assert psu_step.state == "x" * 64
    assert psu_step.request_object == "eyJhbGciOiJQUzI1NiJ9.synthetic.signature"
    assert psu_step.timeout_seconds == 60
    assert psu_step.mandatory is True
    assert psu_step.group == "consent"
    assert psu_step.phase == "setup"


@pytest.mark.unit
def test_parse_v1_psu_step_rejects_invalid_phase() -> None:
    """PSU steps reject unsupported phase values."""
    raw = valid_psu_manifest()
    cast("list[dict[str, JsonValue]]", raw["steps"])[0]["phase"] = "parallel"

    with pytest.raises(ManifestError, match=r"steps\[0\]\.phase must be one of: setup, execution"):
        parse_manifest(raw)


@pytest.mark.unit
def test_parse_v1_psu_step_rejects_invalid_group() -> None:
    """PSU steps reject group ids that violate the step-id character shape."""
    raw = valid_psu_manifest()
    cast("list[dict[str, JsonValue]]", raw["steps"])[0]["group"] = "bad.group"

    with pytest.raises(ManifestError, match=r"steps\[0\]\.group 'bad\.group' contains invalid characters"):
        parse_manifest(raw)


@pytest.mark.unit
def test_parse_v1_step_kind_defaults_to_http() -> None:
    """A step without ``kind`` is parsed as a plain HTTP step."""
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "default-kind",
        "steps": [
            {
                "id": "first",
                "name": "First",
                "request": {"method": "GET", "url": "https://example.com/a"},
                "assertions": [{"type": "http_status", "expected": 200}],
            }
        ],
    }
    manifest = parse_manifest(raw_manifest)
    assert not isinstance(manifest.steps[0], PsuAuthorizationStep)


@pytest.mark.unit
def test_parse_v1_step_explicit_kind_http_is_accepted() -> None:
    """Setting ``"kind": "http"`` explicitly is equivalent to omitting it."""
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "explicit-http",
        "steps": [
            {
                "kind": "http",
                "id": "first",
                "name": "First",
                "request": {"method": "GET", "url": "https://example.com/a"},
                "assertions": [{"type": "http_status", "expected": 200}],
            }
        ],
    }
    manifest = parse_manifest(raw_manifest)
    assert not isinstance(manifest.steps[0], PsuAuthorizationStep)


@pytest.mark.unit
def test_parse_v1_step_rejects_unknown_kind() -> None:
    """Unknown ``kind`` values fail at parse time."""
    raw: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "bad-kind",
        "steps": [{"kind": "telepathy", "id": "first", "name": "first"}],
    }
    with pytest.raises(ManifestError, match=r"steps\[0\]\.kind must be one of"):
        parse_manifest(raw)


@pytest.mark.unit
def test_parse_v1_step_rejects_non_string_kind() -> None:
    """``kind`` must be a string when present."""
    raw: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "bad-kind-type",
        "steps": [{"kind": 7, "id": "first", "name": "first"}],
    }
    with pytest.raises(ManifestError, match=r"steps\[0\]\.kind must be a string"):
        parse_manifest(raw)


@pytest.mark.unit
def test_parse_v1_psu_step_rejects_request_field() -> None:
    """HTTP-only fields are rejected on a PSU step."""
    raw = valid_psu_manifest()
    cast("list[dict[str, JsonValue]]", raw["steps"])[0]["request"] = {
        "method": "GET",
        "url": "https://example.com/x",
    }
    with pytest.raises(ManifestError, match=r"Unknown steps\[0\] field\(s\): request"):
        parse_manifest(raw)


@pytest.mark.unit
def test_parse_v1_http_step_rejects_psu_fields() -> None:
    """PSU-only fields are rejected on an HTTP step."""
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "http-with-psu-field",
        "steps": [
            {
                "id": "first",
                "name": "First",
                "request": {"method": "GET", "url": "https://example.com/a"},
                "assertions": [{"type": "http_status", "expected": 200}],
                "clientId": "should-not-be-here",
            }
        ],
    }
    with pytest.raises(ManifestError, match=r"Unknown steps\[0\] field\(s\): clientId"):
        parse_manifest(raw_manifest)


@pytest.mark.unit
@pytest.mark.parametrize(
    "missing_key",
    ["id", "name", "mode", "authorizationEndpoint", "clientId", "redirectUri"],
)
def test_parse_v1_psu_step_rejects_missing_required_field(missing_key: str) -> None:
    """Each required PSU step field fails fast when omitted."""
    raw = valid_psu_manifest()
    step = cast("list[dict[str, JsonValue]]", raw["steps"])[0]
    del step[missing_key]
    with pytest.raises(ManifestError, match=re.escape(f"steps[0].{missing_key}")):
        parse_manifest(raw)


@pytest.mark.unit
def test_parse_v1_psu_step_rejects_unknown_mode() -> None:
    """``mode`` must be one of the supported literal values."""
    raw = valid_psu_manifest()
    cast("list[dict[str, JsonValue]]", raw["steps"])[0]["mode"] = "auto"
    with pytest.raises(ManifestError, match=r"steps\[0\]\.mode must be one of"):
        parse_manifest(raw)


@pytest.mark.unit
def test_parse_v1_psu_step_accepts_placeholder_in_authorization_endpoint() -> None:
    """Authorisation endpoint may be sourced from an earlier step's response."""
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "with-discovery",
        "steps": [
            {
                "id": "discovery",
                "name": "Discovery",
                "request": {"method": "GET", "url": "https://auth.example.com/.well-known/openid-configuration"},
                "assertions": [{"type": "http_status", "expected": 200}],
            },
            {
                "kind": "psu-authorization",
                "id": "psu",
                "name": "PSU",
                "mode": "headless",
                "authorizationEndpoint": "${steps.discovery.response.body.authorization_endpoint}",
                "clientId": "c",
                "redirectUri": "https://conformance.example.com/callback",
            },
        ],
    }
    manifest = parse_manifest(raw_manifest)
    psu_step = manifest.steps[1]
    assert isinstance(psu_step, PsuAuthorizationStep)
    assert psu_step.mode == "headless"


@pytest.mark.unit
def test_parse_v1_psu_step_accepts_config_redirect_uri_placeholder() -> None:
    """PSU redirectUri may use the narrow participant config placeholder."""
    raw = valid_psu_manifest()
    cast("list[dict[str, JsonValue]]", raw["steps"])[0]["redirectUri"] = "${config.oauth.redirectUri}"

    manifest = parse_manifest(raw)

    psu_step = manifest.steps[0]
    assert isinstance(psu_step, PsuAuthorizationStep)
    assert psu_step.redirect_uri == "${config.oauth.redirectUri}"


@pytest.mark.unit
def test_parse_v1_psu_step_rejects_non_https_authorization_endpoint() -> None:
    """Literal authorisation endpoints are HTTPS-validated at parse time."""
    raw = valid_psu_manifest()
    cast("list[dict[str, JsonValue]]", raw["steps"])[0]["authorizationEndpoint"] = "http://auth.example.com/authorize"
    with pytest.raises(ManifestError, match=r"steps\[0\]\.authorizationEndpoint"):
        parse_manifest(raw)


@pytest.mark.unit
def test_parse_v1_psu_step_rejects_unsupported_placeholder_in_redirect_uri() -> None:
    """Redirect URI only permits the narrow participant config placeholder."""
    raw = valid_psu_manifest()
    cast("list[dict[str, JsonValue]]", raw["steps"])[0]["redirectUri"] = "${config.discoveryUrl}"
    with pytest.raises(ManifestError, match=r"steps\[0\]\.redirectUri may only use"):
        parse_manifest(raw)


@pytest.mark.unit
def test_parse_v1_psu_step_rejects_placeholder_in_response_type() -> None:
    """responseType is a static FAPI-defined value — placeholders are rejected at parse time."""
    raw = valid_psu_manifest()
    cast("list[dict[str, JsonValue]]", raw["steps"])[0]["responseType"] = "${steps.x.response.body.type}"
    with pytest.raises(ManifestError, match=r"steps\[0\]\.responseType must not contain placeholders"):
        parse_manifest(raw)


@pytest.mark.unit
def test_parse_v1_psu_step_rejects_placeholder_in_scope() -> None:
    """scope is a static consent declaration — placeholders are rejected at parse time."""
    raw = valid_psu_manifest()
    cast("list[dict[str, JsonValue]]", raw["steps"])[0]["scope"] = "${steps.x.response.body.scope}"
    with pytest.raises(ManifestError, match=r"steps\[0\]\.scope must not contain placeholders"):
        parse_manifest(raw)


@pytest.mark.unit
def test_parse_v1_psu_step_rejects_non_https_redirect_uri() -> None:
    """Redirect URI is HTTPS-validated at parse time."""
    raw = valid_psu_manifest()
    cast("list[dict[str, JsonValue]]", raw["steps"])[0]["redirectUri"] = "http://conformance.example.com/callback"
    with pytest.raises(ManifestError, match=r"steps\[0\]\.redirectUri"):
        parse_manifest(raw)


@pytest.mark.unit
def test_parse_v1_psu_step_rejects_short_literal_state() -> None:
    """Literal state values shorter than 32 characters are rejected at parse time."""
    raw = valid_psu_manifest()
    cast("list[dict[str, JsonValue]]", raw["steps"])[0]["state"] = "too-short"
    with pytest.raises(ManifestError, match=r"steps\[0\]\.state must be at least 32 characters"):
        parse_manifest(raw)


@pytest.mark.unit
def test_parse_v1_psu_step_accepts_placeholder_state_below_min_length() -> None:
    """A placeholder-bearing state is exempt from the parse-time length check."""
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "with-state-placeholder",
        "steps": [
            {
                "id": "make-state",
                "name": "Make state",
                "request": {"method": "GET", "url": "https://example.com/state"},
                "assertions": [{"type": "http_status", "expected": 200}],
            },
            {
                "kind": "psu-authorization",
                "id": "psu",
                "name": "PSU",
                "mode": "manual",
                "authorizationEndpoint": "https://auth.example.com/authorize",
                "clientId": "c",
                "redirectUri": "https://conformance.example.com/callback",
                "state": "${steps.make-state.response.body.value}",
            },
        ],
    }
    manifest = parse_manifest(raw_manifest)
    psu_step = manifest.steps[1]
    assert isinstance(psu_step, PsuAuthorizationStep)
    assert psu_step.state == "${steps.make-state.response.body.value}"


@pytest.mark.unit
def test_parse_v1_psu_step_rejects_empty_request_object() -> None:
    """Empty/whitespace request_object is rejected (omit instead)."""
    raw = valid_psu_manifest()
    cast("list[dict[str, JsonValue]]", raw["steps"])[0]["requestObject"] = "   "
    with pytest.raises(ManifestError, match=r"steps\[0\]\.requestObject must be a non-empty string"):
        parse_manifest(raw)


@pytest.mark.unit
@pytest.mark.parametrize("bad_value", [0, -1, 601, 1000])
def test_parse_v1_psu_step_rejects_out_of_range_timeout(bad_value: int) -> None:
    """Timeout must be within the documented 1..600 second range."""
    raw = valid_psu_manifest()
    cast("list[dict[str, JsonValue]]", raw["steps"])[0]["timeoutSeconds"] = bad_value
    with pytest.raises(ManifestError, match=r"steps\[0\]\.timeoutSeconds must be between"):
        parse_manifest(raw)


@pytest.mark.unit
@pytest.mark.parametrize("bad_value", [True, False, "30", 1.5, None])
def test_parse_v1_psu_step_rejects_non_integer_timeout(bad_value: JsonValue) -> None:
    """Timeout must be a JSON integer; booleans/strings/floats are rejected."""
    raw = valid_psu_manifest()
    cast("list[dict[str, JsonValue]]", raw["steps"])[0]["timeoutSeconds"] = bad_value
    with pytest.raises(ManifestError, match=r"steps\[0\]\.timeoutSeconds must be a JSON integer"):
        parse_manifest(raw)


@pytest.mark.unit
def test_parse_v1_psu_step_rejects_mandatory_and_optional_both_true() -> None:
    """``mandatory`` and ``optional`` are mutually exclusive on a PSU step."""
    raw = valid_psu_manifest()
    step = cast("list[dict[str, JsonValue]]", raw["steps"])[0]
    step["mandatory"] = True
    step["optional"] = True
    with pytest.raises(ManifestError, match=r"steps\[0\]: 'mandatory' and 'optional' must not both be true"):
        parse_manifest(raw)


@pytest.mark.unit
def test_parse_v1_psu_step_rejects_unknown_key() -> None:
    """Unknown top-level keys on a PSU step fail fast."""
    raw = valid_psu_manifest()
    cast("list[dict[str, JsonValue]]", raw["steps"])[0]["nonsense"] = True
    with pytest.raises(ManifestError, match=r"Unknown steps\[0\] field\(s\): nonsense"):
        parse_manifest(raw)


@pytest.mark.unit
def test_parse_v1_psu_step_rejects_duplicate_id() -> None:
    """A PSU step id collision with an earlier step is rejected."""
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "dup-id",
        "steps": [
            {
                "id": "shared",
                "name": "First",
                "request": {"method": "GET", "url": "https://example.com/a"},
                "assertions": [{"type": "http_status", "expected": 200}],
            },
            {
                "kind": "psu-authorization",
                "id": "shared",
                "name": "PSU",
                "mode": "manual",
                "authorizationEndpoint": "https://auth.example.com/authorize",
                "clientId": "c",
                "redirectUri": "https://conformance.example.com/callback",
            },
        ],
    }
    with pytest.raises(ManifestError, match=r"steps\[1\]\.id 'shared' is a duplicate"):
        parse_manifest(raw_manifest)
