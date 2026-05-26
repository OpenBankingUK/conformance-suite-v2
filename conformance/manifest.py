"""Load and validate v0/v1 conformance manifest files."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from conformance.json_types import JsonValue
from conformance.url_validation import HttpsUrlValidationError, validate_https_url


class ManifestError(ValueError):
    """Raised when a conformance manifest cannot be loaded or validated."""


ManifestSchemaVersion = Literal["v0", "v1"]
"""Manifest schema versions accepted by the parser."""

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
class ManifestStep:
    """Single sequential step declared by a v1 manifest.

    Attributes:
        id: Stable identifier for the step, referenced by later placeholders.
        name: Human-readable step name.
        request: HTTP request to execute (may contain ``${...}`` placeholders).
        assertions: Assertions evaluated against the step response.
    """

    id: str
    name: str
    request: ManifestRequest
    assertions: tuple[ManifestAssertion, ...]


@dataclass(frozen=True)
class Manifest:
    """Validated conformance manifest (v0 or v1).

    Attributes:
        schema_version: Manifest schema version accepted by this parser.
        name: Human-readable manifest name.
        tests: Ordered manifest tests to execute (v0 only, empty for v1).
        steps: Ordered sequential steps to execute (v1 only, empty for v0).
    """

    schema_version: ManifestSchemaVersion
    name: str
    tests: tuple[ManifestTest, ...] = ()
    steps: tuple[ManifestStep, ...] = ()


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
    """Parse a raw JSON object into a validated manifest (v0 or v1).

    Args:
        raw_manifest: JSON object loaded from a conformance manifest file.

    Returns:
        Parsed and validated conformance manifest.

    Raises:
        ManifestError: If required fields are missing or validation fails.
    """
    schema_version = _required_string(raw_manifest, "schemaVersion", location="manifest")
    if schema_version == "v0":
        return _parse_v0_manifest(raw_manifest)
    if schema_version == "v1":
        return _parse_v1_manifest(raw_manifest)
    raise ManifestError("schemaVersion must be v0 or v1")


def _parse_v0_manifest(raw_manifest: dict[str, JsonValue]) -> Manifest:
    """Parse a raw JSON object into a validated v0 manifest.

    Args:
        raw_manifest: JSON object loaded from a conformance manifest file.

    Returns:
        Parsed and validated v0 conformance manifest.

    Raises:
        ManifestError: If required fields are missing or validation fails.
    """
    _reject_unknown_keys(raw_manifest, allowed_keys={"schemaVersion", "name", "tests"}, location="manifest")

    name = _required_string(raw_manifest, "name", location="manifest")
    tests = _required_object_array(raw_manifest, "tests", location="manifest")

    return Manifest(
        schema_version="v0",
        name=name,
        tests=tuple(_parse_test(raw_test, index=index) for index, raw_test in enumerate(tests)),
    )


_STEP_ID_CHAR_CLASS = r"[A-Za-z0-9][A-Za-z0-9_-]*"
"""Character class for valid step/test IDs (excludes dot to avoid resolver ambiguity)."""

_PLACEHOLDER_PATTERN = re.compile(
    r"\$\{steps\.(" + _STEP_ID_CHAR_CLASS + r")"
    r"\.(request|response)\.(body|status_code|method|url)"
    r"((?:\.[A-Za-z0-9_-]+)*)\}"
)
"""Regex matching valid ``${steps.<id>.<request|response>.<segment>...}`` placeholders."""

_PLACEHOLDER_FIND_PATTERN = re.compile(r"\$\{[^}]*\}")
"""Regex matching any ``${...}`` token for syntax validation."""


def _parse_v1_manifest(raw_manifest: dict[str, JsonValue]) -> Manifest:
    """Parse a raw JSON object into a validated v1 manifest.

    Args:
        raw_manifest: JSON object loaded from a conformance manifest file.

    Returns:
        Parsed and validated v1 conformance manifest with sequential steps.

    Raises:
        ManifestError: If required fields are missing or validation fails.
    """
    _reject_unknown_keys(raw_manifest, allowed_keys={"schemaVersion", "name", "steps"}, location="manifest")

    name = _required_string(raw_manifest, "name", location="manifest")
    raw_steps = _required_object_array(raw_manifest, "steps", location="manifest")

    seen_ids: set[str] = set()
    steps: list[ManifestStep] = []
    for index, raw_step in enumerate(raw_steps):
        step = _parse_v1_step(raw_step, index=index, seen_ids=seen_ids)
        seen_ids.add(step.id)
        steps.append(step)

    return Manifest(
        schema_version="v1",
        name=name,
        steps=tuple(steps),
    )


def _parse_v1_step(raw_step: dict[str, JsonValue], *, index: int, seen_ids: set[str]) -> ManifestStep:
    """Parse a single step entry from the v1 manifest steps array.

    Args:
        raw_step: Raw JSON object representing one manifest step.
        index: Zero-based position in the steps array, used for error locations.
        seen_ids: Set of step ids already parsed (for duplicate/forward-ref detection).

    Returns:
        Validated manifest step with request and assertions.

    Raises:
        ManifestError: If required fields are missing, ids are duplicated, or
            placeholders reference forward/unknown steps.
    """
    location = f"steps[{index}]"
    _reject_unknown_keys(
        raw_step,
        allowed_keys={"id", "name", "request", "assertions"},
        location=location,
    )

    step_id = _required_string(raw_step, "id", location=location)
    _validate_step_id(step_id, location=location)
    if step_id in seen_ids:
        raise ManifestError(f"{location}.id '{step_id}' is a duplicate")

    step_name = _required_string(raw_step, "name", location=location)
    request = _parse_v1_request(
        _required_object(raw_step, "request", location=location),
        location=f"{location}.request",
        seen_ids=seen_ids,
    )
    assertions = _required_object_array(raw_step, "assertions", location=location)

    return ManifestStep(
        id=step_id,
        name=step_name,
        request=request,
        assertions=tuple(
            _parse_assertion(raw_assertion, location=f"{location}.assertions[{assertion_index}]")
            for assertion_index, raw_assertion in enumerate(assertions)
        ),
    )


def _parse_v1_request(raw_request: dict[str, JsonValue], *, location: str, seen_ids: set[str]) -> ManifestRequest:
    """Parse and validate a v1 manifest step request object.

    Unlike the v0 parser, this allows ``${...}`` placeholders in the URL field.
    HTTPS validation is deferred to execution time for URLs containing placeholders.
    URLs without placeholders are validated immediately.

    Args:
        raw_request: Raw JSON object expected to contain ``method`` and ``url``.
        location: Dot-path location string used in error messages.
        seen_ids: Set of step ids already parsed (for forward-ref detection).

    Returns:
        Validated request with a GET method and a URL (possibly containing
        placeholders).

    Raises:
        ManifestError: If required fields are missing, invalid, or placeholders
            reference forward steps.
    """
    _reject_unknown_keys(raw_request, allowed_keys={"method", "url"}, location=location)
    method = _required_get_method(raw_request, location=location)
    url = _required_string(raw_request, "url", location=location)

    _validate_placeholder_syntax(url, location=f"{location}.url", seen_ids=seen_ids)

    # Only validate HTTPS if there are no placeholders (deferred otherwise)
    if not _PLACEHOLDER_FIND_PATTERN.search(url):
        try:
            validate_https_url(url, label=f"{location}.url")
        except HttpsUrlValidationError as error:
            raise ManifestError(str(error)) from error

    return ManifestRequest(method=method, url=url)


def _validate_placeholder_syntax(value: str, *, location: str, seen_ids: set[str]) -> None:
    """Validate that all ``${...}`` tokens in a string are syntactically correct.

    Checks that each placeholder matches the canonical grammar and that any
    referenced step id exists in ``seen_ids`` (i.e. no forward references).

    Args:
        value: String potentially containing ``${...}`` placeholders.
        location: Dot-path location string used in error messages.
        seen_ids: Set of step ids already parsed (for forward-ref detection).

    Raises:
        ManifestError: If a placeholder is malformed or references a forward step.
    """
    for match in _PLACEHOLDER_FIND_PATTERN.finditer(value):
        token = match.group(0)
        if not _PLACEHOLDER_PATTERN.fullmatch(token):
            raise ManifestError(f"{location} contains malformed placeholder: {token}")
        # Extract the step id from the valid placeholder
        valid_match = _PLACEHOLDER_PATTERN.fullmatch(token)
        assert valid_match is not None  # noqa: S101 — guaranteed by the fullmatch above
        referenced_id = valid_match.group(1)
        if referenced_id not in seen_ids:
            raise ManifestError(f"{location} references undefined step '{referenced_id}'")


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

    test_id = _required_string(raw_test, "id", location=location)
    _validate_step_id(test_id, location=location)

    return ManifestTest(
        id=test_id,
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
    """Parse and validate a manifest test request object.

    Args:
        raw_request: Raw JSON object expected to contain ``method`` and ``url``.
        location: Dot-path location string used in error messages.

    Returns:
        Validated request with a GET method and a hardened HTTPS URL.

    Raises:
        ManifestError: If required fields are missing, invalid, or unknown keys
            are present.
    """
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
    """Parse the request object inside a manifest follow-up step.

    Args:
        raw_request: Raw JSON object expected to contain only ``method``.
        location: Dot-path location string used in error messages.

    Returns:
        Validated follow-up request with a GET method.

    Raises:
        ManifestError: If the method is missing, not GET, or unknown keys
            are present.
    """
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

    Args:
        raw_assertion: Raw assertion dict from the manifest JSON.
        location: Dot-path location string used in error messages.

    Returns:
        A validated assertion type literal (``http_status`` or ``json_field``).

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

    Args:
        raw_config: The parent JSON object containing a ``method`` field.
        location: Dot-path location string used in error messages.

    Returns:
        The literal string ``"GET"``.

    Raises:
        ManifestError: If the method is missing or not GET.
    """
    method = _required_string(raw_config, "method", location=location)
    if method != "GET":
        raise ManifestError(f"{location}.method must be GET")
    return "GET"


