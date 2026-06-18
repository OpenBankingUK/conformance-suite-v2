"""Validate response payloads against allowlisted bundled schema documents.

This helper targets bundled Open Banking Read/Write AIS OpenAPI snapshots.
It resolves local ``#/...`` references, translates ``nullable`` into plain JSON
Schema union types, and returns deterministic validation diagnostics for use in
manifest assertion failures.
"""

from __future__ import annotations

import copy
import json
import re
from collections.abc import Mapping, Sequence
from functools import cache
from pathlib import Path

from jsonschema import (  # type: ignore[import-untyped]  # jsonschema lacks bundled stubs.
    Draft4Validator,
    ValidationError,
)

from conformance.json_types import JsonObject, JsonValue


class SchemaValidationConfigurationError(Exception):
    """Raised when a configured response schema cannot be loaded or resolved."""


_BUNDLED_OPENAPI_DOCUMENT_PATHS: dict[str, Path] = {
    "ob-read-write-v4.0-account-info-openapi": (
        Path(__file__).resolve().parent / "standards" / "ob_read_write" / "v4_0" / "account-info-openapi.json"
    ),
    "ob-read-write-v4.0-payment-initiation-openapi": (
        Path(__file__).resolve().parent / "standards" / "ob_read_write" / "v4_0" / "payment-initiation-openapi.json"
    ),
    "ob-read-write-v4.0.1-account-info-openapi": (
        Path(__file__).resolve().parent / "standards" / "ob_read_write" / "v4_0_1" / "account-info-openapi.json"
    ),
}
"""Allowlisted bundled OpenAPI documents addressable by response schema assertions."""

_REQUIRED_PROPERTY_MESSAGE_PATTERN = re.compile(r"'([^']+)' is a required property")
"""Pattern used to attach missing-property names to formatted validation paths."""


def validate_json_instance_against_response_schema(
    *,
    source: str,
    document: str,
    schema_ref: str | None,
    inline_schema: Mapping[str, JsonValue] | None,
    instance: JsonValue,
) -> str | None:
    """Validate a JSON instance against a configured response schema.

    Args:
        source: Schema source selector from the manifest assertion.
        document: Allowlisted bundled document identifier.
        schema_ref: JSON Pointer to a schema inside the bundled document.
        inline_schema: Optional inline schema object from the manifest.
        instance: JSON value selected from the HTTP response body.

    Returns:
        ``None`` when the instance is valid, otherwise a deterministic failure
        message suitable for an assertion result.
    """
    try:
        schema = _prepared_schema(
            source=source,
            document=document,
            schema_ref=schema_ref,
            inline_schema=inline_schema,
        )
    except SchemaValidationConfigurationError as error:
        return str(error)

    validator = Draft4Validator(schema)
    errors = sorted(validator.iter_errors(instance), key=_validation_error_sort_key)
    if not errors:
        return None
    return _format_validation_error(errors[0])


def _prepared_schema(
    *,
    source: str,
    document: str,
    schema_ref: str | None,
    inline_schema: Mapping[str, JsonValue] | None,
) -> JsonObject:
    """Load and prepare a response schema for validation.

    Args:
        source: Schema source selector from the manifest assertion.
        document: Allowlisted bundled document identifier.
        schema_ref: JSON Pointer to a schema inside the bundled document.
        inline_schema: Optional inline schema object from the manifest.

    Returns:
        A dereferenced JSON Schema object ready for validation.

    Raises:
        SchemaValidationConfigurationError: If the source, document, or schema
            reference is unsupported or resolves to a non-object schema.
    """
    if source != "bundled_openapi":
        raise SchemaValidationConfigurationError(f"Schema source {source!r} is not supported")

    if schema_ref is not None:
        return _prepared_schema_ref(source=source, document=document, schema_ref=schema_ref)
    if inline_schema is None:
        raise SchemaValidationConfigurationError("Response schema assertion is missing schema configuration")

    document_root = _load_bundled_document(document)
    prepared_schema = _prepare_openapi_schema(document_root, _clone_mapping(inline_schema), seen_refs=())
    if not isinstance(prepared_schema, dict):
        raise SchemaValidationConfigurationError("Resolved response schema must be a JSON object")
    return prepared_schema


