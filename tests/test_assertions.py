from typing import cast

import pytest

from conformance.assertions import evaluate_assertion
from conformance.json_types import JsonValue
from conformance.manifest import (
    HttpStatusAssertion,
    JsonFieldAssertion,
    ManifestAssertion,
    ManifestStep,
    parse_manifest,
)


def parsed_assertion(raw_assertion: dict[str, JsonValue]) -> ManifestAssertion:
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "Assertion contract",
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

    manifest = parse_manifest(raw_manifest)
    return cast("ManifestStep", manifest.steps[0]).assertions[0]


@pytest.mark.unit
def test_evaluate_http_status_passes_when_status_matches() -> None:
    result = evaluate_assertion(
        HttpStatusAssertion(type="http_status", expected=200),
        status_code=200,
        body={},
    )

    assert result.passed is True

    assert result.message == "HTTP status was 200"


@pytest.mark.unit
def test_evaluate_http_status_fails_when_status_differs() -> None:
    result = evaluate_assertion(
        HttpStatusAssertion(type="http_status", expected=200),
        status_code=201,
        body={},
    )

    assert result.passed is False
    assert result.message == "Expected HTTP status 200, got 201"


@pytest.mark.unit
def test_evaluate_required_json_field_passes_when_field_is_present() -> None:
    result = evaluate_assertion(
        JsonFieldAssertion(type="json_field", path="issuer", rule="required"),
        status_code=200,
        body={"issuer": None},
    )

    assert result.passed is True
    assert result.message == "JSON field issuer is present"


@pytest.mark.unit
def test_evaluate_required_json_field_fails_when_field_is_missing() -> None:
    result = evaluate_assertion(
        JsonFieldAssertion(type="json_field", path="issuer", rule="required"),
        status_code=200,
        body={},
    )

    assert result.passed is False
    assert result.message == "JSON field issuer is missing"


@pytest.mark.unit
def test_evaluate_https_url_json_field_passes_for_https_url() -> None:
    result = evaluate_assertion(
        JsonFieldAssertion(type="json_field", path="jwks_uri", rule="https_url"),
        status_code=200,
        body={"jwks_uri": "https://modelbank.example.com/jwks"},
    )

    assert result.passed is True
    assert result.message == "JSON field jwks_uri is an HTTPS URL"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("value", "message"),
    [
        ("http://modelbank.example.com/jwks", "JSON field jwks_uri must be an HTTPS URL"),
        ("https://client@modelbank.example.com/jwks", "JSON field jwks_uri must not include credentials"),
        ("https://127.0.0.1/jwks", "JSON field jwks_uri must use a DNS hostname, not an IP literal"),
        (42, "JSON field jwks_uri must be a non-empty HTTPS URL string"),
    ],
)
def test_evaluate_https_url_json_field_fails_for_unsafe_values(value: JsonValue, message: str) -> None:
    result = evaluate_assertion(
        JsonFieldAssertion(type="json_field", path="jwks_uri", rule="https_url"),
        status_code=200,
        body={"jwks_uri": value},
    )

    assert result.passed is False
    assert result.message == message


@pytest.mark.unit
def test_evaluate_array_json_field_passes_for_array() -> None:
    result = evaluate_assertion(
        JsonFieldAssertion(type="json_field", path="keys", rule="array"),
        status_code=200,
        body={"keys": []},
    )

    assert result.passed is True
    assert result.message == "JSON field keys is an array"


@pytest.mark.unit
def test_evaluate_array_json_field_fails_for_non_array() -> None:
    result = evaluate_assertion(
        JsonFieldAssertion(type="json_field", path="keys", rule="array"),
        status_code=200,
        body={"keys": {}},
    )

    assert result.passed is False
    assert result.message == "JSON field keys must be an array"


@pytest.mark.unit
def test_evaluate_json_field_resolves_dot_paths() -> None:
    result = evaluate_assertion(
        JsonFieldAssertion(type="json_field", path="metadata.issuer", rule="required"),
        status_code=200,
        body={"metadata": {"issuer": "https://modelbank.example.com"}},
    )

    assert result.passed is True


@pytest.mark.unit
def test_evaluate_absent_json_field_passes_when_field_is_missing() -> None:
    assertion = parsed_assertion({"type": "json_field", "path": "issuer", "rule": "absent"})

    result = evaluate_assertion(
        assertion,
        status_code=200,
        body={},
    )

    assert result.passed is True
    assert result.message == "JSON field issuer is absent"


