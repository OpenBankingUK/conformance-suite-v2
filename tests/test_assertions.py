from typing import cast

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from joserfc import jwk, jws

from conformance.assertions import evaluate_assertion
from conformance.json_types import JsonObject, JsonValue
from conformance.manifest import (
    HeaderAssertion,
    HttpStatusAssertion,
    JsonFieldAssertion,
    ManifestAssertion,
    ManifestStep,
    ObErrorCodeAssertion,
    ResponseSchemaAssertion,
    parse_manifest,
)


def _signed_response_fixture(payload: bytes) -> tuple[str, dict[str, JsonValue]]:
    """Build a detached response signature and matching JWKS fixture.

    Args:
        payload: Exact payload bytes to sign.

    Returns:
        Tuple of detached compact JWS and public JWKS body.
    """
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_key_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    signing_key = jwk.import_key(private_key_pem, key_type="RSA")
    public_jwk = cast(dict[str, JsonValue], signing_key.as_dict(is_private=False))
    public_jwk["kid"] = "aspsp-signing-key"
    protected = {
        "alg": "PS256",
        "kid": "aspsp-signing-key",
        "typ": "JOSE",
        "cty": "application/json",
    }
    compact_jws = jws.serialize_compact(protected, payload, signing_key, algorithms=["PS256"])
    return jws.detach_content(compact_jws), {"keys": [public_jwk]}


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
def test_evaluate_ob_error_code_assertion_passes_when_code_matches() -> None:
    """ob_error_code should pass when the response includes any acceptable ErrorCode."""
    result = evaluate_assertion(
        ObErrorCodeAssertion(
            type="ob_error_code",
            codes=("UK.OBIE.Signature.Invalid", "UK.OBIE.Signature.Missing"),
        ),
        status_code=400,
        body={"Errors": [{"ErrorCode": "UK.OBIE.Signature.Invalid"}]},
    )

    assert result.passed is True


@pytest.mark.unit
def test_evaluate_ob_error_code_assertion_fails_when_no_match() -> None:
    """ob_error_code should fail when none of the response ErrorCode values match."""
    result = evaluate_assertion(
        ObErrorCodeAssertion(
            type="ob_error_code",
            codes=("UK.OBIE.Signature.Invalid", "UK.OBIE.Signature.Missing"),
        ),
        status_code=400,
        body={"Errors": [{"ErrorCode": "UK.OBIE.SomeOtherCode"}]},
    )

    assert result.passed is False
    assert "expected one of" in result.message
    assert "UK.OBIE.Signature.Invalid" in result.message


@pytest.mark.unit
def test_evaluate_ob_error_code_assertion_fails_when_errors_absent() -> None:
    """ob_error_code should fail when the response body has no Errors array."""
    result = evaluate_assertion(
        ObErrorCodeAssertion(
            type="ob_error_code",
            codes=("UK.OBIE.Signature.Invalid", "UK.OBIE.Signature.Missing"),
        ),
        status_code=400,
        body={"Code": "400"},
    )

    assert result.passed is False


@pytest.mark.unit
def test_evaluate_ob_error_code_assertion_fails_on_non_object_body() -> None:
    """ob_error_code should fail when the response body is not a JSON object."""
    result = evaluate_assertion(
        ObErrorCodeAssertion(
            type="ob_error_code",
            codes=("UK.OBIE.Signature.Invalid", "UK.OBIE.Signature.Missing"),
        ),
        status_code=400,
        body=cast(JsonObject, "not a dict"),
    )

    assert result.passed is False


@pytest.mark.unit
def test_evaluate_ob_error_code_assertion_passes_on_second_matching_error() -> None:
    """ob_error_code should pass when a later Errors item contains a matching code."""
    result = evaluate_assertion(
        ObErrorCodeAssertion(
            type="ob_error_code",
            codes=("UK.OBIE.Signature.Invalid", "UK.OBIE.Signature.Missing"),
        ),
        status_code=400,
        body={
            "Errors": [
                {"ErrorCode": "UK.OBIE.Other"},
                {"ErrorCode": "UK.OBIE.Signature.Missing"},
            ]
        },
    )

    assert result.passed is True


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
def test_evaluate_all_items_absent_field_passes_when_all_items_omit_field() -> None:
    assertion = parsed_assertion(
        {"type": "json_field", "path": "Data.Transaction", "rule": "all_items_absent_field", "field": "Balance"}
    )

    result = evaluate_assertion(
        assertion,
        status_code=200,
        body={"Data": {"Transaction": [{"Amount": {"Amount": "1.00"}}, {"Status": "Booked"}]}},
    )

    assert result.passed is True
    assert result.message == "Every item in JSON field Data.Transaction omits field Balance"


