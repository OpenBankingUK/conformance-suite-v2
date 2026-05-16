from __future__ import annotations

import json
from dataclasses import dataclass
from ipaddress import ip_address
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from conformance.json_types import JsonValue


class ManifestError(ValueError):
    """Raised when a conformance manifest cannot be loaded or validated."""


ManifestSchemaVersion = Literal["v0"]
RequestMethod = Literal["GET"]
AssertionType = Literal["http_status", "json_field"]
JsonFieldRule = Literal["required", "https_url", "array"]
FollowUpType = Literal["jwks"]
FollowUpUrlSource = Literal["response.body.jwks_uri"]


@dataclass(frozen=True)
class ManifestRequest:
    method: RequestMethod
    url: str


@dataclass(frozen=True)
class FollowUpRequest:
    method: RequestMethod


@dataclass(frozen=True)
class HttpStatusAssertion:
    type: Literal["http_status"]
    expected: int


@dataclass(frozen=True)
class JsonFieldAssertion:
    type: Literal["json_field"]
    path: str
    rule: JsonFieldRule


ManifestAssertion = HttpStatusAssertion | JsonFieldAssertion


@dataclass(frozen=True)
class ManifestFollowUp:
    type: FollowUpType
    url_source: FollowUpUrlSource
    request: FollowUpRequest
    assertions: tuple[ManifestAssertion, ...]


@dataclass(frozen=True)
class ManifestTest:
    id: str
    name: str
    request: ManifestRequest
    assertions: tuple[ManifestAssertion, ...]
    follow_up: ManifestFollowUp | None = None


@dataclass(frozen=True)
class Manifest:
    schema_version: ManifestSchemaVersion
    name: str
    tests: tuple[ManifestTest, ...]


def load_manifest(manifest_path: Path) -> Manifest:
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
    _reject_unknown_keys(raw_request, allowed_keys={"method", "url"}, location=location)
    return ManifestRequest(
        method=_required_get_method(raw_request, location=location),
        url=_required_https_url(raw_request, "url", location=location),
    )


def _parse_follow_up(raw_follow_up: JsonValue, *, location: str) -> ManifestFollowUp:
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
    _reject_unknown_keys(raw_request, allowed_keys={"method"}, location=location)
    return FollowUpRequest(method=_required_get_method(raw_request, location=location))


def _parse_assertion(raw_assertion: dict[str, JsonValue], *, location: str) -> ManifestAssertion:
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
    assertion_type = _required_string(raw_assertion, "type", location=location)
    if assertion_type == "http_status":
        return "http_status"
    if assertion_type == "json_field":
        return "json_field"
    raise ManifestError(f"{location}.type must be one of: http_status, json_field")


def _required_get_method(raw_config: dict[str, JsonValue], *, location: str) -> RequestMethod:
    method = _required_string(raw_config, "method", location=location)
    if method != "GET":
        raise ManifestError(f"{location}.method must be GET")
    return "GET"


def _required_json_field_rule(raw_assertion: dict[str, JsonValue], *, location: str) -> JsonFieldRule:
    rule = _required_string(raw_assertion, "rule", location=location)
    if rule == "required":
        return "required"
    if rule == "https_url":
        return "https_url"
    if rule == "array":
        return "array"
    raise ManifestError(f"{location}.rule must be one of: required, https_url, array")


def _required_status_code(raw_assertion: dict[str, JsonValue], *, location: str) -> int:
    value = raw_assertion.get("expected")
    if not isinstance(value, int) or isinstance(value, bool) or value < 100 or value > 599:
        raise ManifestError(f"{location}.expected must be an HTTP status code")
    return value


def _required_string(raw_config: dict[str, JsonValue], key: str, *, location: str) -> str:
    value = raw_config.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ManifestError(f"{location}.{key} must be a non-empty string")
    return value.strip()


def _required_https_url(raw_config: dict[str, JsonValue], key: str, *, location: str) -> str:
    value = _required_string(raw_config, key, location=location)
    if any(character.isspace() or ord(character) < 32 or ord(character) == 127 for character in value):
        raise ManifestError(f"{location}.{key} must be a valid HTTPS URL")

    parsed_url = urlparse(value)
    try:
        parsed_port = parsed_url.port
    except ValueError as error:
        raise ManifestError(f"{location}.{key} must be a valid HTTPS URL") from error

    if parsed_port is not None and parsed_port <= 0:
        raise ManifestError(f"{location}.{key} must be a valid HTTPS URL")
    if parsed_url.scheme != "https" or parsed_url.hostname is None:
        raise ManifestError(f"{location}.{key} must be an HTTPS URL")
    if parsed_url.username is not None or parsed_url.password is not None:
        raise ManifestError(f"{location}.{key} must not include credentials")
    _validate_hostname(parsed_url.hostname, location=f"{location}.{key}")
    return value


def _validate_hostname(hostname: str | None, *, location: str) -> None:
    if hostname is None:
        raise ManifestError(f"{location} must be an HTTPS URL")
    try:
        ip_address(hostname)
    except ValueError:
        _validate_dns_hostname(hostname, location=location)


def _validate_dns_hostname(hostname: str, *, location: str) -> None:
    try:
        hostname.encode("ascii")
    except UnicodeEncodeError as error:
        raise ManifestError(f"{location} must be a valid HTTPS URL") from error

    trimmed_hostname = hostname.removesuffix(".")
    labels = trimmed_hostname.split(".")
    if not trimmed_hostname or len(trimmed_hostname) > 253:
        raise ManifestError(f"{location} must be a valid HTTPS URL")
    for label in labels:
        if not label or len(label) > 63 or label.startswith("-") or label.endswith("-"):
            raise ManifestError(f"{location} must be a valid HTTPS URL")
        if not all(character.isalnum() or character == "-" for character in label):
            raise ManifestError(f"{location} must be a valid HTTPS URL")


def _required_object(raw_config: dict[str, JsonValue], key: str, *, location: str) -> dict[str, JsonValue]:
    value = raw_config.get(key)
    if not isinstance(value, dict):
        raise ManifestError(f"{location}.{key} must be a JSON object")
    return value


def _required_object_array(raw_config: dict[str, JsonValue], key: str, *, location: str) -> list[dict[str, JsonValue]]:
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
    unknown_keys = sorted(set(raw_config) - allowed_keys)
    if unknown_keys:
        joined_keys = ", ".join(unknown_keys)
        raise ManifestError(f"Unknown {location} field(s): {joined_keys}")
