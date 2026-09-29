"""Parsing of the v1 assertion vocabulary and bundled response-schema assertions."""

from typing import cast

import pytest

from conformance.json_types import JsonValue
from conformance.manifest import (
    ManifestError,
    ManifestStep,
    ResponseSchemaAssertion,
    parse_manifest,
)
from tests.support.manifest_documents import v1_manifest, v1_single_step_manifest

pytestmark = pytest.mark.unit


def test_parse_v1_manifest_accepts_extended_assertion_vocabulary() -> None:
    raw_manifest = v1_manifest()
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
        {
            "type": "response_schema",
            "source": "bundled_openapi",
            "document": "ob-read-write-v4.0-account-info-openapi",
            "schemaRef": "#/components/schemas/OBReadAccount6",
        },
    ]

    manifest = parse_manifest(raw_manifest)

    parsed_step = cast("ManifestStep", manifest.steps[0])
    assert len(parsed_step.assertions) == 18
    assert parsed_step.assertions[13].type == "header"
    assert parsed_step.assertions[17].type == "response_schema"


def test_parse_v1_manifest_accepts_response_schema_assertion_with_schema_ref() -> None:
    raw_manifest = v1_manifest()
    step = cast("dict[str, JsonValue]", cast("list[JsonValue]", raw_manifest["steps"])[0])
    step["assertions"] = [
        {
            "type": "response_schema",
            "source": "bundled_openapi",
            "document": "ob-read-write-v4.0-account-info-openapi",
            "schemaRef": "#/components/schemas/OBReadAccount6",
            "bodyPath": "Data.Account",
        }
    ]

    manifest = parse_manifest(raw_manifest)

    assertion = cast("ResponseSchemaAssertion", cast("ManifestStep", manifest.steps[0]).assertions[0])
    assert assertion.type == "response_schema"
    assert assertion.source == "bundled_openapi"
    assert assertion.document == "ob-read-write-v4.0-account-info-openapi"
    assert assertion.schema_ref == "#/components/schemas/OBReadAccount6"
    assert assertion.body_path == "Data.Account"
    assert assertion.schema is None


def test_parse_v1_manifest_accepts_response_schema_assertion_with_inline_schema() -> None:
    raw_manifest = v1_manifest()
    step = cast("dict[str, JsonValue]", cast("list[JsonValue]", raw_manifest["steps"])[0])
    step["assertions"] = [
        {
            "type": "response_schema",
            "source": "bundled_openapi",
            "document": "ob-read-write-v4.0-account-info-openapi",
            "schema": {
                "type": "object",
                "required": ["Data"],
            },
        }
    ]

    manifest = parse_manifest(raw_manifest)

    assertion = cast("ResponseSchemaAssertion", cast("ManifestStep", manifest.steps[0]).assertions[0])
    assert assertion.type == "response_schema"
    assert assertion.schema_ref is None
    assert assertion.schema is not None


@pytest.mark.parametrize(
    ("raw_assertion", "message"),
    [
        (
            {
                "type": "response_schema",
                "source": "external_url",
                "document": "ob-read-write-v4.0-account-info-openapi",
                "schemaRef": "#/components/schemas/OBReadAccount6",
            },
            r"steps\[0\]\.assertions\[0\]\.source must be one of: bundled_openapi",
        ),
        (
            {
                "type": "response_schema",
                "source": "bundled_openapi",
                "document": "ob-read-write-v9.9-account-info-openapi",
                "schemaRef": "#/components/schemas/OBReadAccount6",
            },
            (
                r"steps\[0\]\.assertions\[0\]\.document must be one of: "
                r"ob-read-write-v3\.1\.11-account-info-openapi, "
                r"ob-read-write-v3\.1\.11-confirmation-funds-openapi, "
                r"ob-read-write-v3\.1\.11-payment-initiation-openapi, "
                r"ob-read-write-v3\.1\.11-vrp-openapi, "
                r"ob-read-write-v4\.0-account-info-openapi, "
                r"ob-read-write-v4\.0-payment-initiation-openapi, "
                r"ob-read-write-v4\.0\.1-account-info-openapi, "
                r"ob-read-write-v4\.0\.1-payment-initiation-openapi"
            ),
        ),
        (
            {
                "type": "response_schema",
                "source": "bundled_openapi",
                "document": "ob-read-write-v4.0-account-info-openapi",
            },
            r"steps\[0\]\.assertions\[0\] must provide exactly one of schemaRef or schema",
        ),
        (
            {
                "type": "response_schema",
                "source": "bundled_openapi",
                "document": "ob-read-write-v4.0-account-info-openapi",
                "schemaRef": "#/components/schemas/OBReadAccount6",
                "schema": {"type": "object"},
            },
            r"steps\[0\]\.assertions\[0\] must provide exactly one of schemaRef or schema",
        ),
        (
            {
                "type": "response_schema",
                "source": "bundled_openapi",
                "document": "ob-read-write-v4.0-account-info-openapi",
                "schemaRef": "#/components/schemas/OBReadAccount6",
                "extra": "bad",
            },
            r"Unknown steps\[0\]\.assertions\[0\] field\(s\): extra",
        ),
    ],
)
def test_parse_v1_manifest_rejects_invalid_response_schema_assertion_shape(
    raw_assertion: dict[str, JsonValue],
    message: str,
) -> None:
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "Response schema assertion validation",
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


BUNDLED_RESPONSE_SCHEMA_DOCUMENTS = (
    pytest.param(
        "ob-read-write-v4.0.1-account-info-openapi",
        "#/components/schemas/OBReadAccount6",
        id="account-info-v4.0.1",
    ),
    pytest.param(
        "ob-read-write-v4.0-payment-initiation-openapi",
        "#/components/schemas/OBWriteDomesticStandingOrderResponse6",
        id="payment-initiation-v4.0",
    ),
    pytest.param(
        "ob-read-write-v4.0.1-payment-initiation-openapi",
        "#/components/schemas/OBWriteDomesticStandingOrderResponse6",
        id="payment-initiation-v4.0.1",
    ),
)
"""Bundled Open Banking OpenAPI documents a response-schema assertion may target."""


@pytest.mark.parametrize(("document", "schema_ref"), BUNDLED_RESPONSE_SCHEMA_DOCUMENTS)
def test_parse_v1_manifest_accepts_bundled_response_schema_document(document: str, schema_ref: str) -> None:
    """Response-schema assertions resolve against each bundled OpenAPI document.

    Args:
        document: Bundled Open Banking OpenAPI document identifier under test.
        schema_ref: JSON pointer into that document's component schemas.
    """
    assertion: JsonValue = {
        "type": "response_schema",
        "source": "bundled_openapi",
        "document": document,
        "schemaRef": schema_ref,
    }

    manifest = parse_manifest(
        v1_single_step_manifest(
            request={"method": "GET", "url": "https://example.com/resource"},
            assertions=[assertion],
        )
    )

    parsed = cast("ResponseSchemaAssertion", cast("ManifestStep", manifest.steps[0]).assertions[0])
    assert parsed.document == document
    assert parsed.schema_ref == schema_ref