@cache
def _prepared_schema_ref(*, source: str, document: str, schema_ref: str) -> JsonObject:
    """Load and cache a dereferenced bundled schema reference.

    Args:
        source: Schema source selector from the manifest assertion.
        document: Allowlisted bundled document identifier.
        schema_ref: JSON Pointer to a schema inside the bundled document.

    Returns:
        A dereferenced JSON Schema object ready for validation.

    Raises:
        SchemaValidationConfigurationError: If the source, document, or schema
            reference is unsupported or resolves to a non-object schema.
    """
    if source != "bundled_openapi":
        raise SchemaValidationConfigurationError(f"Schema source {source!r} is not supported")

    document_root = _load_bundled_document(document)
    resolved_schema = _resolve_json_pointer(document_root, schema_ref)
    prepared_schema = _prepare_openapi_schema(document_root, resolved_schema, seen_refs=())
    if not isinstance(prepared_schema, dict):
        raise SchemaValidationConfigurationError("Resolved response schema must be a JSON object")
    return prepared_schema


@cache
def _load_bundled_document(document: str) -> JsonObject:
    """Load an allowlisted bundled schema document from disk.

    Args:
        document: Allowlisted bundled document identifier.

    Returns:
        Parsed JSON document contents.

    Raises:
        SchemaValidationConfigurationError: If the document id is unknown,
            missing on disk, or does not parse to a JSON object.
    """
    document_path = _BUNDLED_OPENAPI_DOCUMENT_PATHS.get(document)
    if document_path is None:
        raise SchemaValidationConfigurationError(f"Schema document {document!r} is not available")
    if not document_path.is_file():
        raise SchemaValidationConfigurationError(f"Bundled schema document {document!r} is missing from disk")

    loaded = json.loads(document_path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise SchemaValidationConfigurationError(f"Bundled schema document {document!r} must be a JSON object")
    return loaded


def _prepare_openapi_schema(document_root: JsonObject, value: JsonValue, *, seen_refs: tuple[str, ...]) -> JsonValue:
    """Resolve local refs and normalize OpenAPI schema fragments.

    Args:
        document_root: Full bundled document used as the local ref base.
        value: Schema fragment to normalize.
        seen_refs: Stack of refs currently being resolved to detect cycles.

    Returns:
        Normalized JSON value suitable for plain JSON Schema validation.

    Raises:
        SchemaValidationConfigurationError: If a ref is unsupported, missing,
            or forms a cycle.
    """
    if isinstance(value, Mapping):
        if "$ref" in value:
            raw_ref = value["$ref"]
            if not isinstance(raw_ref, str) or not raw_ref:
                raise SchemaValidationConfigurationError("Schema reference must be a non-empty string")
            if raw_ref in seen_refs:
                joined_refs = " -> ".join((*seen_refs, raw_ref))
                raise SchemaValidationConfigurationError(f"Circular schema reference is not supported: {joined_refs}")
            resolved_value = _resolve_json_pointer(document_root, raw_ref)
            resolved_schema = _prepare_openapi_schema(document_root, resolved_value, seen_refs=(*seen_refs, raw_ref))
            sibling_items = {key: member for key, member in value.items() if key != "$ref"}
            if not sibling_items:
                return resolved_schema
            if not isinstance(resolved_schema, dict):
                raise SchemaValidationConfigurationError(
                    "Schema reference with sibling keywords must resolve to an object"
                )
            merged_schema = copy.deepcopy(resolved_schema)
            for key, member in sibling_items.items():
                merged_schema[key] = _prepare_openapi_schema(document_root, member, seen_refs=seen_refs)
            return _normalize_openapi_schema_keywords(merged_schema)

        normalized_schema = {
            key: _prepare_openapi_schema(document_root, member, seen_refs=seen_refs) for key, member in value.items()
        }
        return _normalize_openapi_schema_keywords(normalized_schema)

    if isinstance(value, list):
        return [_prepare_openapi_schema(document_root, item, seen_refs=seen_refs) for item in value]
    return value


def _normalize_openapi_schema_keywords(schema: JsonObject) -> JsonObject:
    """Translate OpenAPI-specific keywords into JSON Schema equivalents.

    Args:
        schema: Schema object possibly containing OpenAPI-only keywords.

    Returns:
        Schema object adjusted for plain JSON Schema validation.
    """
    namespaced_enum_value = schema.pop("x-namespaced-enum", None)
    if (
        "enum" not in schema
        and isinstance(namespaced_enum_value, list)
        and all(isinstance(item, str) for item in namespaced_enum_value)
    ):
        schema["enum"] = namespaced_enum_value

    nullable_value = schema.pop("nullable", None)
    if nullable_value is not True:
        return schema

    schema_type = schema.get("type")
    if isinstance(schema_type, str):
        schema["type"] = [schema_type, "null"]
    elif (
        isinstance(schema_type, list)
        and "null" not in schema_type
        and all(isinstance(item, str) for item in schema_type)
    ):
        schema["type"] = [*schema_type, "null"]
    return schema


def _resolve_json_pointer(document: JsonValue, pointer: str) -> JsonValue:
    """Resolve a local JSON Pointer within a bundled schema document.

    Args:
        document: Root document or schema fragment to traverse.
        pointer: Local JSON Pointer beginning with ``#/``.

    Returns:
        Deep-copied JSON value referenced by the pointer.

    Raises:
        SchemaValidationConfigurationError: If the pointer is unsupported or
            does not exist within the document.
    """
    if not pointer.startswith("#/"):
        raise SchemaValidationConfigurationError(f"Schema reference {pointer!r} must be a local '#/' pointer")

    current_value = document
    for raw_segment in pointer[2:].split("/"):
        segment = raw_segment.replace("~1", "/").replace("~0", "~")
        if isinstance(current_value, Mapping):
            if segment not in current_value:
                raise SchemaValidationConfigurationError(f"Schema reference {pointer!r} was not found")
            current_value = current_value[segment]
            continue
        if isinstance(current_value, list):
            if not segment.isdigit():
                raise SchemaValidationConfigurationError(
                    f"Schema reference {pointer!r} segment {segment!r} is not a list index"
                )
            index = int(segment)
            if index >= len(current_value):
                raise SchemaValidationConfigurationError(f"Schema reference {pointer!r} was not found")
            current_value = current_value[index]
            continue
        raise SchemaValidationConfigurationError(f"Schema reference {pointer!r} could not be resolved")
    return copy.deepcopy(current_value)


def _clone_mapping(mapping: Mapping[str, JsonValue]) -> JsonObject:
    """Clone a mapping-backed JSON object into a mutable ``dict``.

    Args:
        mapping: Mapping containing JSON-compatible values.

    Returns:
        Deep-cloned JSON object.
    """
    return {key: _clone_json_value(value) for key, value in mapping.items()}


def _clone_json_value(value: JsonValue) -> JsonValue:
    """Deep-clone a JSON-compatible value.

    Args:
        value: JSON-compatible value to clone.

    Returns:
        Deep-cloned JSON value.
    """
    if isinstance(value, dict):
        return {key: _clone_json_value(member) for key, member in value.items()}
    if isinstance(value, list):
        return [_clone_json_value(member) for member in value]
    return value


def _validation_error_sort_key(error: ValidationError) -> tuple[str, tuple[str, ...], str]:
    """Build a deterministic sort key for schema validation errors.

    Args:
        error: Validation error emitted by ``jsonschema``.

    Returns:
        Tuple ordering errors by instance path, schema path, then message.
    """
    instance_path = _format_error_path(error.absolute_path)
    schema_path = tuple(str(part) for part in error.absolute_schema_path)
    return (instance_path, schema_path, error.message)


def _format_validation_error(error: ValidationError) -> str:
    """Render a deterministic, path-aware validation error message.

    Args:
        error: Validation error emitted by ``jsonschema``.

    Returns:
        Human-readable validation failure message.
    """
    path = _format_error_path(error.absolute_path)
    error_message = str(error.message)
    if error.validator == "required":
        missing_property = _missing_required_property_name(error_message)
        if missing_property is not None:
            required_path = missing_property if path == "$" else f"{path}.{missing_property}"
            return f"at {required_path}: {error_message}"
    if path == "$":
        return error_message
    return f"at {path}: {error_message}"


def _missing_required_property_name(message: str) -> str | None:
    """Extract a missing-property name from a validation message.

    Args:
        message: Raw ``jsonschema`` error message.

    Returns:
        Missing property name when the message matches the expected pattern,
        otherwise ``None``.
    """
    match = _REQUIRED_PROPERTY_MESSAGE_PATTERN.fullmatch(message)
    if match is None:
        return None
    return str(match.group(1))


def _format_error_path(path: Sequence[object]) -> str:
    """Format a ``jsonschema`` error path using dotted and indexed segments.

    Args:
        path: Sequence of path segments from a validation error.

    Returns:
        Formatted path rooted at ``$``.
    """
    if not path:
        return "$"

    formatted_parts: list[str] = []
    for segment in path:
        if isinstance(segment, int):
            if not formatted_parts:
                formatted_parts.append(f"[{segment}]")
            else:
                formatted_parts[-1] = f"{formatted_parts[-1]}[{segment}]"
            continue
        text_segment = str(segment)
        if not formatted_parts:
            formatted_parts.append(text_segment)
        else:
            formatted_parts.append(f".{text_segment}")
    return "".join(formatted_parts)
