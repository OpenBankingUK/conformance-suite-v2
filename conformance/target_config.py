"""Target-oriented conformance configuration: the new testTarget model.

This module defines the endpoint-first target model that replaces the legacy
``testSuite`` config shape.  A :class:`TestTargetConfig` captures the
participant's intent at the Standard → Specification → Security Profile →
Version level, plus any selected resource groups.

The target coordinates drive catalogue lookup, guided-UI population, and Run
Plan v2 compilation.  They do not directly select a named suite manifest —
the plugin registry resolves which catalogue to load from these coordinates.

Wire format: the JSON representation uses camelCase keys (``specificationVersion``,
``resourceGroups``) to match the REST API and plan-builder UI.
:func:`parse_test_target_config` converts from JSON; :func:`serialise_test_target_config`
converts back.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from conformance.json_types import JsonValue

# ---------------------------------------------------------------------------
# Public type aliases
# ---------------------------------------------------------------------------

Standard = Literal["obl"]
"""Top-level conformance standard identifier.

``"obl"`` is the Open Banking Limited standard.  Additional standards may be
added as the plugin framework expands.
"""

Specification = Literal["read-write", "dynamic-client-registration"]
"""Specification identifier under a :data:`Standard`.

``"read-write"`` covers the Open Banking Read/Write API suite.
``"dynamic-client-registration"`` covers the Open Banking DCR specification.
"""

SecurityProfile = Literal["fapi1-advanced"]
"""Security profile scoping the conformance run.