@pytest.mark.unit
def test_evaluate_all_items_absent_field_passes_for_empty_array() -> None:
    assertion = parsed_assertion(
        {"type": "json_field", "path": "Data.Transaction", "rule": "all_items_absent_field", "field": "Balance"}
    )

    result = evaluate_assertion(
        assertion,
        status_code=200,
        body={"Data": {"Transaction": []}},
    )

    assert result.passed is True
    assert result.message == "Every item in JSON field Data.Transaction omits field Balance"


@pytest.mark.unit
def test_evaluate_all_items_absent_field_fails_when_first_item_contains_field() -> None:
    assertion = parsed_assertion(
        {"type": "json_field", "path": "Data.Transaction", "rule": "all_items_absent_field", "field": "Balance"}
    )

    result = evaluate_assertion(
        assertion,
        status_code=200,
        body={"Data": {"Transaction": [{"Balance": {}}, {"Status": "Booked"}]}},
    )

    assert result.passed is False
    assert result.message == "Every item in JSON field Data.Transaction must omit field Balance"


@pytest.mark.unit
def test_evaluate_all_items_absent_field_fails_when_non_first_item_contains_field() -> None:
    assertion = parsed_assertion(
        {"type": "json_field", "path": "Data.Transaction", "rule": "all_items_absent_field", "field": "Balance"}
    )

    result = evaluate_assertion(
        assertion,
        status_code=200,
        body={"Data": {"Transaction": [{"Status": "Booked"}, {"Balance": {}}]}},
    )

    assert result.passed is False
    assert result.message == "Every item in JSON field Data.Transaction must omit field Balance"


@pytest.mark.unit
def test_evaluate_all_items_absent_field_fails_when_path_is_not_an_array() -> None:
    assertion = parsed_assertion(
        {"type": "json_field", "path": "Data.Transaction", "rule": "all_items_absent_field", "field": "Balance"}
    )

    result = evaluate_assertion(
        assertion,
        status_code=200,
        body={"Data": {"Transaction": {}}},
    )

    assert result.passed is False
    assert result.message == "JSON field Data.Transaction must be an array"


@pytest.mark.unit
def test_evaluate_all_items_absent_field_fails_when_forbidden_field_name_is_blank() -> None:
    assertion = JsonFieldAssertion(type="json_field", path="Data.Transaction", rule="all_items_absent_field", field="")

    result = evaluate_assertion(
        assertion,
        status_code=200,
        body={"Data": {"Transaction": []}},
    )

    assert result.passed is False
    assert result.message == "JSON field Data.Transaction has an invalid forbidden item field"


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
@pytest.mark.parametrize(
    ("expected", "actual", "message"),
    [
        (True, 1, "JSON field x must equal true"),
        (1, True, "JSON field x must equal 1"),
        (False, 0, "JSON field x must equal false"),
        (0, False, "JSON field x must equal 0"),
    ],
)
def test_evaluate_equals_json_field_distinguishes_booleans_from_numbers(
    expected: JsonValue,
    actual: JsonValue,
    message: str,
) -> None:
    assertion = parsed_assertion({"type": "json_field", "path": "x", "rule": "equals", "value": expected})

    result = evaluate_assertion(
        assertion,
        status_code=200,
        body={"x": actual},
    )

    assert result.passed is False
    assert result.message == message


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
def test_evaluate_one_of_json_field_distinguishes_boolean_and_numeric_candidates() -> None:
    assertion = parsed_assertion(
        {
            "type": "json_field",
            "path": "x",
            "rule": "one_of",
            "values": [True, 2],
        }
    )

    result = evaluate_assertion(
        assertion,
        status_code=200,
        body={"x": 1},
    )

    assert result.passed is False
    assert result.message == "JSON field x must equal one of: true, 2"


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