@pytest.mark.unit
def test_evaluate_absent_json_field_fails_when_field_is_present() -> None:
    assertion = parsed_assertion({"type": "json_field", "path": "issuer", "rule": "absent"})

    result = evaluate_assertion(
        assertion,
        status_code=200,
        body={"issuer": "https://modelbank.example.com"},
    )

    assert result.passed is False
    assert result.message == "JSON field issuer must be absent"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("rule", "body", "message"),
    [
        ("string", {"issuer": "https://modelbank.example.com"}, "JSON field issuer is a string"),
        ("number", {"count": 2}, "JSON field count is a number"),
        ("boolean", {"enabled": True}, "JSON field enabled is a boolean"),
        ("object", {"metadata": {}}, "JSON field metadata is an object"),
        ("non_empty_array", {"keys": [{"kid": "one"}]}, "JSON field keys is a non-empty array"),
    ],
)
def test_evaluate_json_field_type_rules_pass_for_matching_values(
    rule: str,
    body: dict[str, JsonValue],
    message: str,
) -> None:
    path = next(iter(body))
    assertion = parsed_assertion({"type": "json_field", "path": path, "rule": rule})

    result = evaluate_assertion(
        assertion,
        status_code=200,
        body=body,
    )

    assert result.passed is True
    assert result.message == message


@pytest.mark.unit
@pytest.mark.parametrize(
    ("raw_assertion", "body", "message"),
    [
        (
            {"type": "json_field", "path": "issuer", "rule": "string"},
            {"issuer": 42},
            "JSON field issuer must be a string",
        ),
        (
            {"type": "json_field", "path": "count", "rule": "number"},
            {"count": True},
            "JSON field count must be a number",
        ),
        (
            {"type": "json_field", "path": "enabled", "rule": "boolean"},
            {"enabled": 1},
            "JSON field enabled must be a boolean",
        ),
        (
            {"type": "json_field", "path": "metadata", "rule": "object"},
            {"metadata": []},
            "JSON field metadata must be an object",
        ),
        (
            {"type": "json_field", "path": "keys", "rule": "non_empty_array"},
            {"keys": []},
            "JSON field keys must be a non-empty array",
        ),
    ],
)
def test_evaluate_json_field_type_rules_fail_for_wrong_values(
    raw_assertion: dict[str, JsonValue],
    body: dict[str, JsonValue],
    message: str,
) -> None:
    assertion = parsed_assertion(raw_assertion)

    result = evaluate_assertion(
        assertion,
        status_code=200,
        body=body,
    )

    assert result.passed is False
    assert result.message == message


@pytest.mark.unit
def test_evaluate_min_items_json_field_uses_numeric_threshold() -> None:
    assertion = parsed_assertion({"type": "json_field", "path": "keys", "rule": "min_items", "minItems": 2})

    result = evaluate_assertion(
        assertion,
        status_code=200,
        body={"keys": [{"kid": "one"}]},
    )

    assert result.passed is False
    assert result.message == "JSON field keys must contain at least 2 items"


@pytest.mark.unit
def test_evaluate_equals_json_field_compares_json_value_without_coercion() -> None:
    assertion = parsed_assertion({"type": "json_field", "path": "expires_in", "rule": "equals", "value": 300})

    result = evaluate_assertion(
        assertion,
        status_code=200,
        body={"expires_in": "300"},
    )

    assert result.passed is False
    assert result.message == "JSON field expires_in must equal 300"


@pytest.mark.unit
def test_evaluate_one_of_json_field_checks_json_compatible_candidates() -> None:
    assertion = parsed_assertion(
        {
            "type": "json_field",
            "path": "token_endpoint_auth_method",
            "rule": "one_of",
            "values": ["private_key_jwt", "tls_client_auth"],
        }
    )

    result = evaluate_assertion(
        assertion,
        status_code=200,
        body={"token_endpoint_auth_method": "client_secret_basic"},
    )

    assert result.passed is False
    assert result.message == (
        "JSON field token_endpoint_auth_method must equal one of: private_key_jwt, tls_client_auth"
    )


@pytest.mark.unit
def test_evaluate_header_present_is_case_insensitive() -> None:
    assertion = parsed_assertion({"type": "header", "name": "content-type", "rule": "present"})

    result = evaluate_assertion(
        assertion,
        status_code=200,
        headers={"Content-Type": "application/json"},
        body={},
    )

    assert result.passed is True
    assert result.message == "Header content-type is present"


@pytest.mark.unit
def test_evaluate_header_equals_uses_case_insensitive_name_lookup() -> None:
    assertion = parsed_assertion(
        {"type": "header", "name": "x-fapi-interaction-id", "rule": "equals", "value": "abc123"}
    )

    result = evaluate_assertion(
        assertion,
        status_code=200,
        headers={"X-FAPI-Interaction-Id": "def456"},
        body={},
    )

    assert result.passed is False
    assert result.message == "Header x-fapi-interaction-id must equal the expected value"


@pytest.mark.unit
def test_evaluate_header_contains_checks_substring() -> None:
    assertion = parsed_assertion({"type": "header", "name": "cache-control", "rule": "contains", "value": "no-store"})

    result = evaluate_assertion(
        assertion,
        status_code=200,
        headers={"Cache-Control": "private"},
        body={},
    )

    assert result.passed is False
    assert result.message == "Header cache-control must contain the expected value"
