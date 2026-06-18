"""Evaluate manifest-declared assertions against JSON HTTP responses."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass

from conformance.json_types import JsonObject, JsonValue
from conformance.manifest import (
    HeaderAssertion,
    HttpStatusAssertion,
    JsonFieldAssertion,
    ManifestAssertion,
    ObErrorCodeAssertion,
    ResponseSchemaAssertion,
    ResponseSignatureAssertion,
)
from conformance.schema_validation import validate_json_instance_against_response_schema
from conformance.signing_service import DetachedJwsVerificationError, verify_ps256_detached_jws
from conformance.url_validation import HttpsUrlValidationError, validate_https_url


@dataclass(frozen=True)
class AssertionResult:
    """Outcome of evaluating one manifest assertion.

    Attributes:
        passed: Whether the assertion matched the response.
        message: Human-readable explanation suitable for result details.
    """

    passed: bool
    message: str


def evaluate_assertion(
    assertion: ManifestAssertion,
    *,
    status_code: int,
    headers: Mapping[str, str] | None = None,
    body: JsonObject,
    request_headers: Mapping[str, str] | None = None,
    body_bytes: bytes | None = None,
    response_signature_jwks: Mapping[str, JsonValue] | None = None,
) -> AssertionResult:
    """Evaluate a manifest assertion against an HTTP response.

    Args:
        assertion: Parsed manifest assertion to evaluate.
        status_code: HTTP response status code.
        headers: HTTP response headers available for header assertions.
        body: Parsed JSON object response body.
        request_headers: Resolved outbound HTTP request headers for the step.
            Only used by the ``matches_request_header`` header rule. When
            ``None``, that rule produces a failing result.
        body_bytes: Exact response body bytes used by ``response_signature``
            assertions. When ``None``, that rule produces a failing result.
        response_signature_jwks: JWKS response bodies keyed by step id for
            ``response_signature`` assertions.

    Returns:
        Assertion outcome and a concise diagnostic message.
    """
    if isinstance(assertion, HttpStatusAssertion):
        return _evaluate_http_status(assertion, status_code=status_code)
    if isinstance(assertion, ResponseSchemaAssertion):
        return _evaluate_response_schema(assertion, body=body)
    if isinstance(assertion, HeaderAssertion):
        return _evaluate_header(assertion, headers=headers, request_headers=request_headers)
    if isinstance(assertion, ObErrorCodeAssertion):
        return _evaluate_ob_error_code_assertion(assertion, response_body=body, location="Response body")
    if isinstance(assertion, ResponseSignatureAssertion):
        return _evaluate_response_signature(
            assertion,
            headers=headers,
            body_bytes=body_bytes,
            response_signature_jwks=response_signature_jwks,
        )
    return _evaluate_json_field(assertion, body=body)


def _evaluate_response_schema(assertion: ResponseSchemaAssertion, *, body: JsonObject) -> AssertionResult:
    """Evaluate a schema-backed response assertion.

    Args:
        assertion: Parsed response schema assertion.
        body: Parsed JSON response body to validate.

    Returns:
        Assertion result indicating whether the selected response value matches
        the configured schema.
    """
    instance: JsonValue = body
    location = "Response body"
    if assertion.body_path is not None:
        selected_value = _resolve_json_path(body, assertion.body_path)
        if isinstance(selected_value, _MissingValue):
            return AssertionResult(
                passed=False,
                message=f"Response body path {assertion.body_path} is missing",
            )
        instance = selected_value
        location = f"Response body path {assertion.body_path}"

    validation_message = validate_json_instance_against_response_schema(
        source=assertion.source,
        document=assertion.document,
        schema_ref=assertion.schema_ref,
        inline_schema=assertion.schema,
        instance=instance,
    )
    if validation_message is None:
        return AssertionResult(
            passed=True,
            message=f"{location} matches schema {_describe_response_schema(assertion)}",
        )
    return AssertionResult(
        passed=False,
        message=f"{location} failed schema validation: {validation_message}",
    )


def _evaluate_http_status(assertion: HttpStatusAssertion, *, status_code: int) -> AssertionResult:
    """Evaluate an HTTP status assertion.

    Args:
        assertion: Parsed HTTP status assertion with the expected code.
        status_code: Actual HTTP response status code.

    Returns:
        Assertion result indicating whether the status code matched.
    """
    if status_code == assertion.expected:
        return AssertionResult(passed=True, message=f"HTTP status was {status_code}")
    return AssertionResult(
        passed=False,
        message=f"Expected HTTP status {assertion.expected}, got {status_code}",
    )


def _evaluate_ob_error_code_assertion(
    assertion: ObErrorCodeAssertion,
    *,
    response_body: JsonValue,
    location: str,
) -> AssertionResult:
    """Evaluate an OB error-code assertion against a parsed response body.

    Checks that at least one ``Errors[*].ErrorCode`` in the response body
    matches one of the acceptable codes in the assertion. Implements the legacy
    ``asserts_one_of`` OB error-code semantics for negative test cases.

    Args:
        assertion: Parsed ``ObErrorCodeAssertion`` with acceptable error codes.
        response_body: Parsed JSON response body to evaluate.
        location: Dot-path location string used in result messages.

    Returns:
        Passing ``AssertionResult`` if at least one ``Errors[*].ErrorCode`` matches
        an acceptable code; failing result with diagnostic message otherwise.
    """
    if not isinstance(response_body, dict):
        return AssertionResult(
            passed=False,
            message=f"{location}: ob_error_code assertion requires a JSON object response body",
        )
    errors = response_body.get("Errors")
    if not isinstance(errors, list) or not errors:
        return AssertionResult(
            passed=False,
            message=(
                f"{location}: ob_error_code assertion requires Errors array in response; "
                f"expected one of {list(assertion.codes)}"
            ),
        )
    found_codes = [
        item.get("ErrorCode") for item in errors if isinstance(item, dict) and isinstance(item.get("ErrorCode"), str)
    ]
    for found in found_codes:
        if found in assertion.codes:
            return AssertionResult(passed=True, message=f"{location}: found acceptable OB error code {found!r}")
    return AssertionResult(
        passed=False,
        message=(
            f"{location}: ob_error_code assertion failed; expected one of {list(assertion.codes)}, got {found_codes}"
        ),
    )


def _evaluate_response_signature(
    assertion: ResponseSignatureAssertion,
    *,
    headers: Mapping[str, str] | None,
    body_bytes: bytes | None,
    response_signature_jwks: Mapping[str, JsonValue] | None,
) -> AssertionResult:
    """Evaluate an ASPSP response detached-JWS signature assertion.

    Args:
        assertion: Parsed response-signature assertion.
        headers: HTTP response headers containing the detached JWS.
        body_bytes: Exact response body bytes covered by the detached JWS.
        response_signature_jwks: JWKS bodies keyed by step id.

    Returns:
        Assertion result indicating whether the response signature verified.
    """
    if headers is None:
        return AssertionResult(passed=False, message="Response signature assertion requires response headers")
    signature = headers.get(assertion.header_name)
    if signature is None or not signature.strip():
        return AssertionResult(
            passed=False,
            message=f"Response signature assertion requires {assertion.header_name} header",
        )
    if body_bytes is None:
        return AssertionResult(passed=False, message="Response signature assertion requires raw response body bytes")
    if response_signature_jwks is None or assertion.jwks_step_id not in response_signature_jwks:
        return AssertionResult(
            passed=False,
            message=f"Response signature assertion requires JWKS from step {assertion.jwks_step_id!r}",
        )
    try:
        verify_ps256_detached_jws(
            signature=signature,
            payload=body_bytes,
            jwks=response_signature_jwks[assertion.jwks_step_id],
        )
    except DetachedJwsVerificationError as error:
        return AssertionResult(passed=False, message=f"Response signature verification failed: {error}")
    return AssertionResult(
        passed=True,
        message=f"Response {assertion.header_name} signature verified using JWKS step {assertion.jwks_step_id!r}",
    )


def _evaluate_json_field(assertion: JsonFieldAssertion, *, body: JsonObject) -> AssertionResult:
    """Evaluate a JSON field assertion against a parsed response body.

    Resolves the dot-separated path, checks presence, then delegates to the
    rule-specific evaluator.

    Args:
        assertion: Parsed JSON field assertion with path and rule.
        body: Parsed JSON response body to evaluate against.

    Returns:
        Assertion result indicating whether the field satisfies the rule.
    """
    value = _resolve_json_path(body, assertion.path)
    if assertion.rule == "absent":
        if isinstance(value, _MissingValue):
            return AssertionResult(passed=True, message=f"JSON field {assertion.path} is absent")
        return AssertionResult(passed=False, message=f"JSON field {assertion.path} must be absent")

    if isinstance(value, _MissingValue):
        return AssertionResult(passed=False, message=f"JSON field {assertion.path} is missing")
    if assertion.rule == "required":
        return AssertionResult(passed=True, message=f"JSON field {assertion.path} is present")
    if assertion.rule == "https_url":
        return _evaluate_https_url(assertion.path, value)
    if assertion.rule == "array":
        return _evaluate_array(assertion.path, value)
    if assertion.rule == "string":
        return _evaluate_json_string(assertion.path, value)
    if assertion.rule == "number":
        return _evaluate_json_number(assertion.path, value)
    if assertion.rule == "boolean":
        return _evaluate_json_boolean(assertion.path, value)
    if assertion.rule == "object":
        return _evaluate_json_object(assertion.path, value)
    if assertion.rule == "non_empty_array":
        return _evaluate_non_empty_array(assertion.path, value)
    if assertion.rule == "min_items":
        return _evaluate_min_items(assertion.path, value, assertion.min_items)
    if assertion.rule == "equals":
        return _evaluate_json_equals(assertion.path, value, assertion.value)
    if assertion.rule == "one_of":
        return _evaluate_json_one_of(assertion.path, value, assertion.values)
    if assertion.rule == "all_items_have_field":
        return _evaluate_all_items_have_field(assertion.path, value, assertion.field)
    return _evaluate_all_items_absent_field(assertion.path, value, assertion.field)


def _describe_response_schema(assertion: ResponseSchemaAssertion) -> str:
    """Describe the response schema target for diagnostics.

    Args:
        assertion: Parsed response schema assertion.

    Returns:
        Human-readable schema target description.
    """
    if assertion.schema_ref is not None:
        return f"{assertion.schema_ref} from {assertion.document}"
    return f"inline schema from {assertion.document}"


def _evaluate_https_url(path: str, value: JsonValue) -> AssertionResult:
    """Evaluate whether a JSON value is an HTTPS URL string.

    Args:
        path: Dot-separated JSON path used for diagnostic messages.
        value: Resolved JSON value to validate as an HTTPS URL.

    Returns:
        Assertion result indicating whether the value is a valid HTTPS URL.
    """
    if not isinstance(value, str) or not value.strip():
        return AssertionResult(passed=False, message=f"JSON field {path} must be a non-empty HTTPS URL string")
    try:
        validate_https_url(value.strip(), label=f"JSON field {path}")
    except HttpsUrlValidationError as error:
        return AssertionResult(passed=False, message=str(error))
    return AssertionResult(passed=True, message=f"JSON field {path} is an HTTPS URL")


def _evaluate_array(path: str, value: JsonValue) -> AssertionResult:
    """Evaluate whether a JSON value is an array.

    Args:
        path: Dot-separated JSON path used for diagnostic messages.
        value: Resolved JSON value to check for list type.

    Returns:
        Assertion result indicating whether the value is an array.
    """
    if isinstance(value, list):
        return AssertionResult(passed=True, message=f"JSON field {path} is an array")
    return AssertionResult(passed=False, message=f"JSON field {path} must be an array")


def _evaluate_json_string(path: str, value: JsonValue) -> AssertionResult:
    """Evaluate whether a JSON value is a string.

    Args:
        path: Dot-separated JSON path used for diagnostic messages.
        value: Resolved JSON value to validate.

    Returns:
        Assertion result indicating whether the value is a string.
    """
    if isinstance(value, str):
        return AssertionResult(passed=True, message=f"JSON field {path} is a string")
    return AssertionResult(passed=False, message=f"JSON field {path} must be a string")


def _evaluate_json_number(path: str, value: JsonValue) -> AssertionResult:
    """Evaluate whether a JSON value is a number.

    JSON booleans must not pass this check even though ``bool`` subclasses
    ``int`` in Python.

    Args:
        path: Dot-separated JSON path used for diagnostic messages.
        value: Resolved JSON value to validate.

    Returns:
        Assertion result indicating whether the value is a JSON number.
    """
    if isinstance(value, bool):
        return AssertionResult(passed=False, message=f"JSON field {path} must be a number")
    if isinstance(value, int | float):
        return AssertionResult(passed=True, message=f"JSON field {path} is a number")
    return AssertionResult(passed=False, message=f"JSON field {path} must be a number")


def _evaluate_json_boolean(path: str, value: JsonValue) -> AssertionResult:
    """Evaluate whether a JSON value is a boolean.

    Args:
        path: Dot-separated JSON path used for diagnostic messages.
        value: Resolved JSON value to validate.

    Returns:
        Assertion result indicating whether the value is a boolean.
    """
    if isinstance(value, bool):
        return AssertionResult(passed=True, message=f"JSON field {path} is a boolean")
    return AssertionResult(passed=False, message=f"JSON field {path} must be a boolean")


def _evaluate_json_object(path: str, value: JsonValue) -> AssertionResult:
    """Evaluate whether a JSON value is an object.

    Args:
        path: Dot-separated JSON path used for diagnostic messages.
        value: Resolved JSON value to validate.

    Returns:
        Assertion result indicating whether the value is a JSON object.
    """
    if isinstance(value, Mapping):
        return AssertionResult(passed=True, message=f"JSON field {path} is an object")
    return AssertionResult(passed=False, message=f"JSON field {path} must be an object")


def _evaluate_non_empty_array(path: str, value: JsonValue) -> AssertionResult:
    """Evaluate whether a JSON value is a non-empty array.

    Args:
        path: Dot-separated JSON path used for diagnostic messages.
        value: Resolved JSON value to validate.

    Returns:
        Assertion result indicating whether the value is a non-empty list.
    """
    if isinstance(value, list) and value:
        return AssertionResult(passed=True, message=f"JSON field {path} is a non-empty array")
    return AssertionResult(passed=False, message=f"JSON field {path} must be a non-empty array")


def _evaluate_min_items(path: str, value: JsonValue, minimum: int | None) -> AssertionResult:
    """Evaluate whether a JSON array contains at least ``minimum`` items.

    Args:
        path: Dot-separated JSON path used for diagnostic messages.
        value: Resolved JSON value to validate.
        minimum: Minimum allowed array length parsed from the manifest.

    Returns:
        Assertion result indicating whether the array length meets the
        configured threshold.
    """
    if not isinstance(value, list):
        return AssertionResult(passed=False, message=f"JSON field {path} must be an array")
    if minimum is None:
        return AssertionResult(passed=False, message=f"JSON field {path} has an invalid minimum item count")
    if len(value) >= minimum:
        return AssertionResult(passed=True, message=f"JSON field {path} contains at least {minimum} items")
    return AssertionResult(passed=False, message=f"JSON field {path} must contain at least {minimum} items")


def _evaluate_json_equals(path: str, value: JsonValue, expected: JsonValue | None) -> AssertionResult:
    """Evaluate whether a JSON value equals the manifest's expected value.

    Args:
        path: Dot-separated JSON path used for diagnostic messages.
        value: Resolved JSON value to compare.
        expected: Expected JSON-compatible value from the manifest.

    Returns:
        Assertion result indicating whether the values are equal.
    """
    if _json_values_equal(value, expected):
        return AssertionResult(passed=True, message=f"JSON field {path} equals {_format_json_value(expected)}")
    return AssertionResult(passed=False, message=f"JSON field {path} must equal {_format_json_value(expected)}")


def _evaluate_json_one_of(path: str, value: JsonValue, candidates: tuple[JsonValue, ...] | None) -> AssertionResult:
    """Evaluate whether a JSON value matches one of the manifest candidates.

    Args:
        path: Dot-separated JSON path used for diagnostic messages.
        value: Resolved JSON value to compare.
        candidates: Candidate JSON-compatible values accepted by the rule.

    Returns:
        Assertion result indicating whether the value matched one candidate.
    """
    if candidates is None:
        return AssertionResult(passed=False, message=f"JSON field {path} has no allowed values")
    if any(_json_values_equal(value, candidate) for candidate in candidates):
        return AssertionResult(
            passed=True,
            message=f"JSON field {path} equals one of: {_format_json_value_list(candidates)}",
        )
    return AssertionResult(
        passed=False,
        message=f"JSON field {path} must equal one of: {_format_json_value_list(candidates)}",
    )


def _evaluate_all_items_have_field(path: str, value: JsonValue, field_name: str | None) -> AssertionResult:
    """Evaluate whether every object in an array contains ``field_name``.

    Args:
        path: Dot-separated JSON path used for diagnostic messages.
        value: Resolved JSON value to validate.
        field_name: Required field name for each array item.

    Returns:
        Assertion result indicating whether every array item is an object with
        the required field.
    """
    if not isinstance(value, list):
        return AssertionResult(passed=False, message=f"JSON field {path} must be an array")
    if not field_name:
        return AssertionResult(passed=False, message=f"JSON field {path} has an invalid required item field")
    for item in value:
        if not isinstance(item, Mapping) or field_name not in item:
            return AssertionResult(
                passed=False,
                message=f"Every item in JSON field {path} must contain field {field_name}",
            )
    return AssertionResult(
        passed=True,
        message=f"Every item in JSON field {path} contains field {field_name}",
    )


def _evaluate_all_items_absent_field(path: str, value: JsonValue, field_name: str | None) -> AssertionResult:
    """Evaluate whether every object in an array omits ``field_name``.

    Args:
        path: Dot-separated JSON path used for diagnostic messages.
        value: Resolved JSON value to validate.
        field_name: Field name that must be absent from every object item.

    Returns:
        Assertion result indicating whether every object item omits the field.
    """
    if not isinstance(value, list):
        return AssertionResult(passed=False, message=f"JSON field {path} must be an array")
    if not field_name:
        return AssertionResult(passed=False, message=f"JSON field {path} has an invalid forbidden item field")
    for item in value:
        if isinstance(item, Mapping) and field_name in item:
            return AssertionResult(
                passed=False,
                message=f"Every item in JSON field {path} must omit field {field_name}",
            )
    return AssertionResult(
        passed=True,
        message=f"Every item in JSON field {path} omits field {field_name}",
    )


def _evaluate_header(
    assertion: HeaderAssertion,
    *,
    headers: Mapping[str, str] | None,
    request_headers: Mapping[str, str] | None = None,
) -> AssertionResult:
    """Evaluate a response-header assertion.

    Args:
        assertion: Parsed header assertion with name and rule.
        headers: Response header mapping available for lookup.
        request_headers: Resolved outbound request headers. Only used by the
            ``matches_request_header`` rule.

    Returns:
        Assertion result indicating whether the header satisfied the rule.
    """
    header_value = _resolve_header_value(headers, assertion.name)
    if assertion.rule == "present":
        if header_value is None:
            return AssertionResult(passed=False, message=f"Header {assertion.name} is missing")
        return AssertionResult(passed=True, message=f"Header {assertion.name} is present")
    if assertion.rule == "absent":
        if header_value is None:
            return AssertionResult(passed=True, message=f"Header {assertion.name} is absent")
        return AssertionResult(passed=False, message=f"Header {assertion.name} must be absent")
    if header_value is None:
        return AssertionResult(passed=False, message=f"Header {assertion.name} is missing")
    if assertion.rule == "equals":
        if header_value == assertion.value:
            return AssertionResult(passed=True, message=f"Header {assertion.name} equals the expected value")
        return AssertionResult(passed=False, message=f"Header {assertion.name} must equal the expected value")
    if assertion.rule == "contains":
        if assertion.value is not None and assertion.value in header_value:
            return AssertionResult(passed=True, message=f"Header {assertion.name} contains the expected value")
        return AssertionResult(passed=False, message=f"Header {assertion.name} must contain the expected value")
    if assertion.rule == "matches_request_header":
        req_header_name = assertion.request_header if assertion.request_header is not None else assertion.name
        sent_value = _resolve_header_value(request_headers, req_header_name)
        if sent_value is None:
            return AssertionResult(
                passed=False,
                message=(
                    f"Request header {req_header_name} was not sent; "
                    f"cannot verify response playback of {assertion.name}"
                ),
            )
        response_value = _resolve_header_value(headers, assertion.name)
        if response_value is None:
            return AssertionResult(
                passed=False,
                message=(
                    f"Response header {assertion.name} is missing; "
                    f"expected playback of request header {req_header_name}"
                ),
            )
        if response_value == sent_value:
            return AssertionResult(
                passed=True,
                message=f"Response header {assertion.name} echoes request header {req_header_name}",
            )
        return AssertionResult(
            passed=False,
            message=(f"Response header {assertion.name} does not match request header {req_header_name}"),
        )
    return AssertionResult(passed=False, message=f"Header {assertion.name} has unsupported rule {assertion.rule}")


def _resolve_header_value(headers: Mapping[str, str] | None, name: str) -> str | None:
    """Resolve a header value using case-insensitive header-name matching.

    Args:
        headers: Header mapping to search.
        name: Header field name to resolve.

    Returns:
        Matching header value, or ``None`` when the header is absent.
    """
    if headers is None:
        return None
    lower_name = name.lower()
    for header_name, header_value in headers.items():
        if header_name.lower() == lower_name:
            return header_value
    return None


def _format_json_value(value: JsonValue | None) -> str:
    """Render a JSON-compatible value for concise diagnostic messages.

    Args:
        value: JSON-compatible value to render.

    Returns:
        Human-readable representation suitable for assertion diagnostics.
    """
    if isinstance(value, str):
        return value
    return json.dumps(value, separators=(",", ": "))


def _format_json_value_list(values: tuple[JsonValue, ...]) -> str:
    """Render a list of JSON-compatible values for diagnostics.

    Args:
        values: Candidate values accepted by an assertion.

    Returns:
        Comma-separated representation of the values.
    """
    return ", ".join(_format_json_value(value) for value in values)


def _json_values_equal(left: JsonValue, right: JsonValue | None) -> bool:
    """Compare JSON values without conflating booleans and numbers.

    Python treats ``bool`` as a subclass of ``int``, so bare ``==`` would make
    ``true`` equal to ``1`` and ``false`` equal to ``0``. Manifest assertions
    need JSON-style type-aware equality instead.

    Args:
        left: Actual JSON value resolved from the response.
        right: Expected JSON-compatible value from the manifest.

    Returns:
        ``True`` when the values are equal without cross-type boolean/number
        coercion.
    """
    if isinstance(left, bool) or isinstance(right, bool):
        return isinstance(left, bool) and isinstance(right, bool) and left is right
    if isinstance(left, float) and math.isnan(left):
        return isinstance(right, float) and math.isnan(right)
    if isinstance(right, float) and math.isnan(right):
        return False
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(
            _json_values_equal(left_item, right_item) for left_item, right_item in zip(left, right, strict=True)
        )
    if isinstance(left, Mapping) and isinstance(right, Mapping):
        return left.keys() == right.keys() and all(_json_values_equal(left[key], right[key]) for key in left)
    return left == right


class _MissingValue:
    """Sentinel used to distinguish missing fields from explicit JSON null."""


_MISSING = _MissingValue()


def _resolve_json_path(body: JsonObject, path: str) -> JsonValue | _MissingValue:
    """Resolve a dot-separated JSON object path.

    Args:
        body: Parsed JSON response body to traverse.
        path: Dot-separated field path (e.g. ``openid_configuration.issuer``).

    Returns:
        The resolved value, or the ``_MISSING`` sentinel if any segment is absent.
    """
    current_value: JsonValue = body
    for path_part in path.split("."):
        if not isinstance(current_value, Mapping) or path_part not in current_value:
            return _MISSING
        current_value = current_value[path_part]
    return current_value