@pytest.mark.unit
def test_evaluate_response_schema_passes_for_valid_bundled_openapi_payload() -> None:
    assertion = parsed_assertion(
        {
            "type": "response_schema",
            "source": "bundled_openapi",
            "document": "ob-read-write-v4.0-account-info-openapi",
            "schemaRef": "#/components/schemas/OBReadAccount6",
        }
    )

    result = evaluate_assertion(
        assertion,
        status_code=200,
        body={
            "Data": {"Account": [{"AccountId": "account-123"}]},
            "Links": {"Self": "https://api.example.com/accounts"},
            "Meta": {},
        },
    )

    assert result.passed is True
    assert result.message == (
        "Response body matches schema #/components/schemas/OBReadAccount6 from ob-read-write-v4.0-account-info-openapi"
    )


@pytest.mark.unit
def test_evaluate_response_signature_passes_for_valid_detached_jws() -> None:
    """response_signature assertions verify the response body against JWKS."""
    payload = b'{"Data":{"ConsentId":"consent-123"},"Risk":{}}'
    signature, jwks_body = _signed_response_fixture(payload)
    assertion = parsed_assertion({"type": "response_signature", "jwksStepId": "jwks-fetch"})

    result = evaluate_assertion(
        assertion,
        status_code=201,
        headers={"x-jws-signature": signature},
        body={"Data": {"ConsentId": "consent-123"}, "Risk": {}},
        body_bytes=payload,
        response_signature_jwks={"jwks-fetch": jwks_body},
    )

    assert result.passed is True


@pytest.mark.unit
def test_evaluate_response_signature_fails_when_payload_is_tampered() -> None:
    """response_signature assertions fail when raw response bytes differ."""
    payload = b'{"Data":{"ConsentId":"consent-123"},"Risk":{}}'
    signature, jwks_body = _signed_response_fixture(payload)
    assertion = parsed_assertion({"type": "response_signature", "jwksStepId": "jwks-fetch"})

    result = evaluate_assertion(
        assertion,
        status_code=201,
        headers={"x-jws-signature": signature},
        body={"Data": {"ConsentId": "consent-456"}, "Risk": {}},
        body_bytes=b'{"Data":{"ConsentId":"consent-456"},"Risk":{}}',
        response_signature_jwks={"jwks-fetch": jwks_body},
    )

    assert result.passed is False
    assert "response x-jws-signature verification failed" in result.message


@pytest.mark.unit
def test_evaluate_response_schema_passes_for_v4_pis_payment_initiation_payload() -> None:
    """Bundled v4 PIS schema assertions should validate representative consent responses."""
    assertion = parsed_assertion(
        {
            "type": "response_schema",
            "source": "bundled_openapi",
            "document": "ob-read-write-v4.0-payment-initiation-openapi",
            "schemaRef": "#/components/schemas/OBWriteDomesticConsentResponse5",
        }
    )

    result = evaluate_assertion(
        assertion,
        status_code=201,
        body={
            "Data": {
                "ConsentId": "consent-123",
                "CreationDateTime": "2026-06-17T15:00:00+00:00",
                "Status": "AWAU",
                "StatusUpdateDateTime": "2026-06-17T15:00:00+00:00",
                "Initiation": {
                    "InstructionIdentification": "instr-123",
                    "EndToEndIdentification": "e2e-123",
                    "InstructedAmount": {"Amount": "10.00", "Currency": "GBP"},
                    "CreditorAccount": {
                        "SchemeName": "UK.OBIE.SortCodeAccountNumber",
                        "Identification": "12345612345678",
                        "Name": "Receiver",
                    },
                },
            },
            "Risk": {},
            "Links": {"Self": "https://api.example.com/open-banking/v4.0/pisp/domestic-payment-consents/consent-123"},
            "Meta": {},
        },
    )

    assert result.passed is True


