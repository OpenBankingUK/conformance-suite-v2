"""Load and validate v0 conformance manifest files."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from conformance.json_types import JsonValue
from conformance.url_validation import HttpsUrlValidationError, validate_https_url


class ManifestError(ValueError):
    """Raised when a conformance manifest cannot be loaded or validated."""


ManifestSchemaVersion = Literal["v0"]
"""Manifest schema versions accepted by the v0 parser."""

RequestMethod = Literal["GET"]
"""HTTP methods supported by manifest-driven smoke-check requests."""

AssertionType = Literal["http_status", "json_field"]
"""Assertion discriminators supported by v0 manifest tests."""

JsonFieldRule = Literal["required", "https_url", "array"]
"""JSON field validation rules supported by manifest assertions."""

FollowUpType = Literal["jwks"]
"""Follow-up request kinds supported by v0 manifest tests."""

FollowUpUrlSource = Literal["response.body.jwks_uri"]
"""Locations from which follow-up request URLs may be extracted."""


@dataclass(frozen=True)
class ManifestRequest:
    """HTTP request declared by a manifest test.

    Attributes:
        method: HTTP method used for the manifest request.
        url: HTTPS URL fetched for the manifest test.
    """

    method: RequestMethod
    url: str


@dataclass(frozen=True)
class FollowUpRequest:
    """HTTP request shape for a manifest follow-up step.

    Attributes:
        method: HTTP method used for the follow-up request.
    """

    method: RequestMethod


@dataclass(frozen=True)
class HttpStatusAssertion:
    """Assertion requiring a specific HTTP response status.

    Attributes:
        type: Assertion discriminator for HTTP status checks.
        expected: Expected HTTP status code.
    """

    type: Literal["http_status"]
    expected: int


@dataclass(frozen=True)
class JsonFieldAssertion:
    """Assertion requiring a JSON field to satisfy a rule.

    Attributes:
        type: Assertion discriminator for JSON field checks.
        path: Dot-path to the response JSON field under test.
        rule: Validation rule applied to the JSON field.
    """

    type: Literal["json_field"]
    path: str
    rule: JsonFieldRule


ManifestAssertion = HttpStatusAssertion | JsonFieldAssertion
"""Assertion variants accepted by v0 manifest tests and follow-up steps."""


@dataclass(frozen=True)
class ManifestFollowUp:
    """Manifest follow-up step derived from a prior response.

    Attributes:
        type: Follow-up step discriminator.
        url_source: Response location used to discover the follow-up URL.
        request: HTTP request shape for the follow-up fetch.
        assertions: Assertions evaluated against the follow-up response.
    """

    type: FollowUpType
    url_source: FollowUpUrlSource
    request: FollowUpRequest
    assertions: tuple[ManifestAssertion, ...]


@dataclass(frozen=True)
class ManifestTest:
    """Single conformance check declared by a v0 manifest.

    Attributes:
        id: Stable identifier for the manifest test.
        name: Human-readable test name.
        request: Primary HTTP request to execute.
        assertions: Assertions evaluated against the primary response.
        follow_up: Optional follow-up step, such as JWKS validation.
    """

    id: str
    name: str
    request: ManifestRequest
    assertions: tuple[ManifestAssertion, ...]
    follow_up: ManifestFollowUp | None = None


@dataclass(frozen=True)
class Manifest:
    """Validated v0 conformance manifest.

    Attributes:
        schema_version: Manifest schema version accepted by this parser.
        name: Human-readable manifest name.
        tests: Ordered manifest tests to execute.
    """

    schema_version: ManifestSchemaVersion
    name: str
    tests: tuple[ManifestTest, ...]


def load_manifest(manifest_path: Path) -> Manifest:
    """Load a v0 conformance manifest JSON file from disk.

    Args:
        manifest_path: Path to the manifest JSON file.

    Returns:
        Parsed and validated conformance manifest.

    Raises:
        ManifestError: If the file cannot be read, parsed, or validated.
    """
    resolved_manifest_path = manifest_path.resolve()
    try:
        raw_manifest = json.loads(resolved_manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ManifestError(f"Invalid JSON manifest: {error.msg}") from error
    except OSError as error:
        raise ManifestError(f"Unable to read manifest file: {error}") from error

    if not isinstance(raw_manifest, dict):
        raise ManifestError("Manifest root must be a JSON object")

    return parse_manifest(raw_manifest)


def parse_manifest(raw_manifest: dict[str, JsonValue]) -> Manifest:
    """Parse a raw JSON object into a validated v0 manifest.

    Args:
        raw_manifest: JSON object loaded from a conformance manifest file.

    Returns:
        Parsed and validated conformance manifest.

    Raises:
        ManifestError: If required fields are missing or validation fails.
    """
    _reject_unknown_keys(raw_manifest, allowed_keys={"schemaVersion", "name", "tests"}, location="manifest")

    schema_version = _required_string(raw_manifest, "schemaVersion", location="manifest")
    if schema_version != "v0":
        raise ManifestError("schemaVersion must be v0")

    name = _required_string(raw_manifest, "name", location="manifest")
    tests = _required_object_array(raw_manifest, "tests", location="manifest")

    return Manifest(
        schema_version="v0",
        name=name,
        tests=tuple(_parse_test(raw_test, index=index) for index, raw_test in enumerate(tests)),
    )


def _parse_test(raw_test: dict[str, JsonValue], *, index: int) -> ManifestTest:
    """Parse a single test entry from the manifest tests array.

    Args:
        raw_test: Raw JSON object representing one manifest test.
        index: Zero-based position in the tests array, used for error locations.

    Returns:
        Validated manifest test with request, assertions, and optional follow-up.

    Raises:
        ManifestError: If required fields are missing or contain invalid values.
    """
    location = f"tests[{index}]"
    _reject_unknown_keys(
        raw_test,
        allowed_keys={"id", "name", "request", "assertions", "followUp"},
        location=location,
    )

    assertions = _required_object_array(raw_test, "assertions", location=location)
    has_follow_up = "followUp" in raw_test
    raw_follow_up = raw_test["followUp"] if has_follow_up else None

    return ManifestTest(
        id=_required_string(raw_test, "id", location=location),
        name=_required_string(raw_test, "name", location=location),
        request=_parse_request(
            _required_object(raw_test, "request", location=location),
            location=f"{location}.request",
        ),
        assertions=tuple(
            _parse_assertion(raw_assertion, location=f"{location}.assertions[{assertion_index}]")
            for assertion_index, raw_assertion in enumerate(assertions)
        ),
        follow_up=_parse_follow_up(raw_follow_up, location=f"{location}.followUp") if has_follow_up else None,
    )


def _parse_request(raw_request: dict[str, JsonValue], *, location: str) -> ManifestRequest:
    """Parse and validate a manifest test request object."""
    _reject_unknown_keys(raw_request, allowed_keys={"method", "url"}, location=location)
    return ManifestRequest(
        method=_required_get_method(raw_request, location=location),
        url=_required_https_url(raw_request, "url", location=location),
    )


def _parse_follow_up(raw_follow_up: JsonValue, *, location: str) -> ManifestFollowUp:
    """Parse a JWKS follow-up step from a manifest test.

    Validates that the follow-up declares a ``jwks`` type with
    ``response.body.jwks_uri`` as the URL source, then recursively parses
    the nested request and assertions.

    Args:
        raw_follow_up: Raw JSON value expected to be the followUp object.
        location: Manifest path prefix for error messages.

    Returns:
        Validated follow-up step ready for execution.

    Raises:
        ManifestError: If the follow-up shape is invalid or unsupported.
    """
    if not isinstance(raw_follow_up, dict):
        raise ManifestError(f"{location} must be a JSON object")
    _reject_unknown_keys(
        raw_follow_up,
        allowed_keys={"type", "urlSource", "request", "assertions"},
        location=location,
    )

    follow_up_type = _required_string(raw_follow_up, "type", location=location)
    if follow_up_type != "jwks":
        raise ManifestError(f"{location}.type must be jwks")

    url_source = _required_string(raw_follow_up, "urlSource", location=location)
    if url_source != "response.body.jwks_uri":
        raise ManifestError(f"{location}.urlSource must be response.body.jwks_uri")

    assertions = _required_object_array(raw_follow_up, "assertions", location=location)

    return ManifestFollowUp(
        type="jwks",
        url_source="response.body.jwks_uri",
        request=_parse_follow_up_request(
            _required_object(raw_follow_up, "request", location=location),
            location=f"{location}.request",
        ),
        assertions=tuple(
            _parse_assertion(raw_assertion, location=f"{location}.assertions[{assertion_index}]")
            for assertion_index, raw_assertion in enumerate(assertions)
        ),
    )


def _parse_follow_up_request(raw_request: dict[str, JsonValue], *, location: str) -> FollowUpRequest:
    """Parse the request object inside a manifest follow-up step."""
    _reject_unknown_keys(raw_request, allowed_keys={"method"}, location=location)
    return FollowUpRequest(method=_required_get_method(raw_request, location=location))


def _parse_assertion(raw_assertion: dict[str, JsonValue], *, location: str) -> ManifestAssertion:
    """Parse and dispatch a single assertion by its type discriminator.

    Args:
        raw_assertion: Raw assertion dict from the manifest JSON.
        location: Dot-path location string used in error messages.

    Returns:
        A typed assertion dataclass (HttpStatusAssertion or JsonFieldAssertion).

    Raises:
        ManifestError: If the assertion type is missing, unsupported, or
            required fields are invalid.
    """
    assertion_type = _required_assertion_type(raw_assertion, location=location)
    if assertion_type == "http_status":
        _reject_unknown_keys(raw_assertion, allowed_keys={"type", "expected"}, location=location)
        return HttpStatusAssertion(type="http_status", expected=_required_status_code(raw_assertion, location=location))
    if assertion_type == "json_field":
        _reject_unknown_keys(raw_assertion, allowed_keys={"type", "path", "rule"}, location=location)
        return JsonFieldAssertion(
            type="json_field",
            path=_required_string(raw_assertion, "path", location=location),
            rule=_required_json_field_rule(raw_assertion, location=location),
        )


def _required_assertion_type(raw_assertion: dict[str, JsonValue], *, location: str) -> AssertionType:
    """Extract and validate the assertion type discriminator.

    Raises:
        ManifestError: If the assertion type is missing or unsupported.
    """
    assertion_type = _required_string(raw_assertion, "type", location=location)
    if assertion_type == "http_status":
        return "http_status"
    if assertion_type == "json_field":
        return "json_field"
    raise ManifestError(f"{location}.type must be one of: http_status, json_field")


def _required_get_method(raw_config: dict[str, JsonValue], *, location: str) -> RequestMethod:
    """Extract and validate that the request method is GET.

    Raises:
        ManifestError: If the method is missing or not GET.
    """
    method = _required_string(raw_config, "method", location=location)
    if method != "GET":
        raise ManifestError(f"{location}.method must be GET")
    return "GET"


def _required_json_field_rule(raw_assertion: dict[str, JsonValue], *, location: str) -> JsonFieldRule:
    """Extract and validate the JSON field assertion rule.

    Raises:
        ManifestError: If the JSON field rule is missing or unsupported.
    """
    rule = _required_string(raw_assertion, "rule", location=location)
    if rule == "required":
        return "required"
    if rule == "https_url":
        return "https_url"
    if rule == "array":
        return "array"
    raise ManifestError(f"{location}.rule must be one of: required, https_url, array")


def _required_status_code(raw_assertion: dict[str, JsonValue], *, location: str) -> int:
    """Extract and validate an HTTP status code (100–599).

    Raises:
        ManifestError: If the value is not an HTTP status code.
    """
    value = raw_assertion.get("expected")
    if not isinstance(value, int) or isinstance(value, bool) or value < 100 or value > 599:
        raise ManifestError(f"{location}.expected must be an HTTP status code")
    return value


def _required_string(raw_config: dict[str, JsonValue], key: str, *, location: str) -> str:
    """Extract a required non-empty string from a JSON object.

    Raises:
        ManifestError: If the value is missing or not a non-empty string.
    """
    value = raw_config.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ManifestError(f"{location}.{key} must be a non-empty string")
    return value.strip()


def _required_https_url(raw_config: dict[str, JsonValue], key: str, *, location: str) -> str:
    """Extract and validate a hardened HTTPS URL from a JSON object.

    Rejects non-HTTPS schemes, embedded credentials, control characters,
    IP-literal hostnames, and malformed DNS hostnames. Used for all
    manifest URLs that will be fetched over the network.

    Raises:
        ManifestError: If the value is not a safe, well-formed HTTPS URL.
    """
    value = _required_string(raw_config, key, location=location)
    try:
        validate_https_url(value, label=f"{location}.{key}")
    except HttpsUrlValidationError as error:
        raise ManifestError(str(error)) from error
    return value


def _required_object(raw_config: dict[str, JsonValue], key: str, *, location: str) -> dict[str, JsonValue]:
    """Extract a required JSON object from a parent object.

    Raises:
        ManifestError: If the value is missing or not a JSON object.
    """
    value = raw_config.get(key)
    if not isinstance(value, dict):
        raise ManifestError(f"{location}.{key} must be a JSON object")
    return value


def _required_object_array(raw_config: dict[str, JsonValue], key: str, *, location: str) -> list[dict[str, JsonValue]]:
    """Extract a required non-empty array of JSON objects.

    Raises:
        ManifestError: If the value is missing, empty, or contains non-objects.
    """
    value = raw_config.get(key)
    if not isinstance(value, list) or not value:
        raise ManifestError(f"{location}.{key} must be a non-empty array")
    objects: list[dict[str, JsonValue]] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise ManifestError(f"{location}.{key}[{index}] must be a JSON object")
        objects.append(item)
    return objects


def _reject_unknown_keys(raw_config: dict[str, JsonValue], *, allowed_keys: set[str], location: str) -> None:
    """Raise if the JSON object contains keys outside the allowed set.

    Raises:
        ManifestError: If any keys are outside the allowed set.
    """
    unknown_keys = sorted(set(raw_config) - allowed_keys)
    if unknown_keys:
        joined_keys = ", ".join(unknown_keys)
        raise ManifestError(f"Unknown {location} field(s): {joined_keys}")
