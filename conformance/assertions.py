"""Evaluate manifest-declared assertions against JSON HTTP responses."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

from conformance.json_types import JsonObject, JsonValue
from conformance.manifest import HttpStatusAssertion, JsonFieldAssertion, ManifestAssertion


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
    """Evaluate a JSON field assertion against a parsed response body."""
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
    parsed_url = urlparse(value.strip())
    try:
        parsed_port = parsed_url.port
    except ValueError:
        return AssertionResult(passed=False, message=f"JSON field {path} must be a valid HTTPS URL")
    if parsed_port is not None and parsed_port <= 0:
        return AssertionResult(passed=False, message=f"JSON field {path} must be a valid HTTPS URL")
    if parsed_url.scheme != "https" or parsed_url.hostname is None:
        return AssertionResult(passed=False, message=f"JSON field {path} must be an HTTPS URL")
    if parsed_url.username is not None or parsed_url.password is not None:
        return AssertionResult(passed=False, message=f"JSON field {path} must not include credentials")
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
