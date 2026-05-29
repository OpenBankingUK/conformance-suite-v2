"""Load and validate v0/v1 conformance manifest files."""

from __future__ import annotations

import copy
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Literal, cast

from conformance.json_types import JsonValue
from conformance.url_validation import HttpsUrlValidationError, validate_https_url


class ManifestError(ValueError):
    """Raised when a conformance manifest cannot be loaded or validated."""


ManifestSchemaVersion = Literal["v0", "v1"]
"""Manifest schema versions accepted by the parser."""

RequestMethod = Literal["GET", "POST", "PUT", "PATCH", "DELETE"]
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
class JsonBody:
    """Manifest request body sent as ``application/json``.

    The default body shape for v1 manifests. A bare body value
    (no ``encoding`` tag) is also parsed into ``JsonBody`` for
    backwards compatibility with the original DL-0013 contract.

    Attributes:
        value: JSON value sent verbatim as the request body. String leaves
            may contain ``${...}`` placeholders that the executor resolves
            against the execution context before dispatch.
    """

    value: JsonValue


@dataclass(frozen=True)
class FormBody:
    """Manifest request body sent as ``application/x-www-form-urlencoded``.

    Used by OAuth 2.0 token-exchange and similar flows where the wire format
    is form-urlencoded rather than JSON. The executor sets ``Content-Type:
    application/x-www-form-urlencoded`` automatically only when the manifest
    has not supplied a ``Content-Type`` header (case-insensitive per RFC
    7230). Encoding is delegated to ``httpx`` (never hand-rolled).

    Attributes:
        fields: Mapping of form field name to value. Both names and values
            are strings; placeholder substitution applies to each value
            before dispatch. Stored as a read-only ``MappingProxyType`` so
            the parsed body cannot be mutated after parse time.
    """

    fields: Mapping[str, str]


type ManifestBody = JsonBody | FormBody
"""Discriminated request body shape carried by ``ManifestRequest.body``."""


@dataclass(frozen=True)
class ManifestRequest:
    """HTTP request declared by a manifest test.

    Attributes:
        method: HTTP method used for the manifest request.
        url: HTTPS URL fetched for the manifest test.
        headers: Optional string-valued headers to send with the request.
        body: Optional typed body (JSON or form-urlencoded). Allowed on
            POST/PUT/PATCH/DELETE; rejected on GET at parse time.
    """

    method: RequestMethod
    url: str
    headers: dict[str, str] | None = None
    body: ManifestBody | None = None


@dataclass(frozen=True)
class FollowUpRequest:
    """HTTP request shape for a manifest follow-up step.

    v0 follow-ups are always GET requests. The narrow type enforces the
    contract at the type level; ``_required_get_method`` enforces it at
    parse time.

    Attributes:
        method: HTTP method used for the follow-up request (always GET).
    """

    method: Literal["GET"]


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
"""Assertion variants accepted by manifest tests and sequential steps (v0 and v1)."""


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
        warning: Optional deprecation or risk message. When present and the step
            would otherwise pass, the executor emits a ``warn`` outcome instead
            of ``passed`` and surfaces this message in the step result. Per the
            PRD, ``warn`` does not block certification.
        mandatory: Whether this step is required for certification eligibility.
            Defaults to ``False``. Per the PRD's Certification Eligibility
            Assessment, a run is eligible for certification submission only
            when every mandatory step passed (``warn`` is non-blocking, but
            ``failed`` and ``skipped`` are blocking). Mandatory status is
            defined per spec version and standard in manifest configuration —
            never hardcoded — so OBL Standards can adjust mandatory coverage
            without an engine release.
    """

    id: str
    name: str
    request: ManifestRequest
    assertions: tuple[ManifestAssertion, ...]
    warning: str | None = None
    mandatory: bool = False


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

_HEADER_NAME_PATTERN = re.compile(r"^[A-Za-z0-9!#$%&'*+\-.^_`|~]+$")
"""RFC 7230 token pattern for valid HTTP header field names."""

_HEADER_VALUE_INVALID_PATTERN = re.compile(r"[^\x09\x20-\x7e]")
"""Pattern matching characters not transportable as HTTP header field values.

