"""Versioned JSON catalogue domain types and loader.

A catalogue is a source-controlled JSON document that records the reviewed,
versioned set of endpoints, operations, field metadata, and test definitions
for one plugin.  Catalogues drive:

- endpoint/operation selection in the guided UI;
- test applicability rules that map selected coverage to executable tests;
- mandatory/conditional/optional endpoint status sourced from the specification;
- drift detection via a content hash stored in the :class:`RunPlanV2`.

This module defines the in-memory catalogue types and the :func:`parse_catalogue`
function that validates a raw JSON document.  Actual catalogue JSON files live
under plugin-owned package areas (e.g. ``conformance/suites/catalogues/``).

Content hashing: :func:`compute_catalogue_hash` returns a canonical SHA-256
digest of the raw catalogue bytes in the same ``"sha256:<hex>"`` format used
by :func:`conformance.run_plan.compute_manifest_hash`.  The hash is stored in
:class:`CatalogueIdentity` and in Run Plan v2 documents so the engine can
detect catalogue drift between when a plan was authored and when it is executed.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Literal, cast

from conformance.json_types import JsonObject, JsonValue

# ---------------------------------------------------------------------------
# Public type aliases
# ---------------------------------------------------------------------------

EndpointRequirement = Literal["mandatory", "conditional", "optional"]
"""Endpoint requirement level sourced from the specification.

``"mandatory"``
    The endpoint must be implemented.  Omitting it from a run makes the
    resource group incomplete/non-ready but does not block execution.

``"conditional"``
    The endpoint is required when the ASPSP has declared support for a
    specific optional feature.

``"optional"``
    The endpoint may be omitted without affecting resource-group readiness.