def _required_json_field_rule(raw_assertion: dict[str, JsonValue], *, location: str) -> JsonFieldRule:
    """Extract and validate the JSON field assertion rule.

    Args:
        raw_assertion: Raw assertion dict from the manifest JSON.
        location: Dot-path location string used in error messages.

    Returns:
        A validated rule literal (``required``, ``https_url``, or ``array``).

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

    Args:
        raw_assertion: Raw assertion dict expected to contain an ``expected`` field.
        location: Dot-path location string used in error messages.

    Returns:
        An integer HTTP status code in the 100–599 range.

    Raises:
        ManifestError: If the value is not an HTTP status code.
    """
    value = raw_assertion.get("expected")
    if not isinstance(value, int) or isinstance(value, bool) or value < 100 or value > 599:
        raise ManifestError(f"{location}.expected must be an HTTP status code")
    return value


def _required_string(raw_config: dict[str, JsonValue], key: str, *, location: str) -> str:
    """Extract a required non-empty string from a JSON object.

    Args:
        raw_config: The parent JSON object to extract from.
        key: The key to look up in the object.
        location: Dot-path location string used in error messages.

    Returns:
        The stripped, non-empty string value.

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

    Args:
        raw_config: The parent JSON object to extract from.
        key: The key to look up in the object.
        location: Dot-path location string used in error messages.

    Returns:
        The validated HTTPS URL string.

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

    Args:
        raw_config: The parent JSON object to extract from.
        key: The key to look up in the object.
        location: Dot-path location string used in error messages.

    Returns:
        The nested JSON object (dict).

    Raises:
        ManifestError: If the value is missing or not a JSON object.
    """
    value = raw_config.get(key)
    if not isinstance(value, dict):
        raise ManifestError(f"{location}.{key} must be a JSON object")
    return value