``"fapi1-advanced"`` is the FAPI 1.0 Advanced profile mandated by Open Banking
UK.  This value is fixed and visible in the guided UI but not user-selectable.
"""

# ---------------------------------------------------------------------------
# Validation constants
# ---------------------------------------------------------------------------

_SUPPORTED_STANDARDS: frozenset[str] = frozenset({"obl"})
"""Recognised :data:`Standard` values accepted by the parser."""

_SUPPORTED_SPECIFICATIONS: frozenset[str] = frozenset({"read-write", "dynamic-client-registration"})
"""Recognised :data:`Specification` values accepted by the parser."""

_SUPPORTED_SECURITY_PROFILES: frozenset[str] = frozenset({"fapi1-advanced"})
"""Recognised :data:`SecurityProfile` values accepted by the parser."""

# ---------------------------------------------------------------------------
# Exception
# ---------------------------------------------------------------------------


class TestTargetConfigError(ValueError):
    """Raised when a raw JSON value cannot be parsed into a :class:`TestTargetConfig`.

    Wraps ``ValueError`` so callers can catch either the specific error or the
    generic base class depending on how much granularity they need.
    """


# ---------------------------------------------------------------------------
# Domain dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TestTargetConfig:
    """Validated target coordinates for an endpoint-first conformance run.

    Replaces the legacy ``testSuite`` config shape.  The target captures
    *what* the participant intends to test — standard, specification, security
    profile, specification version, and resource groups — from which the plugin
    registry derives which catalogue to load and which tests to compile.

    ``resource_groups`` is empty for DCR, which exposes operations rather than
    resource groups.  For Read/Write it contains one or more of ``"ais"``,
    ``"pis"``, ``"cbpii"``, or ``"vrp"``.

    Attributes:
        standard: Top-level conformance standard (e.g. ``"obl"``).
        specification: Specification under the standard (e.g. ``"read-write"``).
        security_profile: Fixed FAPI 1 Advanced security profile
            (always ``"fapi1-advanced"``).
        specification_version: Standards version string
            (e.g. ``"v4.0.1"`` for Read/Write, ``"3.3"`` for DCR).
        resource_groups: Ordered tuple of selected resource-group identifiers.
            Empty for specifications that do not use resource groups (DCR).
    """

    standard: Standard
    specification: Specification
    security_profile: SecurityProfile
    specification_version: str
    resource_groups: tuple[str, ...] = field(default_factory=tuple)


# ---------------------------------------------------------------------------
# Public functions
# ---------------------------------------------------------------------------


def parse_test_target_config(raw: JsonValue) -> TestTargetConfig:
    """Parse and validate a raw JSON value into a :class:`TestTargetConfig`.

    Validates all required fields and their allowed values.  Raises
    :class:`TestTargetConfigError` with a human-readable message for every
    validation failure.

    Parsing rules:

    - ``standard`` must be one of the supported :data:`Standard` values.
    - ``specification`` must be one of the supported :data:`Specification` values.
    - ``securityProfile`` must be one of the supported :data:`SecurityProfile`
      values.  When absent it defaults to ``"fapi1-advanced"`` (the only
      supported value).
    - ``specificationVersion`` must be a non-empty string.
    - ``resourceGroups`` is optional; when present it must be a JSON array of
      non-empty strings.

    Args:
        raw: The parsed JSON value to validate.  Typically the result of
            ``json.loads()``.

    Returns:
        A fully validated :class:`TestTargetConfig` instance.

    Raises:
        TestTargetConfigError: If ``raw`` is not a JSON object, if any
            required field is missing or has the wrong type, or if a field
            value is not in the allowed set.
    """
    if not isinstance(raw, dict):
        raise TestTargetConfigError("testTarget must be a JSON object")

    standard = _require_enum(raw, "standard", _SUPPORTED_STANDARDS)
    specification = _require_enum(raw, "specification", _SUPPORTED_SPECIFICATIONS)
    security_profile = _parse_security_profile(raw)
    specification_version = _require_string(raw, "specificationVersion")
    resource_groups = _parse_resource_groups(raw)

    return TestTargetConfig(
        standard=standard,  # type: ignore[arg-type]
        specification=specification,  # type: ignore[arg-type]
        security_profile=security_profile,  # type: ignore[arg-type]
        specification_version=specification_version,
        resource_groups=resource_groups,
    )


def serialise_test_target_config(target: TestTargetConfig) -> dict[str, JsonValue]:
    """Serialise a :class:`TestTargetConfig` to a camelCase JSON-compatible dictionary.

    The output uses camelCase keys to match the wire format expected by the
    REST API and the plan-builder UI.  The result can be passed directly to
    ``json.dumps()``.

    Args:
        target: The :class:`TestTargetConfig` to serialise.

    Returns:
        A ``dict[str, JsonValue]`` ready for JSON serialisation.
    """
    result: dict[str, JsonValue] = {
        "standard": target.standard,
        "specification": target.specification,
        "securityProfile": target.security_profile,
        "specificationVersion": target.specification_version,
    }
    if target.resource_groups:
        result["resourceGroups"] = list(target.resource_groups)
    return result


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _require_string(obj: dict[str, JsonValue], key: str) -> str:
    """Extract a required non-empty string value from a JSON object.

    Args:
        obj: The JSON object to extract from.
        key: The key whose value must be a non-empty string.

    Returns:
        The string value for ``key``.

    Raises:
        TestTargetConfigError: If the key is absent or its value is not a
            non-empty string.
    """
    value = obj.get(key)
    if not isinstance(value, str):
        raise TestTargetConfigError(
            f"Missing or invalid field {key!r}: expected a non-empty string, got {type(value).__name__!r}"
        )
    if not value:
        raise TestTargetConfigError(f"Field {key!r} must not be an empty string")
    return value


def _require_enum(obj: dict[str, JsonValue], key: str, allowed: frozenset[str]) -> str:
    """Extract a required string value and validate it against an allowed set.

    Args:
        obj: The JSON object to extract from.
        key: The key whose value must appear in ``allowed``.
        allowed: Frozenset of accepted string values.

    Returns:
        The validated string value for ``key``.

    Raises:
        TestTargetConfigError: If the key is absent, not a string, or not in
            the allowed set.
    """
    value = _require_string(obj, key)
    if value not in allowed:
        sorted_allowed = sorted(allowed)
        raise TestTargetConfigError(f"Unsupported {key!r} value {value!r}; expected one of {sorted_allowed}")
    return value


def _parse_security_profile(obj: dict[str, JsonValue]) -> str:
    """Parse the optional ``securityProfile`` field, defaulting to ``"fapi1-advanced"``.

    When ``securityProfile`` is absent the default value ``"fapi1-advanced"``
    is used.  When present it must be one of the supported values.

    Args:
        obj: The top-level testTarget JSON object.

    Returns:
        The security-profile string.

    Raises:
        TestTargetConfigError: If ``securityProfile`` is present but not a
            valid supported value.
    """
    raw = obj.get("securityProfile")
    if raw is None:
        return "fapi1-advanced"
    if not isinstance(raw, str):
        raise TestTargetConfigError(f"Field 'securityProfile' must be a string, got {type(raw).__name__!r}")
    if raw not in _SUPPORTED_SECURITY_PROFILES:
        sorted_allowed = sorted(_SUPPORTED_SECURITY_PROFILES)
        raise TestTargetConfigError(f"Unsupported 'securityProfile' value {raw!r}; expected one of {sorted_allowed}")
    return raw


def _parse_resource_groups(obj: dict[str, JsonValue]) -> tuple[str, ...]:
    """Parse the optional ``resourceGroups`` array from a testTarget object.

    Args:
        obj: The top-level testTarget JSON object.

    Returns:
        A tuple of non-empty resource-group identifier strings, or an empty
        tuple when ``resourceGroups`` is absent.

    Raises:
        TestTargetConfigError: If ``resourceGroups`` is present but not a
            JSON array, or if any element is not a non-empty string.
    """
    raw = obj.get("resourceGroups")
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise TestTargetConfigError("Field 'resourceGroups' must be a JSON array when present")
    groups: list[str] = []
    for idx, item in enumerate(raw):
        if not isinstance(item, str):
            raise TestTargetConfigError(f"resourceGroups[{idx}] must be a string, got {type(item).__name__!r}")
        if not item:
            raise TestTargetConfigError(f"resourceGroups[{idx}] must not be an empty string")
        groups.append(item)
    return tuple(groups)