httpx encodes string header values as ASCII, so only the ASCII-safe subset
of RFC 7230 §3.2.6 field content is transportable: HTAB (0x09), SP (0x20),
and VCHAR (0x21-0x7E). The RFC's obs-text range (0x80-0xFF) is rejected
because it cannot be transmitted without a UnicodeEncodeError at the
transport layer.
"""

_PLACEHOLDER_PATTERN = re.compile(
    r"\$\{steps\.(" + _STEP_ID_CHAR_CLASS + r")"
    r"\.(?:"
    r"request\.(?:method|url)"
    r"|"
    r"response\.(?:status_code|body(?:\.[A-Za-z0-9_-]+)+)"
    r")\}"
)
"""Regex matching valid ``${steps.<id>...}`` placeholders with direction-specific rules.

Request direction accepts: ``method``, ``url`` (no sub-segments).
Response direction accepts: ``status_code`` (no sub-segments), ``body.<path>`` (at least one segment).
"""

_PLACEHOLDER_FIND_PATTERN = re.compile(r"\$\{[^}]*\}")
"""Regex matching any ``${...}`` token for syntax validation."""


def validate_header_value(value: str, *, location: str) -> None:
    """Validate an HTTP header field value for transport safety.

    Rejects empty/whitespace-only values and values containing characters
    that cannot be transmitted by httpx (which encodes headers as ASCII).
    Permitted characters are HTAB (0x09), SP (0x20), and VCHAR (0x21-0x7E).

    The RFC 7230 §3.2.6 obs-text range (0x80-0xFF) is intentionally excluded
    because httpx raises ``UnicodeEncodeError`` for non-ASCII str header
    values. This restriction ensures all validated values are transportable.

    This function is used both at manifest parse time (static values) and
    after placeholder resolution (dynamic values) to ensure no invalid
    characters reach the HTTP transport layer.

    Args:
        value: The header field value to validate.
        location: Dot-path location string used in error messages.

    Raises:
        ManifestError: If the value is empty or contains non-transportable
            characters.
    """
    if not value.strip():
        raise ManifestError(f"{location} must not be empty")
    match = _HEADER_VALUE_INVALID_PATTERN.search(value)
    if match:
        bad_char = match.group()
        code_point = ord(bad_char)
        raise ManifestError(
            f"{location} contains non-transportable character U+{code_point:04X} "
            "(only HTAB, SP, and VCHAR 0x21-0x7E are permitted)"
        )


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
        allowed_keys={"id", "name", "request", "assertions", "warning", "mandatory"},
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
    warning = _parse_optional_warning(raw_step, location=location)
    mandatory = _parse_optional_mandatory(raw_step, location=location)

    return ManifestStep(
        id=step_id,
        name=step_name,
        request=request,
        assertions=tuple(
            _parse_assertion(raw_assertion, location=f"{location}.assertions[{assertion_index}]")
            for assertion_index, raw_assertion in enumerate(assertions)
        ),
        warning=warning,
        mandatory=mandatory,
    )


def _parse_v1_request(raw_request: dict[str, JsonValue], *, location: str, seen_ids: set[str]) -> ManifestRequest:
    """Parse and validate a v1 manifest step request object.

    Unlike the v0 parser, this allows ``${...}`` placeholders in the URL field,
    header values, and body string leaves. Supports GET, POST, PUT, PATCH, and
    DELETE methods. Body is rejected on GET requests.

    Args:
        raw_request: Raw JSON object expected to contain ``method``, ``url``,
            and optionally ``headers`` and ``body``.
        location: Dot-path location string used in error messages.
        seen_ids: Set of step ids already parsed (for forward-ref detection).

    Returns:
        Validated request with method, URL, optional headers, and optional body
        (body may contain placeholders in string leaves).

    Raises:
        ManifestError: If required fields are missing, invalid, or placeholders
            reference forward steps.
    """
    _reject_unknown_keys(raw_request, allowed_keys={"method", "url", "headers", "body"}, location=location)
    method = _required_v1_method(raw_request, location=location)
    url = _required_string(raw_request, "url", location=location)

    _validate_placeholder_syntax(url, location=f"{location}.url", seen_ids=seen_ids)

    # Only validate HTTPS if there are no placeholders (deferred otherwise)
    if not _PLACEHOLDER_FIND_PATTERN.search(url):
        try:
            validate_https_url(url, label=f"{location}.url")
        except HttpsUrlValidationError as error:
            raise ManifestError(str(error)) from error

    # Parse optional headers
    headers = _parse_v1_headers(raw_request, location=location, seen_ids=seen_ids)

    # Parse optional body
    body = _parse_v1_body(raw_request, method=method, location=location, seen_ids=seen_ids)

    return ManifestRequest(method=method, url=url, headers=headers, body=body)


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
    matched_tokens = list(_PLACEHOLDER_FIND_PATTERN.finditer(value))
    if value.count("${") > len(matched_tokens):
        raise ManifestError(f"{location} contains an unterminated placeholder (missing closing '}}')")
    for match in matched_tokens:
        token = match.group(0)
        valid_match = _PLACEHOLDER_PATTERN.fullmatch(token)
        if valid_match is None:
            raise ManifestError(f"{location} contains malformed placeholder: {token}")
        referenced_id = valid_match.group(1)
        if referenced_id not in seen_ids:
            raise ManifestError(f"{location} references undefined step '{referenced_id}'")


def _required_v1_method(raw_config: dict[str, JsonValue], *, location: str) -> RequestMethod:
    """Extract and validate the request method for a v1 step.

    Accepts GET, POST, PUT, PATCH, and DELETE.

    Args:
        raw_config: The parent JSON object containing a ``method`` field.
        location: Dot-path location string used in error messages.

    Returns:
        A validated HTTP method literal.

    Raises:
        ManifestError: If the method is missing or not one of the supported values.
    """
    method = _required_string(raw_config, "method", location=location)
    allowed: set[str] = {"GET", "POST", "PUT", "PATCH", "DELETE"}
    if method not in allowed:
        raise ManifestError(f"{location}.method must be one of: GET, POST, PUT, PATCH, DELETE")
    return cast(RequestMethod, method)


def _parse_v1_headers(raw_request: dict[str, JsonValue], *, location: str, seen_ids: set[str]) -> dict[str, str] | None:
    """Parse and validate optional headers from a v1 step request.

    Header names must be RFC 7230 tokens. Header values must be non-empty
    strings (may contain ``${...}`` placeholders).

    Args:
        raw_request: Raw request JSON object potentially containing ``headers``.
        location: Dot-path location string used in error messages.
        seen_ids: Set of step ids already parsed (for forward-ref detection).

    Returns:
        A dict mapping header names to string values, or ``None`` if no
        headers key is present.

    Raises:
        ManifestError: If header names or values are invalid, or if
            placeholders reference forward steps.
    """
    if "headers" not in raw_request:
        return None
    raw_headers = raw_request["headers"]
    if not isinstance(raw_headers, dict):
        raise ManifestError(f"{location}.headers must be a JSON object")

    headers: dict[str, str] = {}
    for name, value in raw_headers.items():
        header_location = f"{location}.headers.{name}"
        if not _HEADER_NAME_PATTERN.match(name):
            raise ManifestError(f"{header_location} is not a valid HTTP header name (RFC 7230 token)")
        if not isinstance(value, str):
            raise ManifestError(f"{header_location} must be a string value")
        validate_header_value(value, location=header_location)
        _validate_placeholder_syntax(value, location=header_location, seen_ids=seen_ids)
        headers[name] = value
    return headers


def _parse_v1_body(
    raw_request: dict[str, JsonValue], *, method: RequestMethod, location: str, seen_ids: set[str]
) -> ManifestBody | None:
    """Parse and validate the optional body from a v1 step request.

    Body is rejected on GET requests. Two body shapes are accepted:

    1. **Bare JSON value** (no ``encoding`` tag): parsed as ``JsonBody``.
       Preserves DL-0013 back-compat — any v1 manifest written before
       DL-0014 keeps working without change.
    2. **Tagged dict** ``{"encoding": "json" | "form", ...}``: parsed as
       ``JsonBody`` (with required ``value``) or ``FormBody`` (with
       required non-empty ``fields`` mapping of string→string).

    The tagged-vs-bare discrimination is conservative: only a dict that
    contains an ``encoding`` key is treated as tagged. A bare dict without
    ``encoding`` is still a JSON body — manifests that happen to send a
    JSON object with no ``encoding`` field continue to work.

    Placeholder syntax is validated in:
    - every string leaf of a JSON body (recursively, via
      ``_validate_placeholders_in_structure``), and
    - every value of a form body's ``fields`` mapping.

    Args:
        raw_request: Raw request JSON object potentially containing ``body``.
        method: The parsed HTTP method (used to reject body on GET).
        location: Dot-path location string used in error messages.
        seen_ids: Set of step ids already parsed (for forward-ref detection).

    Returns:
        A ``JsonBody`` or ``FormBody``, or ``None`` if no body key is present.

    Raises:
        ManifestError: If body is present on a GET request, the tagged shape
            is malformed, ``encoding`` is unknown, ``fields`` are empty or
            contain non-string values, or any placeholder is invalid.
    """
    if "body" not in raw_request:
        return None
    if method == "GET":
        raise ManifestError(f"{location}: GET requests must not declare a body")
    body = raw_request["body"]
    if body is None:
        raise ManifestError(f"{location}.body must not be null (omit the key to send no body)")

    body_location = f"{location}.body"

    # Tagged shape: only triggered when body is a dict carrying an
    # ``encoding`` key. Bare JSON objects without ``encoding`` remain
    # JsonBody for back-compat with DL-0013 manifests.
    if isinstance(body, dict) and "encoding" in body:
        return _parse_tagged_body(body, location=body_location, seen_ids=seen_ids)

    # Bare body: implicit JSON (DL-0013 behaviour preserved).
    _validate_placeholders_in_structure(body, location=body_location, seen_ids=seen_ids)
    # Deep-copy so the parsed ``ManifestRequest`` owns its body. Without this,
    # the frozen dataclass would alias mutable JSON structures from the raw
    # manifest dict, and any post-parse mutation of the input could bypass
    # parse-time placeholder validation and change what the executor sends.
    return JsonBody(value=copy.deepcopy(body))


def _parse_tagged_body(body: dict[str, JsonValue], *, location: str, seen_ids: set[str]) -> ManifestBody:
    """Parse a ``{"encoding": ..., ...}`` tagged body dict into a typed body.

    Args:
        body: The raw body dict, known to contain an ``encoding`` key.
        location: Dot-path location string used in error messages.
        seen_ids: Set of step ids already parsed (for forward-ref detection).

    Returns:
        A typed ``JsonBody`` or ``FormBody``.

    Raises:
        ManifestError: If ``encoding`` is unknown, required tagged-shape keys
            are missing, ``fields`` are empty or non-string-valued, or any
            placeholder inside the body is invalid.
    """
    encoding = body.get("encoding")
    if encoding == "json":
        _reject_unknown_keys(body, allowed_keys={"encoding", "value"}, location=location)
        if "value" not in body:
            raise ManifestError(f"{location}: tagged JSON body must include a 'value' key")
        value = body["value"]
        if value is None:
            raise ManifestError(f"{location}.value must not be null (omit the body key to send no body)")
        _validate_placeholders_in_structure(value, location=f"{location}.value", seen_ids=seen_ids)
        return JsonBody(value=copy.deepcopy(value))
    if encoding == "form":
        _reject_unknown_keys(body, allowed_keys={"encoding", "fields"}, location=location)
        if "fields" not in body:
            raise ManifestError(f"{location}: form body must include a 'fields' object")
        raw_fields = body["fields"]
        if not isinstance(raw_fields, dict):
            raise ManifestError(f"{location}.fields must be a JSON object")
        if not raw_fields:
            raise ManifestError(f"{location}.fields must not be empty")
        validated_fields: dict[str, str] = {}
        for field_name, field_value in raw_fields.items():
            field_location = f"{location}.fields.{field_name}"
            if not field_name:
                raise ManifestError(f"{location}.fields contains an empty field name")
            if not isinstance(field_value, str):
                raise ManifestError(f"{field_location} must be a string value")
            _validate_placeholder_syntax(field_value, location=field_location, seen_ids=seen_ids)
            validated_fields[field_name] = field_value
        # Freeze the parsed fields against post-parse mutation. The dict is
        # built locally so deep-copy is unnecessary; MappingProxyType keeps
        # the public Mapping read-only.
        return FormBody(fields=MappingProxyType(validated_fields))
    raise ManifestError(f"{location}.encoding must be one of: json, form (got: {encoding!r})")


def _validate_placeholders_in_structure(value: JsonValue, *, location: str, seen_ids: set[str]) -> None:
    """Recursively validate placeholders in all string leaves of a JSON structure.

    Walks dicts and lists depth-first, checking each string leaf for valid
    ``${...}`` placeholder syntax and forward-reference violations.

    Args:
        value: JSON value (possibly nested) to validate.
        location: Dot-path location string used in error messages.
        seen_ids: Set of step ids already parsed (for forward-ref detection).

    Raises:
        ManifestError: If any string leaf contains malformed or forward-referencing
            placeholders.
    """
    if isinstance(value, str):
        _validate_placeholder_syntax(value, location=location, seen_ids=seen_ids)
    elif isinstance(value, dict):
        for key, child in value.items():
            _validate_placeholders_in_structure(child, location=f"{location}.{key}", seen_ids=seen_ids)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _validate_placeholders_in_structure(child, location=f"{location}[{index}]", seen_ids=seen_ids)


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
    # Defensive: _required_assertion_type already constrains assertion_type to the
    # AssertionType literal, but an explicit raise removes the implicit None
    # fall-through and guards against future literal additions.
    raise ManifestError(f"{location}.type has unexpected value: {assertion_type!r}")


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


def _required_get_method(raw_config: dict[str, JsonValue], *, location: str) -> Literal["GET"]:
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


def _parse_optional_warning(raw_step: dict[str, JsonValue], *, location: str) -> str | None:
    """Extract an optional ``warning`` message from a v1 step.

    The ``warning`` field is optional. When absent, ``None`` is returned. When
    present it must be a non-empty string (after stripping). An empty or
    whitespace-only string is rejected to fail fast on misauthored manifests
    rather than silently emit an empty warning at runtime.

    Args:
        raw_step: Raw JSON object for the step.
        location: Dot-path location string used in error messages.

    Returns:
        The stripped warning message, or ``None`` if no ``warning`` key was set.

    Raises:
        ManifestError: If ``warning`` is present but is not a non-empty string.
    """
    if "warning" not in raw_step:
        return None
    value = raw_step["warning"]
    if not isinstance(value, str) or not value.strip():
        raise ManifestError(f"{location}.warning must be a non-empty string when present")
    return value.strip()


def _parse_optional_mandatory(raw_step: dict[str, JsonValue], *, location: str) -> bool:
    """Extract the optional ``mandatory`` flag from a v1 step.

    The ``mandatory`` field is optional. When absent, ``False`` is returned
    (steps are opt-in to mandatory coverage). When present it must be a JSON
    boolean. Truthy/falsy coercion is intentionally rejected so that integer,
    string, or ``null`` values fail fast at parse time rather than silently
    flip certification eligibility on a misauthored manifest.

    Args:
        raw_step: Raw JSON object for the step.
        location: Dot-path location string used in error messages.

    Returns:
        ``True`` if the step is mandatory for certification, ``False`` otherwise.

    Raises:
        ManifestError: If ``mandatory`` is present but is not a JSON boolean.
    """
    if "mandatory" not in raw_step:
        return False
    value = raw_step["mandatory"]
    # ``isinstance(value, bool)`` is required: in Python ``bool`` is a
    # subclass of ``int``, so a bare ``isinstance(value, int)`` would also
    # admit integers. We want to reject ``1``/``0`` and other truthy values.
    if not isinstance(value, bool):
        raise ManifestError(f"{location}.mandatory must be a JSON boolean when present")
    return value


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
            f"{location}.id '{step_id}' contains invalid characters (must match [A-Za-z0-9][A-Za-z0-9_-]*)"
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