def _required_object_array(raw_config: dict[str, JsonValue], key: str, *, location: str) -> list[dict[str, JsonValue]]:
    """Extract a required non-empty array of JSON objects.

    Args:
        raw_config: The parent JSON object to extract from.
        key: The key to look up in the object.
        location: Dot-path location string used in error messages.

    Returns:
        A non-empty list of validated JSON object dicts.

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


_STEP_ID_PATTERN = re.compile(r"^" + _STEP_ID_CHAR_CLASS + r"$")
"""Compiled pattern for validating step/test IDs at parse time."""


def _validate_step_id(step_id: str, *, location: str) -> None:
    """Validate that a step or test ID uses only allowed characters.

    IDs must start with an alphanumeric character and may contain only
    alphanumerics, hyphens, and underscores. Dots are forbidden because
    the placeholder resolver splits on dots.

    Args:
        step_id: The candidate ID string to validate.
        location: Dot-path location string used in error messages.

    Raises:
        ManifestError: If the ID contains invalid characters.
    """
    if not _STEP_ID_PATTERN.match(step_id):
        raise ManifestError(
            f"{location}.id '{step_id}' contains invalid characters "
            "(must match [A-Za-z0-9][A-Za-z0-9_-]*)"
        )


def _reject_unknown_keys(raw_config: dict[str, JsonValue], *, allowed_keys: set[str], location: str) -> None:
    """Raise if the JSON object contains keys outside the allowed set.

    Args:
        raw_config: The JSON object to validate.
        allowed_keys: Set of permitted key names.
        location: Dot-path location string used in error messages.

    Raises:
        ManifestError: If any keys are outside the allowed set.
    """
    unknown_keys = sorted(set(raw_config) - allowed_keys)
    if unknown_keys:
        joined_keys = ", ".join(unknown_keys)
        raise ManifestError(f"Unknown {location} field(s): {joined_keys}")