@pytest.mark.unit
@pytest.mark.parametrize(
    "document",
    [
        "ob-read-write-v4.0-account-info-openapi",
        "ob-read-write-v4.0.1-account-info-openapi",
    ],
)
def test_evaluate_response_schema_accepts_namespaced_account_identification_code(document: str) -> None:
    """Bundled v4 Account schemas accept the Standards namespaced account scheme codes.

    Args:
        document: Bundled OpenAPI document identifier under test.
    """
    assertion = parsed_assertion(
        {
            "type": "response_schema",
            "source": "bundled_openapi",
            "document": document,
            "schemaRef": "#/components/schemas/OBReadAccount6",
        }
    )

    result = evaluate_assertion(
        assertion,
        status_code=200,
        body={
            "Data": {
                "Account": [
                    {
                        "AccountId": "account-123",
                        "Account": [
                            {
                                "SchemeName": "UK.OBIE.SortCodeAccountNumber",
                                "Identification": "12345612345678",
                            }
                        ],
                    }
                ]
            },
            "Links": {"Self": "https://api.example.com/accounts"},
            "Meta": {},
        },
    )

    assert result.passed is True


@pytest.mark.unit
def test_evaluate_response_schema_rejects_bare_account_identification_code() -> None:
    """Bundled v4 schemas enforce Open Banking ``x-namespaced-enum`` code sets."""
    assertion = parsed_assertion(
        {
            "type": "response_schema",
            "source": "bundled_openapi",
            "document": "ob-read-write-v4.0-account-info-openapi",
            "schemaRef": "#/components/schemas/OBReadAccount6",
        }
    )

    result = evaluate_assertion(
        assertion,
        status_code=200,
        body={
            "Data": {
                "Account": [
                    {
                        "AccountId": "account-123",
                        "Account": [
                            {
                                "SchemeName": "IBAN",
                                "Identification": "GB33BUKB20201555555555",
                            }
                        ],
                    }
                ]
            },
            "Links": {"Self": "https://api.example.com/accounts"},
            "Meta": {},
        },
    )

    assert result.passed is False
    assert "at Data.Account[0].Account[0].SchemeName" in result.message
    assert "'IBAN' is not one of" in result.message


@pytest.mark.unit
def test_evaluate_response_schema_fails_for_bundled_schema_mismatch() -> None:
    assertion = parsed_assertion(
        {
            "type": "response_schema",
            "source": "bundled_openapi",
            "document": "ob-read-write-v4.0-account-info-openapi",
            "schemaRef": "#/components/schemas/OBReadAccount6",
        }
    )

    result = evaluate_assertion(
        assertion,
        status_code=200,
        body={"Data": {"Account": [{}]}, "Links": {}, "Meta": {}},
    )

    assert result.passed is False
    assert result.message == (
        "Response body failed schema validation: at Data.Account[0].AccountId: 'AccountId' is a required property"
    )


@pytest.mark.unit
def test_evaluate_response_schema_uses_body_path_for_inline_schema() -> None:
    assertion = parsed_assertion(
        {
            "type": "response_schema",
            "source": "bundled_openapi",
            "document": "ob-read-write-v4.0-account-info-openapi",
            "bodyPath": "Data",
            "schema": {
                "type": "object",
                "required": ["Account"],
                "properties": {
                    "Account": {
                        "type": "array",
                    }
                },
                "additionalProperties": False,
            },
        }
    )

    result = evaluate_assertion(
        assertion,
        status_code=200,
        body={"Data": {"Account": []}},
    )

    assert result.passed is True
    assert result.message == (
        "Response body path Data matches schema inline schema from ob-read-write-v4.0-account-info-openapi"
    )


@pytest.mark.unit
def test_evaluate_response_schema_fails_when_body_path_is_missing() -> None:
    assertion = parsed_assertion(
        {
            "type": "response_schema",
            "source": "bundled_openapi",
            "document": "ob-read-write-v4.0-account-info-openapi",
            "bodyPath": "Data.Account",
            "schema": {
                "type": "array",
            },
        }
    )

    result = evaluate_assertion(
        assertion,
        status_code=200,
        body={"Data": {}},
    )

    assert result.passed is False
    assert result.message == "Response body path Data.Account is missing"


@pytest.mark.unit
def test_evaluate_response_schema_reports_unknown_schema_ref_as_assertion_failure() -> None:
    result = evaluate_assertion(
        ResponseSchemaAssertion(
            type="response_schema",
            source="bundled_openapi",
            document="ob-read-write-v4.0-account-info-openapi",
            schema_ref="#/components/schemas/DoesNotExist",
        ),
        status_code=200,
        body={"Data": {}},
    )

    assert result.passed is False
    assert result.message == (
        "Response body failed schema validation: Schema reference '#/components/schemas/DoesNotExist' was not found"
    )


