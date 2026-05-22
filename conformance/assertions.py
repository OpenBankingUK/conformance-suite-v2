"""Evaluate manifest-declared assertions against JSON HTTP responses."""

from __future__ import annotations

from dataclasses import dataclass

from conformance.json_types import JsonObject, JsonValue
from conformance.manifest import HttpStatusAssertion, JsonFieldAssertion, ManifestAssertion
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
    body: JsonObject,
) -> AssertionResult:
    """Evaluate a v0 manifest assertion against an HTTP response.

    Args:
        assertion: Parsed manifest assertion to evaluate.
        status_code: HTTP response status code.
        body: Parsed JSON object response body.

    Returns:
        Assertion outcome and a concise diagnostic message.
    """
    if isinstance(assertion, HttpStatusAssertion):
        return _evaluate_http_status(assertion, status_code=status_code)
    return _evaluate_json_field(assertion, body=body)


def _evaluate_http_status(assertion: HttpStatusAssertion, *, status_code: int) -> AssertionResult:
    """Evaluate an HTTP status assertion."""
    if status_code == assertion.expected:
        return AssertionResult(passed=True, message=f"HTTP status was {status_code}")
    return AssertionResult(
        passed=False,
        message=f"Expected HTTP status {assertion.expected}, got {status_code}",
    )


def _evaluate_json_field(assertion: JsonFieldAssertion, *, body: JsonObject) -> AssertionResult:
    """Evaluate a JSON field assertion against a parsed response body.

    Resolves the dot-separated path, checks presence, then delegates to the
    rule-specific evaluator (required, https_url, or array).

    Args:
        assertion: Parsed JSON field assertion with path and rule.
        body: Parsed JSON response body to evaluate against.

    Returns:
        Assertion result indicating whether the field satisfies the rule.
    """
    value = _resolve_json_path(body, assertion.path)
    if isinstance(value, _MissingValue):
        return AssertionResult(passed=False, message=f"JSON field {assertion.path} is missing")
    if assertion.rule == "required":
        return AssertionResult(passed=True, message=f"JSON field {assertion.path} is present")
    if assertion.rule == "https_url":
        return _evaluate_https_url(assertion.path, value)
    return _evaluate_array(assertion.path, value)


def _evaluate_https_url(path: str, value: JsonValue) -> AssertionResult:
    """Evaluate whether a JSON value is an HTTPS URL string."""
    if not isinstance(value, str) or not value.strip():
        return AssertionResult(passed=False, message=f"JSON field {path} must be a non-empty HTTPS URL string")
    try:
        validate_https_url(value.strip(), label=f"JSON field {path}")
    except HttpsUrlValidationError as error:
        return AssertionResult(passed=False, message=str(error))
    return AssertionResult(passed=True, message=f"JSON field {path} is an HTTPS URL")


def _evaluate_array(path: str, value: JsonValue) -> AssertionResult:
    """Evaluate whether a JSON value is an array."""
    if isinstance(value, list):
        return AssertionResult(passed=True, message=f"JSON field {path} is an array")
    return AssertionResult(passed=False, message=f"JSON field {path} must be an array")


class _MissingValue:
    """Sentinel used to distinguish missing fields from explicit JSON null."""


_MISSING = _MissingValue()


def _resolve_json_path(body: JsonObject, path: str) -> JsonValue | _MissingValue:
    """Resolve a dot-separated JSON object path."""
    current_value: JsonValue = body
    for path_part in path.split("."):
        if not isinstance(current_value, dict) or path_part not in current_value:
            return _MISSING
        current_value = current_value[path_part]
    return current_value
