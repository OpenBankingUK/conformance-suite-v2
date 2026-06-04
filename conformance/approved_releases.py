"""Approved-release policy parsing shared by reports and OBL validation."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import cast

APPROVED_RELEASE_POLICY_SCHEMA_VERSION = "v1"
"""Approved-release policy schema version accepted by the tool."""


class ApprovedReleasePolicyError(ValueError):
    """Raised when approved-release policy inputs are malformed."""


@dataclass(frozen=True)
class ApprovedReleasePolicy:
    """Approved FCS release policy supplied by OBL.

    Attributes:
        schema_version: Policy schema version. Currently only ``v1`` is
            accepted so policy evolution can be explicit.
        approved_tool_versions: Tool versions approved for certification
            assessment.
    """

    schema_version: str
    approved_tool_versions: tuple[str, ...]

    def is_tool_version_approved(self, tool_version: str) -> bool:
        """Return whether ``tool_version`` is approved by this policy.

        Args:
            tool_version: Submitted report tool version to check.

        Returns:
            True when the exact tool version appears in the approved release
            list; otherwise False.
        """
        return tool_version in self.approved_tool_versions


def load_approved_release_policy(policy_path: Path) -> ApprovedReleasePolicy:
    """Load and parse an approved-release policy JSON file.

    Args:
        policy_path: Path to the approved-release policy JSON file.

    Returns:
        Parsed approved-release policy.

    Raises:
        ApprovedReleasePolicyError: If the file cannot be read, decoded, or
            parsed as an approved-release policy.
    """
    return parse_approved_release_policy(_load_json_file(policy_path))


def parse_approved_release_policy(raw_policy: object) -> ApprovedReleasePolicy:
    """Parse a decoded JSON value as an approved-release policy.

    Args:
        raw_policy: Decoded JSON value expected to be the policy root object.

    Returns:
        Parsed approved-release policy.

    Raises:
        ApprovedReleasePolicyError: If the policy root or required fields are
            missing or malformed.
    """
    policy = _as_object(raw_policy, location="approved-release policy")
    schema_version = _required_non_empty_string(policy, "schemaVersion", location="approved-release policy")
    if schema_version != APPROVED_RELEASE_POLICY_SCHEMA_VERSION:
        raise ApprovedReleasePolicyError(
            "approved-release policy.schemaVersion must be "
            f"{APPROVED_RELEASE_POLICY_SCHEMA_VERSION!r} (got {schema_version!r})"
        )
    return ApprovedReleasePolicy(
        schema_version=schema_version,
        approved_tool_versions=_required_string_array(
            policy,
            "approvedToolVersions",
            location="approved-release policy",
        ),
    )


def _load_json_file(path: Path) -> object:
    """Load a JSON policy file from disk.

    Args:
        path: Path to the JSON policy file.

    Returns:
        Decoded JSON value.

    Raises:
        ApprovedReleasePolicyError: If the file cannot be read or decoded.
    """
    resolved_path = path.resolve()
    try:
        decoded: object = json.loads(resolved_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ApprovedReleasePolicyError(f"Invalid JSON approved-release policy: {error.msg}") from error
    except OSError as error:
        raise ApprovedReleasePolicyError(f"Unable to read approved-release policy file: {error}") from error
    return decoded


def _as_object(value: object, *, location: str) -> dict[str, object]:
    """Return ``value`` as a JSON object.

    Args:
        value: Decoded JSON value to validate.
        location: Dot-path location string used in error messages.

    Returns:
        JSON object with string keys.

    Raises:
        ApprovedReleasePolicyError: If the value is not a JSON object or any
            key is not a string.
    """
    if not isinstance(value, dict):
        raise ApprovedReleasePolicyError(f"{location} must be a JSON object")
    if any(not isinstance(key, str) for key in value):
        raise ApprovedReleasePolicyError(f"{location} keys must be strings")
    return cast(dict[str, object], value)


def _required_array(parent: Mapping[str, object], key: str, *, location: str) -> list[object]:
    """Extract a required JSON array field.

    Args:
        parent: Parent JSON object.
        key: Field name to extract.
        location: Dot-path location string used in error messages.

    Returns:
        Required child JSON array.

    Raises:
        ApprovedReleasePolicyError: If the field is missing or not a JSON
            array.
    """
    if key not in parent:
        raise ApprovedReleasePolicyError(f"{location}.{key} is required")
    value = parent[key]
    if not isinstance(value, list):
        raise ApprovedReleasePolicyError(f"{location}.{key} must be a JSON array")
    return cast(list[object], value)


def _required_non_empty_string(parent: Mapping[str, object], key: str, *, location: str) -> str:
    """Extract a required non-empty string field.

    Args:
        parent: Parent JSON object.
        key: Field name to extract.
        location: Dot-path location string used in error messages.

    Returns:
        Stripped non-empty string value.

    Raises:
        ApprovedReleasePolicyError: If the field is missing, not a string, or
            empty after stripping.
    """
    if key not in parent:
        raise ApprovedReleasePolicyError(f"{location}.{key} is required")
    value = parent[key]
    if not isinstance(value, str):
        raise ApprovedReleasePolicyError(f"{location}.{key} must be a string")
    stripped = value.strip()
    if not stripped:
        raise ApprovedReleasePolicyError(f"{location}.{key} must not be empty")
    return stripped


def _required_string_array(parent: Mapping[str, object], key: str, *, location: str) -> tuple[str, ...]:
    """Extract a required array of non-empty strings.

    Args:
        parent: Parent JSON object.
        key: Field name to extract.
        location: Dot-path location string used in error messages.

    Returns:
        Tuple of stripped non-empty string values.

    Raises:
        ApprovedReleasePolicyError: If the field is missing, not an array, or
            contains a non-string or empty item.
    """
    values = _required_array(parent, key, location=location)
    parsed_values: list[str] = []
    for index, value in enumerate(values):
        item_location = f"{location}.{key}[{index}]"
        if not isinstance(value, str):
            raise ApprovedReleasePolicyError(f"{item_location} must be a string")
        stripped = value.strip()
        if not stripped:
            raise ApprovedReleasePolicyError(f"{item_location} must not be empty")
        parsed_values.append(stripped)
    return tuple(parsed_values)