@pytest.mark.unit
def test_evaluate_header_matches_request_header_passes_for_matching_values() -> None:
    assertion = parsed_assertion({"type": "header", "name": "x-fapi-interaction-id", "rule": "matches_request_header"})

    result = evaluate_assertion(
        assertion,
        status_code=200,
        headers={"x-fapi-interaction-id": "abc-123"},
        request_headers={"x-fapi-interaction-id": "abc-123"},
        body={},
    )

    assert result.passed is True
    assert result.message == "Response header x-fapi-interaction-id echoes request header x-fapi-interaction-id"


@pytest.mark.unit
def test_evaluate_header_matches_request_header_uses_case_insensitive_name_lookup() -> None:
    assertion = parsed_assertion({"type": "header", "name": "x-fapi-interaction-id", "rule": "matches_request_header"})

    result = evaluate_assertion(
        assertion,
        status_code=200,
        headers={"x-fapi-interaction-id": "abc-123"},
        request_headers={"X-Fapi-Interaction-Id": "abc-123"},
        body={},
    )

    assert result.passed is True


@pytest.mark.unit
def test_evaluate_header_matches_request_header_fails_when_response_header_missing() -> None:
    assertion = parsed_assertion({"type": "header", "name": "x-fapi-interaction-id", "rule": "matches_request_header"})

    result = evaluate_assertion(
        assertion,
        status_code=200,
        headers={},
        request_headers={"x-fapi-interaction-id": "abc-123"},
        body={},
    )

    assert result.passed is False
    assert "x-fapi-interaction-id" in result.message
    assert "abc-123" not in result.message


@pytest.mark.unit
@pytest.mark.parametrize("request_headers", [None, {}])
def test_evaluate_header_matches_request_header_fails_when_request_header_not_sent(
    request_headers: dict[str, str] | None,
) -> None:
    assertion = parsed_assertion({"type": "header", "name": "x-fapi-interaction-id", "rule": "matches_request_header"})

    result = evaluate_assertion(
        assertion,
        status_code=200,
        headers={"x-fapi-interaction-id": "abc-123"},
        request_headers=request_headers,
        body={},
    )

    assert result.passed is False
    assert "was not sent" in result.message
    assert "x-fapi-interaction-id" in result.message


@pytest.mark.unit
def test_evaluate_header_matches_request_header_fails_on_value_mismatch_without_leaking_values() -> None:
    assertion = parsed_assertion({"type": "header", "name": "x-fapi-interaction-id", "rule": "matches_request_header"})

    result = evaluate_assertion(
        assertion,
        status_code=200,
        headers={"x-fapi-interaction-id": "xyz-999"},
        request_headers={"x-fapi-interaction-id": "abc-123"},
        body={},
    )

    assert result.passed is False
    assert "x-fapi-interaction-id" in result.message
    assert "abc-123" not in result.message
    assert "xyz-999" not in result.message


@pytest.mark.unit
def test_evaluate_header_present_still_works_when_request_headers_is_none() -> None:
    assertion = parsed_assertion({"type": "header", "name": "content-type", "rule": "present"})

    result = evaluate_assertion(
        assertion,
        status_code=200,
        headers={"Content-Type": "application/json"},
        request_headers=None,
        body={},
    )

    assert result.passed is True
    assert result.message == "Header content-type is present"


@pytest.mark.unit
def test_evaluate_header_matches_request_header_uses_explicit_request_header_name() -> None:
    assertion = HeaderAssertion(
        type="header",
        name="x-response-id",
        rule="matches_request_header",
        request_header="x-request-id",
    )

    result = evaluate_assertion(
        assertion,
        status_code=200,
        headers={"x-response-id": "abc-123"},
        request_headers={"x-request-id": "abc-123"},
        body={},
    )

    assert result.passed is True
    assert result.message == "Response header x-response-id echoes request header x-request-id"


@pytest.mark.unit
def test_evaluate_header_matches_request_header_compares_values_case_sensitively() -> None:
    assertion = parsed_assertion({"type": "header", "name": "x-fapi-interaction-id", "rule": "matches_request_header"})

    result = evaluate_assertion(
        assertion,
        status_code=200,
        headers={"x-fapi-interaction-id": "abc-123"},
        request_headers={"x-fapi-interaction-id": "Abc-123"},
        body={},
    )

    assert result.passed is False