"""

# ---------------------------------------------------------------------------
# Exception
# ---------------------------------------------------------------------------


class CatalogueParseError(ValueError):
    """Raised when a raw JSON value cannot be parsed into a :class:`Catalogue`.

    Wraps ``ValueError`` so callers can catch either the specific error or the
    generic base class depending on how much granularity they need.
    """


# ---------------------------------------------------------------------------
# Domain dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CatalogueIdentity:
    """Identity and drift-detection metadata for a versioned catalogue.

    Stored inside a catalogue document and inside :class:`RunPlanV2` so the
    engine can detect when the catalogue has changed since the plan was
    authored.

    Attributes:
        plugin_id: Owning plugin identifier (e.g. ``"read-write"``).
        specification: Specification the catalogue covers
            (e.g. ``"read-write"``).
        specification_version: Standards version string
            (e.g. ``"v4.0.1"``).
        content_hash: SHA-256 hex digest of the raw catalogue JSON bytes in
            the canonical ``"sha256:<hex>"`` format returned by
            :func:`compute_catalogue_hash`.
        standard: Optional standard coordinate for schema v2 catalogues
            (e.g. ``"obl"``).
        security_profile: Optional security-profile coordinate for schema v2
            catalogues (e.g. ``"fapi1-advanced"``).
        version_aliases: Accepted migration aliases for
            :attr:`specification_version`; aliases are not serialised as the
            canonical version.
    """

    plugin_id: str
    specification: str
    specification_version: str
    content_hash: str
    standard: str | None = None
    security_profile: str | None = None
    version_aliases: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class EndpointCatalogueEntry:
    """Catalogue record for one endpoint operation.

    Each entry captures the endpoint's identity, HTTP method, resource-group
    membership, requirement level, and a human-readable display label for use
    in the guided UI.

    Attributes:
        endpoint_id: Stable unique identifier for this endpoint operation,
            used as the reference key in Run Plan v2 selections
            (e.g. ``"get-accounts"``).
        operation: HTTP operation name (e.g. ``"GET"``, ``"POST"``).
        path: Open Banking API path relative to the resource base URL
            (e.g. ``"/accounts"``).
        method: HTTP method string (e.g. ``"GET"``).
        resource_group: Resource-group identifier, or ``None`` for
            specifications that do not use resource groups (DCR).
        requirement: Requirement level sourced from the specification.
        display_label: Short human-readable label for the guided UI
            (e.g. ``"Get Accounts"``).
        schema_ref: Optional JSON pointer to the source OpenAPI operation.
        test_applicability_rules: Catalogue-native applicability metadata for
            tests bound to this endpoint.
        source_citations: Source documents that justify the endpoint metadata.
    """

    endpoint_id: str
    operation: str
    path: str
    method: str
    resource_group: str | None
    requirement: EndpointRequirement
    display_label: str
    schema_ref: str | None = None
    test_applicability_rules: tuple[Mapping[str, JsonValue], ...] = field(default_factory=tuple)
    source_citations: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class CatalogueResourceGroup:
    """Resource-group section in a schema v2 catalogue.

    Attributes:
        resource_group: Stable resource-group identifier (e.g. ``"ais"``).
        display_label: Human-readable label for the guided UI.
        requirement: Requirement level for the group as a whole.
        endpoint_ids: Ordered endpoint identifiers included in this section.
        source_citations: Source documents that justify the section metadata.
    """

    resource_group: str
    display_label: str
    requirement: EndpointRequirement
    endpoint_ids: tuple[str, ...] = field(default_factory=tuple)
    source_citations: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class CatalogueFieldVisibilityCondition:
    """Predicate controlling when one field is visible in guided UI prompts.

    Attributes:
        field_id: Referenced field identifier whose selected value controls
            visibility.
        equals: Allowed string values that make the field visible.
    """

    field_id: str
    equals: tuple[str, ...]


@dataclass(frozen=True)
class CatalogueFieldSchema:
    """Participant-supplied or generated field metadata from a catalogue.

    Attributes:
        field_id: Stable field identifier.
        display_label: Human-readable field label.
        scope: Scope where the field applies, such as ``"shared"`` or an
            endpoint identifier.
        value_type: Expected value category, such as ``"string"`` or
            ``"path"``.
        required: Whether the field is required for its scope.
        sensitive: Whether values for this field must be masked in evidence.
        helper_text: Optional UI guidance shown below the field prompt.
        visible_when: Optional visibility predicates that must all match before
            the field is shown in the plan-builder UI.
    """

    field_id: str
    display_label: str
    scope: str
    value_type: str
    required: bool
    sensitive: bool
    helper_text: str | None = None
    visible_when: tuple[CatalogueFieldVisibilityCondition, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class CatalogueApplicabilityPredicate:
    """Predicate controlling when a catalogue test applies.

    Attributes:
        predicate_type: Stable predicate type understood by the planner.
        parameters: JSON-compatible predicate parameters.
    """

    predicate_type: str
    parameters: Mapping[str, JsonValue] = field(default_factory=lambda: MappingProxyType({}))


@dataclass(frozen=True)
class CatalogueSourceCoverageRef:
    """Reference from a catalogue test back to migration/parity source coverage.

    Attributes:
        source_kind: Source family, such as ``"current-suite"`` or
            ``"previous-fcs"``.
        source_id: Source test, step, or script identifier.
        mapping_mode: How the source was mapped into the catalogue.
        source_files: Source files that contained the referenced identifier.
    """

    source_kind: str
    source_id: str
    mapping_mode: str
    source_files: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class CatalogueRunnerPrimitive:
    """Runner primitive referenced by executable catalogue tests.

    Attributes:
        primitive_id: Stable primitive identifier.
        primitive_type: Primitive category, such as ``"http-step"``.
        description: Human-readable summary for reviewers and diagnostics.
    """

    primitive_id: str
    primitive_type: str
    description: str


@dataclass(frozen=True)
class CatalogueExecutableTest:
    """Executable test definition in a schema v2 catalogue.

    Attributes:
        test_id: Stable catalogue test identifier used in plans/results.
        display_label: Human-readable label for the test.
        endpoint_id: Optional endpoint this test exercises.
        resource_group: Optional resource group this test contributes to.
        runner_primitive_id: Runner primitive used to execute the test.
        applicability: Ordered applicability predicates for this test.
        source_coverage: Source coverage references preserved during migration.
    """

    test_id: str
    display_label: str
    endpoint_id: str | None
    resource_group: str | None
    runner_primitive_id: str
    applicability: tuple[CatalogueApplicabilityPredicate, ...] = field(default_factory=tuple)
    source_coverage: tuple[CatalogueSourceCoverageRef, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class CatalogueReadinessPolicy:
    """Readiness and certification policy metadata owned by a catalogue.

    Attributes:
        policy_id: Stable policy identifier.
        certification_status: Certification eligibility for the catalogue.
        omitted_mandatory_outcome: Outcome when selected resource groups omit
            mandatory endpoint coverage.
        failed_selected_outcome: Outcome when selected tests fail.
    """

    policy_id: str
    certification_status: str
    omitted_mandatory_outcome: str
    failed_selected_outcome: str


@dataclass(frozen=True)
class CatalogueMaskingMetadata:
    """Plugin-specific masking metadata from a catalogue.

    Attributes:
        masked_fields: Field identifiers whose runtime values must be masked.
        evidence_paths: JSON-pointer-like paths to mask in plugin evidence.
    """

    masked_fields: tuple[str, ...] = field(default_factory=tuple)
    evidence_paths: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class Catalogue:
    """In-memory representation of a validated versioned JSON catalogue.

    Attributes:
        identity: Identity and drift-detection metadata.
        endpoints: Ordered tuple of endpoint catalogue entries.
        schema_version: Catalogue schema version.  Existing endpoint-only
            catalogues are treated as version ``1``; authoritative catalogues
            use version ``2``.
        resource_groups: Ordered schema v2 resource-group sections.
        field_schemas: Field/value metadata for shared and endpoint-specific
            participant input.
        runner_primitives: Runner primitives referenced by executable tests.
        executable_tests: Ordered executable catalogue test definitions.
        readiness_policy: Optional catalogue-owned readiness/certification
            policy metadata.
        masking: Optional plugin-specific masking metadata.
        source_coverage: Catalogue-level source coverage metadata.
    """

    identity: CatalogueIdentity
    endpoints: tuple[EndpointCatalogueEntry, ...]
    schema_version: int = 1
    resource_groups: tuple[CatalogueResourceGroup, ...] = field(default_factory=tuple)
    field_schemas: tuple[CatalogueFieldSchema, ...] = field(default_factory=tuple)
    runner_primitives: tuple[CatalogueRunnerPrimitive, ...] = field(default_factory=tuple)
    executable_tests: tuple[CatalogueExecutableTest, ...] = field(default_factory=tuple)
    readiness_policy: CatalogueReadinessPolicy | None = None
    masking: CatalogueMaskingMetadata | None = None
    source_coverage: Mapping[str, JsonValue] = field(default_factory=lambda: MappingProxyType({}))


# ---------------------------------------------------------------------------
# Public functions
# ---------------------------------------------------------------------------


def compute_catalogue_hash(catalogue_bytes: bytes) -> str:
    """Compute the canonical SHA-256 hash of raw catalogue JSON bytes.

    Returns a string in the format ``"sha256:<hex>"`` where ``<hex>`` is the
    lowercase hexadecimal representation of the digest.  This format is stored
    in :attr:`CatalogueIdentity.content_hash` and compared against the hash of
    live catalogue bytes when the engine checks for drift.

    Args:
        catalogue_bytes: The raw bytes of the catalogue JSON document to hash.

    Returns:
        A string of the form ``"sha256:<64-hex-chars>"``.
    """
    digest = hashlib.sha256(catalogue_bytes).hexdigest()
    return f"sha256:{digest}"


def parse_catalogue(raw: JsonValue) -> Catalogue:
    """Parse and validate a raw JSON value into a :class:`Catalogue`.

    Validates the full document structure including the ``identity`` block and
    all ``endpoints`` entries.  Raises :class:`CatalogueParseError` with a
    human-readable message for every validation failure so that catalogue
    authoring errors are easy to diagnose.

    Parsing rules:

    - The document must be a JSON object.
    - ``identity`` must be a JSON object with non-empty string fields
      ``pluginId``, ``specification``, ``specificationVersion``, and
      ``contentHash``.
    - ``endpoints`` must be a JSON array; each element must be a JSON object
      with fields ``endpointId``, ``operation``, ``path``, ``method``,
      ``requirement``, and ``displayLabel`` (all non-empty strings), plus an
      optional ``resourceGroup`` (non-empty string or ``null``).
    - ``requirement`` must be one of ``"mandatory"``, ``"conditional"``, or
      ``"optional"``.

    Args:
        raw: The parsed JSON value to validate.  Typically the result of
            ``json.loads()``.

    Returns:
        A fully validated :class:`Catalogue` instance.

    Raises:
        CatalogueParseError: If the document is structurally invalid or any
            field value fails validation.
    """
    if not isinstance(raw, dict):
        raise CatalogueParseError("Catalogue must be a JSON object")

    schema_version = _parse_schema_version(raw)
    identity = _parse_identity(raw, schema_version=schema_version)
    resource_groups = _parse_resource_group_sections(raw)
    endpoints = _parse_endpoints(raw, resource_groups=resource_groups)
    field_schemas = _parse_field_schemas(raw)
    runner_primitives = _parse_runner_primitives(raw)
    executable_tests = _parse_executable_tests(raw)
    readiness_policy = _parse_readiness_policy(raw)
    masking = _parse_masking(raw)
    source_coverage = _parse_source_coverage(raw)
    _validate_unique_endpoint_ids(endpoints)
    _validate_unique_test_ids(executable_tests)

    return Catalogue(
        identity=identity,
        endpoints=endpoints,
        schema_version=schema_version,
        resource_groups=resource_groups,
        field_schemas=field_schemas,
        runner_primitives=runner_primitives,
        executable_tests=executable_tests,
        readiness_policy=readiness_policy,
        masking=masking,
        source_coverage=source_coverage,
    )


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _require_string(obj: dict[str, JsonValue], key: str, *, context: str = "") -> str:
    """Extract a required non-empty string value from a JSON object.

    Args:
        obj: The JSON object to extract from.
        key: The key whose value must be a non-empty string.
        context: Optional dot-path context prefix for error messages.

    Returns:
        The string value for ``key``.

    Raises:
        CatalogueParseError: If the key is absent or its value is not a
            non-empty string.
    """
    prefix = f"{context}." if context else ""
    value = obj.get(key)
    if not isinstance(value, str):
        raise CatalogueParseError(
            f"Missing or invalid field {prefix!r}{key!r}: expected a non-empty string, got {type(value).__name__!r}"
        )
    if not value:
        raise CatalogueParseError(f"Field {prefix!r}{key!r} must not be an empty string")
    return value


def _optional_string(obj: dict[str, JsonValue], key: str, *, context: str = "") -> str | None:
    """Extract an optional non-empty string value from a JSON object.

    Args:
        obj: The JSON object to extract from.
        key: The key whose value, when present, must be a non-empty string.
        context: Optional dot-path context prefix for error messages.

    Returns:
        The string value, or ``None`` when the key is absent or null.

    Raises:
        CatalogueParseError: If the value is present but not a non-empty string.
    """
    value = obj.get(key)
    if value is None:
        return None
    return _require_string(obj, key, context=context)


def _require_bool(obj: dict[str, JsonValue], key: str, *, context: str = "") -> bool:
    """Extract a required boolean value from a JSON object.

    Args:
        obj: The JSON object to extract from.
        key: The key whose value must be a boolean.
        context: Optional dot-path context prefix for error messages.

    Returns:
        The boolean value for ``key``.

    Raises:
        CatalogueParseError: If the key is absent or its value is not boolean.
    """
    prefix = f"{context}." if context else ""
    value = obj.get(key)
    if not isinstance(value, bool):
        raise CatalogueParseError(f"Missing or invalid field {prefix!r}{key!r}: expected a boolean")
    return value


def _optional_bool(obj: dict[str, JsonValue], key: str, *, default: bool) -> bool:
    """Extract an optional boolean value from a JSON object.

    Args:
        obj: The JSON object to extract from.
        key: The key whose value, when present, must be a boolean.
        default: Value returned when ``key`` is absent or null.

    Returns:
        The parsed boolean value or ``default``.

    Raises:
        CatalogueParseError: If the value is present but not a boolean.
    """
    value = obj.get(key)
    if value is None:
        return default
    if not isinstance(value, bool):
        raise CatalogueParseError(f"Field {key!r} must be a boolean, got {type(value).__name__!r}")
    return value


def _parse_schema_version(doc: dict[str, JsonValue]) -> int:
    """Parse the optional catalogue schema version discriminator.

    Args:
        doc: The top-level catalogue JSON object.

    Returns:
        The parsed schema version.  Missing values default to ``1`` for
        backwards compatibility with existing endpoint-only catalogues.

    Raises:
        CatalogueParseError: If the version is not ``1`` or ``2``.
    """
    raw_version = doc.get("schemaVersion", 1)
    if isinstance(raw_version, bool):
        raise CatalogueParseError("Unsupported catalogue schemaVersion; expected 1 or 2")
    if isinstance(raw_version, str) and raw_version in {"1", "2"}:
        raw_version = int(raw_version)
    if raw_version not in {1, 2}:
        raise CatalogueParseError("Unsupported catalogue schemaVersion; expected 1 or 2")
    return cast(int, raw_version)


def _parse_identity(doc: dict[str, JsonValue], *, schema_version: int) -> CatalogueIdentity:
    """Parse the ``identity`` block from a raw catalogue document.

    Args:
        doc: The top-level catalogue JSON object.
        schema_version: Parsed catalogue schema version.  Schema version ``2``
            requires standard and security-profile coordinates.

    Returns:
        A validated :class:`CatalogueIdentity` instance.

    Raises:
        CatalogueParseError: If ``identity`` is absent, not an object, or
            any of its required string fields are missing or empty.
    """
    raw_identity = doc.get("identity")
    if not isinstance(raw_identity, dict):
        raise CatalogueParseError("Missing or invalid field 'identity': expected a JSON object")

    standard = _optional_string(raw_identity, "standard", context="identity")
    security_profile = _optional_string(raw_identity, "securityProfile", context="identity")
    if schema_version == 2:
        if standard is None:
            raise CatalogueParseError("Missing field 'identity.standard' for schemaVersion 2 catalogue")
        if security_profile is None:
            raise CatalogueParseError("Missing field 'identity.securityProfile' for schemaVersion 2 catalogue")

    return CatalogueIdentity(
        plugin_id=_require_string(raw_identity, "pluginId", context="identity"),
        specification=_require_string(raw_identity, "specification", context="identity"),
        specification_version=_require_string(raw_identity, "specificationVersion", context="identity"),
        content_hash=_require_string(raw_identity, "contentHash", context="identity"),
        standard=standard,
        security_profile=security_profile,
        version_aliases=_parse_string_array(raw_identity, "versionAliases", context="identity", required=False),
    )


_VALID_REQUIREMENTS: frozenset[str] = frozenset({"mandatory", "conditional", "optional"})
"""Accepted :data:`EndpointRequirement` values for the parser."""


def _parse_endpoints(
    doc: dict[str, JsonValue],
    *,
    resource_groups: tuple[CatalogueResourceGroup, ...],
) -> tuple[EndpointCatalogueEntry, ...]:
    """Parse the ``endpoints`` array from a raw catalogue document.

    Args:
        doc: The top-level catalogue JSON object.
        resource_groups: Parsed schema v2 resource-group sections.  When the
            top-level ``endpoints`` field is absent, endpoints are flattened
            from these sections.

    Returns:
        A tuple of validated :class:`EndpointCatalogueEntry` instances in
        document order.

    Raises:
        CatalogueParseError: If ``endpoints`` is absent, not a JSON array,
            or any element is structurally invalid.
    """
    raw_endpoints = doc.get("endpoints")
    if raw_endpoints is None and resource_groups:
        return _endpoints_from_resource_group_sections(doc)
    if not isinstance(raw_endpoints, list):
        raise CatalogueParseError("Missing or invalid field 'endpoints': expected a JSON array")

    return _parse_endpoint_array(raw_endpoints, context="endpoints", default_resource_group=None)


def _parse_endpoint_array(
    raw_endpoints: list[JsonValue],
    *,
    context: str,
    default_resource_group: str | None,
) -> tuple[EndpointCatalogueEntry, ...]:
    """Parse an endpoint array from either top-level or resource-group context.

    Args:
        raw_endpoints: Raw endpoint JSON array.
        context: Dot-path context for error messages.
        default_resource_group: Resource group to apply when an endpoint omits
            ``resourceGroup``.

    Returns:
        A tuple of validated endpoint entries.

    Raises:
        CatalogueParseError: If any element is structurally invalid.
    """
    entries: list[EndpointCatalogueEntry] = []
    for idx, raw_entry in enumerate(raw_endpoints):
        if not isinstance(raw_entry, dict):
            raise CatalogueParseError(f"{context}[{idx}] must be a JSON object, got {type(raw_entry).__name__!r}")
        entries.append(
            _parse_endpoint_entry(
                raw_entry,
                idx,
                context=context,
                default_resource_group=default_resource_group,
            )
        )
    return tuple(entries)


def _parse_endpoint_entry(
    raw: dict[str, JsonValue],
    idx: int,
    *,
    context: str = "endpoints",
    default_resource_group: str | None = None,
) -> EndpointCatalogueEntry:
    """Parse a single endpoint entry from the ``endpoints`` array.

    Args:
        raw: The raw JSON object for one endpoint entry.
        idx: Zero-based index used in error messages.
        context: Dot-path context used in validation errors.
        default_resource_group: Resource group applied when the endpoint omits
            ``resourceGroup``.

    Returns:
        A validated :class:`EndpointCatalogueEntry`.

    Raises:
        CatalogueParseError: If any required field is missing, has the wrong
            type, or has an invalid value.
    """
    ctx = f"{context}[{idx}]"

    endpoint_id = _require_string(raw, "endpointId", context=ctx)
    operation = _require_string(raw, "operation", context=ctx)
    path = _require_string(raw, "path", context=ctx)
    method = _require_string(raw, "method", context=ctx)
    display_label = _require_string(raw, "displayLabel", context=ctx)

    requirement_raw = _require_string(raw, "requirement", context=ctx)
    if requirement_raw not in _VALID_REQUIREMENTS:
        sorted_valid = sorted(_VALID_REQUIREMENTS)
        raise CatalogueParseError(f"{ctx}.requirement {requirement_raw!r} is not valid; expected one of {sorted_valid}")

    resource_group = _parse_resource_group(
        raw,
        idx,
        context=context,
        default_resource_group=default_resource_group,
    )

    return EndpointCatalogueEntry(
        endpoint_id=endpoint_id,
        operation=operation,
        path=path,
        method=method,
        resource_group=resource_group,
        requirement=cast(EndpointRequirement, requirement_raw),
        display_label=display_label,
        schema_ref=_optional_string(raw, "schemaRef", context=ctx),
        test_applicability_rules=_parse_json_object_array(
            raw,
            "testApplicabilityRules",
            context=ctx,
            required=False,
        ),
        source_citations=_parse_string_array(raw, "sourceCitations", context=ctx, required=False),
    )


def _parse_resource_group(
    raw: dict[str, JsonValue],
    idx: int,
    *,
    context: str = "endpoints",
    default_resource_group: str | None = None,
) -> str | None:
    """Parse the optional ``resourceGroup`` field from an endpoint entry.

    ``null`` and absent use ``default_resource_group``.  A non-empty string is
    returned as-is.

    Args:
        raw: The raw JSON object for one endpoint entry.
        idx: Zero-based index used in error messages for this entry.
        context: Dot-path context used in validation errors.
        default_resource_group: Resource group returned when
            ``resourceGroup`` is absent.

    Returns:
        The resource-group string, or ``default_resource_group`` if absent or
        ``null``.

    Raises:
        CatalogueParseError: If ``resourceGroup`` is present, not null, and
            not a non-empty string.
    """
    ctx = f"{context}[{idx}]"
    raw_rg = raw.get("resourceGroup")
    if raw_rg is None:
        return default_resource_group
    if not isinstance(raw_rg, str):
        raise CatalogueParseError(f"{ctx}.resourceGroup must be a string or null, got {type(raw_rg).__name__!r}")
    if not raw_rg:
        raise CatalogueParseError(f"{ctx}.resourceGroup must not be an empty string")
    return raw_rg


def _parse_resource_group_sections(doc: dict[str, JsonValue]) -> tuple[CatalogueResourceGroup, ...]:
    """Parse schema v2 resource-group sections from a catalogue document.

    Args:
        doc: The top-level catalogue JSON object.

    Returns:
        Ordered resource-group sections, or an empty tuple when absent.

    Raises:
        CatalogueParseError: If the sections are malformed.
    """
    raw_groups = doc.get("resourceGroups")
    if raw_groups is None:
        return ()
    if not isinstance(raw_groups, list):
        raise CatalogueParseError("Field 'resourceGroups' must be a JSON array when present")

    groups: list[CatalogueResourceGroup] = []
    for idx, raw_group in enumerate(raw_groups):
        if not isinstance(raw_group, dict):
            raise CatalogueParseError(f"resourceGroups[{idx}] must be a JSON object")
        groups.append(_parse_resource_group_section(raw_group, idx))
    return tuple(groups)


def _parse_resource_group_section(raw: JsonObject, idx: int) -> CatalogueResourceGroup:
    """Parse one schema v2 resource-group section.

    Args:
        raw: Raw resource-group JSON object.
        idx: Zero-based index used in error messages.

    Returns:
        A validated resource-group section.

    Raises:
        CatalogueParseError: If the section is malformed.
    """
    ctx = f"resourceGroups[{idx}]"
    requirement_raw = raw.get("requirement", "optional")
    if not isinstance(requirement_raw, str) or requirement_raw not in _VALID_REQUIREMENTS:
        sorted_valid = sorted(_VALID_REQUIREMENTS)
        raise CatalogueParseError(f"{ctx}.requirement must be one of {sorted_valid}")
    raw_endpoints = raw.get("endpoints", [])
    if not isinstance(raw_endpoints, list):
        raise CatalogueParseError(f"{ctx}.endpoints must be a JSON array when present")
    endpoint_ids = []
    for endpoint_idx, raw_endpoint in enumerate(raw_endpoints):
        if not isinstance(raw_endpoint, dict):
            raise CatalogueParseError(f"{ctx}.endpoints[{endpoint_idx}] must be a JSON object")
        endpoint_ids.append(
            _require_string(
                raw_endpoint,
                "endpointId",
                context=f"{ctx}.endpoints[{endpoint_idx}]",
            )
        )
    return CatalogueResourceGroup(
        resource_group=_require_string(raw, "id", context=ctx),
        display_label=_require_string(raw, "displayLabel", context=ctx),
        requirement=cast(EndpointRequirement, requirement_raw),
        endpoint_ids=tuple(endpoint_ids),
        source_citations=_parse_string_array(raw, "sourceCitations", context=ctx, required=False),
    )


def _endpoints_from_resource_group_sections(doc: dict[str, JsonValue]) -> tuple[EndpointCatalogueEntry, ...]:
    """Flatten endpoint entries embedded in schema v2 resource-group sections.

    Args:
        doc: The top-level catalogue JSON object.

    Returns:
        Ordered endpoint entries from all resource-group sections.

    Raises:
        CatalogueParseError: If embedded endpoint arrays are malformed.
    """
    raw_groups = doc.get("resourceGroups")
    if not isinstance(raw_groups, list):
        return ()
    entries: list[EndpointCatalogueEntry] = []
    for group_idx, raw_group in enumerate(raw_groups):
        if not isinstance(raw_group, dict):
            continue
        resource_group = _require_string(raw_group, "id", context=f"resourceGroups[{group_idx}]")
        raw_endpoints = raw_group.get("endpoints", [])
        if not isinstance(raw_endpoints, list):
            raise CatalogueParseError(f"resourceGroups[{group_idx}].endpoints must be a JSON array when present")
        entries.extend(
            _parse_endpoint_array(
                raw_endpoints,
                context=f"resourceGroups[{group_idx}].endpoints",
                default_resource_group=resource_group,
            )
        )
    return tuple(entries)


def _parse_field_schemas(doc: dict[str, JsonValue]) -> tuple[CatalogueFieldSchema, ...]:
    """Parse field/value schema metadata.

    Args:
        doc: The top-level catalogue JSON object.

    Returns:
        Ordered field schema entries.

    Raises:
        CatalogueParseError: If any field schema is malformed.
    """
    raw_fields = doc.get("fieldSchemas", [])
    if not isinstance(raw_fields, list):
        raise CatalogueParseError("Field 'fieldSchemas' must be a JSON array when present")
    fields: list[CatalogueFieldSchema] = []
    for idx, raw_field in enumerate(raw_fields):
        if not isinstance(raw_field, dict):
            raise CatalogueParseError(f"fieldSchemas[{idx}] must be a JSON object")
        ctx = f"fieldSchemas[{idx}]"
        fields.append(
            CatalogueFieldSchema(
                field_id=_require_string(raw_field, "fieldId", context=ctx),
                display_label=_require_string(raw_field, "displayLabel", context=ctx),
                scope=_require_string(raw_field, "scope", context=ctx),
                value_type=_require_string(raw_field, "valueType", context=ctx),
                required=_require_bool(raw_field, "required", context=ctx),
                sensitive=_optional_bool(raw_field, "sensitive", default=False),
                helper_text=_optional_string(raw_field, "helperText", context=ctx),
                visible_when=_parse_field_visibility_conditions(raw_field, context=ctx),
            )
        )
    return tuple(fields)


def _parse_field_visibility_conditions(
    obj: dict[str, JsonValue],
    *,
    context: str,
) -> tuple[CatalogueFieldVisibilityCondition, ...]:
    """Parse optional field-visibility predicates from one field schema object.

    Args:
        obj: Raw field schema object.
        context: Dot-path context for validation errors.

    Returns:
        Ordered visibility predicates, or an empty tuple when absent.

    Raises:
        CatalogueParseError: If ``visibleWhen`` is present but malformed.
    """
    raw_conditions = obj.get("visibleWhen")
    if raw_conditions is None:
        return ()
    if not isinstance(raw_conditions, list):
        raise CatalogueParseError(f"{context}.visibleWhen must be a JSON array")

    conditions: list[CatalogueFieldVisibilityCondition] = []
    for idx, raw_condition in enumerate(raw_conditions):
        if not isinstance(raw_condition, dict):
            raise CatalogueParseError(f"{context}.visibleWhen[{idx}] must be a JSON object")
        condition_context = f"{context}.visibleWhen[{idx}]"
        conditions.append(
            CatalogueFieldVisibilityCondition(
                field_id=_require_string(raw_condition, "fieldId", context=condition_context),
                equals=_parse_string_array(raw_condition, "equals", context=condition_context, required=True),
            )
        )
    return tuple(conditions)


def _parse_runner_primitives(doc: dict[str, JsonValue]) -> tuple[CatalogueRunnerPrimitive, ...]:
    """Parse runner primitive metadata.

    Args:
        doc: The top-level catalogue JSON object.

    Returns:
        Ordered runner primitive entries.

    Raises:
        CatalogueParseError: If any primitive is malformed.
    """
    raw_primitives = doc.get("runnerPrimitives", [])
    if not isinstance(raw_primitives, list):
        raise CatalogueParseError("Field 'runnerPrimitives' must be a JSON array when present")
    primitives: list[CatalogueRunnerPrimitive] = []
    for idx, raw_primitive in enumerate(raw_primitives):
        if not isinstance(raw_primitive, dict):
            raise CatalogueParseError(f"runnerPrimitives[{idx}] must be a JSON object")
        ctx = f"runnerPrimitives[{idx}]"
        primitives.append(
            CatalogueRunnerPrimitive(
                primitive_id=_require_string(raw_primitive, "primitiveId", context=ctx),
                primitive_type=_require_string(raw_primitive, "type", context=ctx),
                description=_require_string(raw_primitive, "description", context=ctx),
            )
        )
    return tuple(primitives)


def _parse_executable_tests(doc: dict[str, JsonValue]) -> tuple[CatalogueExecutableTest, ...]:
    """Parse executable catalogue test definitions.

    Args:
        doc: The top-level catalogue JSON object.

    Returns:
        Ordered executable test definitions.

    Raises:
        CatalogueParseError: If any test definition is malformed.
    """
    raw_tests = doc.get("tests", [])
    if not isinstance(raw_tests, list):
        raise CatalogueParseError("Field 'tests' must be a JSON array when present")
    tests: list[CatalogueExecutableTest] = []
    for idx, raw_test in enumerate(raw_tests):
        if not isinstance(raw_test, dict):
            raise CatalogueParseError(f"tests[{idx}] must be a JSON object")
        tests.append(_parse_executable_test(raw_test, idx))
    return tuple(tests)


def _parse_executable_test(raw: JsonObject, idx: int) -> CatalogueExecutableTest:
    """Parse one executable catalogue test definition.

    Args:
        raw: Raw executable test JSON object.
        idx: Zero-based index used in error messages.

    Returns:
        A validated executable test definition.

    Raises:
        CatalogueParseError: If the test definition is malformed.
    """
    ctx = f"tests[{idx}]"
    return CatalogueExecutableTest(
        test_id=_require_string(raw, "testId", context=ctx),
        display_label=_require_string(raw, "displayLabel", context=ctx),
        endpoint_id=_optional_string(raw, "endpointId", context=ctx),
        resource_group=_optional_string(raw, "resourceGroup", context=ctx),
        runner_primitive_id=_require_string(raw, "runnerPrimitiveId", context=ctx),
        applicability=_parse_applicability(raw, context=ctx),
        source_coverage=_parse_source_coverage_refs(raw, context=ctx),
    )


def _parse_applicability(raw: JsonObject, *, context: str) -> tuple[CatalogueApplicabilityPredicate, ...]:
    """Parse applicability predicates for one executable test.

    Args:
        raw: Raw executable test JSON object.
        context: Dot-path context for error messages.

    Returns:
        Ordered applicability predicates.

    Raises:
        CatalogueParseError: If the predicate array is malformed.
    """
    raw_predicates = raw.get("applicability", [])
    if not isinstance(raw_predicates, list):
        raise CatalogueParseError(f"{context}.applicability must be a JSON array when present")
    predicates: list[CatalogueApplicabilityPredicate] = []
    for idx, raw_predicate in enumerate(raw_predicates):
        if not isinstance(raw_predicate, dict):
            raise CatalogueParseError(f"{context}.applicability[{idx}] must be a JSON object")
        predicate_ctx = f"{context}.applicability[{idx}]"
        parameters = raw_predicate.get("parameters", {})
        if not isinstance(parameters, dict):
            raise CatalogueParseError(f"{predicate_ctx}.parameters must be a JSON object when present")
        predicates.append(
            CatalogueApplicabilityPredicate(
                predicate_type=_require_string(raw_predicate, "type", context=predicate_ctx),
                parameters=MappingProxyType(dict(parameters)),
            )
        )
    return tuple(predicates)


def _parse_source_coverage_refs(raw: JsonObject, *, context: str) -> tuple[CatalogueSourceCoverageRef, ...]:
    """Parse source coverage references for one executable test.

    Args:
        raw: Raw executable test JSON object.
        context: Dot-path context for error messages.

    Returns:
        Ordered source coverage references.

    Raises:
        CatalogueParseError: If the source coverage array is malformed.
    """
    raw_refs = raw.get("sourceCoverage", [])
    if not isinstance(raw_refs, list):
        raise CatalogueParseError(f"{context}.sourceCoverage must be a JSON array when present")
    refs: list[CatalogueSourceCoverageRef] = []
    for idx, raw_ref in enumerate(raw_refs):
        if not isinstance(raw_ref, dict):
            raise CatalogueParseError(f"{context}.sourceCoverage[{idx}] must be a JSON object")
        ref_ctx = f"{context}.sourceCoverage[{idx}]"
        refs.append(
            CatalogueSourceCoverageRef(
                source_kind=_require_string(raw_ref, "sourceKind", context=ref_ctx),
                source_id=_require_string(raw_ref, "sourceId", context=ref_ctx),
                mapping_mode=_require_string(raw_ref, "mappingMode", context=ref_ctx),
                source_files=_parse_string_array(raw_ref, "sourceFiles", context=ref_ctx, required=False),
            )
        )
    return tuple(refs)


def _parse_readiness_policy(doc: dict[str, JsonValue]) -> CatalogueReadinessPolicy | None:
    """Parse optional readiness policy metadata.

    Args:
        doc: The top-level catalogue JSON object.

    Returns:
        Parsed readiness policy, or ``None`` when absent.

    Raises:
        CatalogueParseError: If the readiness policy is malformed.
    """
    raw_policy = doc.get("readinessPolicy")
    if raw_policy is None:
        return None
    if not isinstance(raw_policy, dict):
        raise CatalogueParseError("Field 'readinessPolicy' must be a JSON object when present")
    return CatalogueReadinessPolicy(
        policy_id=_require_string(raw_policy, "policyId", context="readinessPolicy"),
        certification_status=_require_string(raw_policy, "certificationStatus", context="readinessPolicy"),
        omitted_mandatory_outcome=_require_string(
            raw_policy,
            "omittedMandatoryOutcome",
            context="readinessPolicy",
        ),
        failed_selected_outcome=_require_string(raw_policy, "failedSelectedOutcome", context="readinessPolicy"),
    )


def _parse_masking(doc: dict[str, JsonValue]) -> CatalogueMaskingMetadata | None:
    """Parse optional masking metadata.

    Args:
        doc: The top-level catalogue JSON object.

    Returns:
        Parsed masking metadata, or ``None`` when absent.

    Raises:
        CatalogueParseError: If the masking metadata is malformed.
    """
    raw_masking = doc.get("masking")
    if raw_masking is None:
        return None
    if not isinstance(raw_masking, dict):
        raise CatalogueParseError("Field 'masking' must be a JSON object when present")
    return CatalogueMaskingMetadata(
        masked_fields=_parse_string_array(raw_masking, "maskedFields", context="masking", required=False),
        evidence_paths=_parse_string_array(raw_masking, "evidencePaths", context="masking", required=False),
    )


def _parse_source_coverage(doc: dict[str, JsonValue]) -> Mapping[str, JsonValue]:
    """Parse catalogue-level source coverage metadata.

    Args:
        doc: The top-level catalogue JSON object.

    Returns:
        Immutable source coverage metadata mapping.

    Raises:
        CatalogueParseError: If the metadata is present but not a JSON object.
    """
    raw_coverage = doc.get("sourceCoverage", {})
    if not isinstance(raw_coverage, dict):
        raise CatalogueParseError("Field 'sourceCoverage' must be a JSON object when present")
    return MappingProxyType(dict(raw_coverage))


def _parse_string_array(
    obj: dict[str, JsonValue],
    key: str,
    *,
    context: str,
    required: bool,
) -> tuple[str, ...]:
    """Parse a string array field.

    Args:
        obj: JSON object containing the array.
        key: Field name to parse.
        context: Dot-path context for error messages.
        required: Whether the field must be present.

    Returns:
        Tuple of parsed strings, or an empty tuple when optional and absent.

    Raises:
        CatalogueParseError: If the field is missing when required or contains
            non-string or empty-string items.
    """
    raw_array = obj.get(key)
    if raw_array is None:
        if required:
            raise CatalogueParseError(f"Missing field {context}.{key}")
        return ()
    if not isinstance(raw_array, list):
        raise CatalogueParseError(f"{context}.{key} must be a JSON array")
    values: list[str] = []
    for idx, item in enumerate(raw_array):
        if not isinstance(item, str):
            raise CatalogueParseError(f"{context}.{key}[{idx}] must be a string")
        if not item:
            raise CatalogueParseError(f"{context}.{key}[{idx}] must not be empty")
        values.append(item)
    return tuple(values)


def _parse_json_object_array(
    obj: dict[str, JsonValue],
    key: str,
    *,
    context: str,
    required: bool,
) -> tuple[Mapping[str, JsonValue], ...]:
    """Parse an array of JSON objects.

    Args:
        obj: JSON object containing the array.
        key: Field name to parse.
        context: Dot-path context for error messages.
        required: Whether the field must be present.

    Returns:
        Tuple of immutable JSON object mappings.

    Raises:
        CatalogueParseError: If the field is missing when required or contains
            non-object items.
    """
    raw_array = obj.get(key)
    if raw_array is None:
        if required:
            raise CatalogueParseError(f"Missing field {context}.{key}")
        return ()
    if not isinstance(raw_array, list):
        raise CatalogueParseError(f"{context}.{key} must be a JSON array")
    values: list[Mapping[str, JsonValue]] = []
    for idx, item in enumerate(raw_array):
        if not isinstance(item, dict):
            raise CatalogueParseError(f"{context}.{key}[{idx}] must be a JSON object")
        values.append(MappingProxyType(dict(item)))
    return tuple(values)


def _validate_unique_endpoint_ids(endpoints: tuple[EndpointCatalogueEntry, ...]) -> None:
    """Validate endpoint identifiers are unique.

    Args:
        endpoints: Parsed endpoint entries.

    Raises:
        CatalogueParseError: If duplicate endpoint identifiers are present.
    """
    seen: set[str] = set()
    duplicates: set[str] = set()
    for endpoint in endpoints:
        if endpoint.endpoint_id in seen:
            duplicates.add(endpoint.endpoint_id)
        seen.add(endpoint.endpoint_id)
    if duplicates:
        raise CatalogueParseError(f"Duplicate endpointId values in catalogue: {sorted(duplicates)}")


def _validate_unique_test_ids(tests: tuple[CatalogueExecutableTest, ...]) -> None:
    """Validate executable test identifiers are unique.

    Args:
        tests: Parsed executable test definitions.

    Raises:
        CatalogueParseError: If duplicate test identifiers are present.
    """
    seen: set[str] = set()
    duplicates: set[str] = set()
    for test in tests:
        if test.test_id in seen:
            duplicates.add(test.test_id)
        seen.add(test.test_id)
    if duplicates:
        raise CatalogueParseError(f"Duplicate testId values in catalogue: {sorted(duplicates)}")
