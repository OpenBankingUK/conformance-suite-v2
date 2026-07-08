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
from dataclasses import dataclass
from typing import Literal

from conformance.json_types import JsonValue

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
    """

    plugin_id: str
    specification: str
    specification_version: str
    content_hash: str


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
    """

    endpoint_id: str
    operation: str
    path: str
    method: str
    resource_group: str | None
    requirement: EndpointRequirement
    display_label: str


@dataclass(frozen=True)
class Catalogue:
    """In-memory representation of a validated versioned JSON catalogue.

    Attributes:
        identity: Identity and drift-detection metadata.
        endpoints: Ordered tuple of endpoint catalogue entries.
    """

    identity: CatalogueIdentity
    endpoints: tuple[EndpointCatalogueEntry, ...]


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

    identity = _parse_identity(raw)
    endpoints = _parse_endpoints(raw)

    return Catalogue(identity=identity, endpoints=endpoints)


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


def _parse_identity(doc: dict[str, JsonValue]) -> CatalogueIdentity:
    """Parse the ``identity`` block from a raw catalogue document.

    Args:
        doc: The top-level catalogue JSON object.

    Returns:
        A validated :class:`CatalogueIdentity` instance.

    Raises:
        CatalogueParseError: If ``identity`` is absent, not an object, or
            any of its required string fields are missing or empty.
    """
    raw_identity = doc.get("identity")
    if not isinstance(raw_identity, dict):
        raise CatalogueParseError("Missing or invalid field 'identity': expected a JSON object")

    return CatalogueIdentity(
        plugin_id=_require_string(raw_identity, "pluginId", context="identity"),
        specification=_require_string(raw_identity, "specification", context="identity"),
        specification_version=_require_string(raw_identity, "specificationVersion", context="identity"),
        content_hash=_require_string(raw_identity, "contentHash", context="identity"),
    )


_VALID_REQUIREMENTS: frozenset[str] = frozenset({"mandatory", "conditional", "optional"})
"""Accepted :data:`EndpointRequirement` values for the parser."""


def _parse_endpoints(doc: dict[str, JsonValue]) -> tuple[EndpointCatalogueEntry, ...]:
    """Parse the ``endpoints`` array from a raw catalogue document.

    Args:
        doc: The top-level catalogue JSON object.

    Returns:
        A tuple of validated :class:`EndpointCatalogueEntry` instances in
        document order.

    Raises:
        CatalogueParseError: If ``endpoints`` is absent, not a JSON array,
            or any element is structurally invalid.
    """
    raw_endpoints = doc.get("endpoints")
    if not isinstance(raw_endpoints, list):
        raise CatalogueParseError("Missing or invalid field 'endpoints': expected a JSON array")

    entries: list[EndpointCatalogueEntry] = []
    for idx, raw_entry in enumerate(raw_endpoints):
        if not isinstance(raw_entry, dict):
            raise CatalogueParseError(f"endpoints[{idx}] must be a JSON object, got {type(raw_entry).__name__!r}")
        entries.append(_parse_endpoint_entry(raw_entry, idx))
    return tuple(entries)


def _parse_endpoint_entry(raw: dict[str, JsonValue], idx: int) -> EndpointCatalogueEntry:
    """Parse a single endpoint entry from the ``endpoints`` array.

    Args:
        raw: The raw JSON object for one endpoint entry.
        idx: Zero-based index used in error messages.

    Returns:
        A validated :class:`EndpointCatalogueEntry`.

    Raises:
        CatalogueParseError: If any required field is missing, has the wrong
            type, or has an invalid value.
    """
    ctx = f"endpoints[{idx}]"

    endpoint_id = _require_string(raw, "endpointId", context=ctx)
    operation = _require_string(raw, "operation", context=ctx)
    path = _require_string(raw, "path", context=ctx)
    method = _require_string(raw, "method", context=ctx)
    display_label = _require_string(raw, "displayLabel", context=ctx)

    requirement_raw = _require_string(raw, "requirement", context=ctx)
    if requirement_raw not in _VALID_REQUIREMENTS:
        sorted_valid = sorted(_VALID_REQUIREMENTS)
        raise CatalogueParseError(f"{ctx}.requirement {requirement_raw!r} is not valid; expected one of {sorted_valid}")

    resource_group = _parse_resource_group(raw, idx)

    return EndpointCatalogueEntry(
        endpoint_id=endpoint_id,
        operation=operation,
        path=path,
        method=method,
        resource_group=resource_group,
        requirement=requirement_raw,  # type: ignore[arg-type]
        display_label=display_label,
    )


def _parse_resource_group(raw: dict[str, JsonValue], idx: int) -> str | None:
    """Parse the optional ``resourceGroup`` field from an endpoint entry.

    ``null`` and absent are both treated as "no resource group" and return
    ``None``.  A non-empty string is returned as-is.

    Args:
        raw: The raw JSON object for one endpoint entry.
        idx: Zero-based index used in error messages for this entry.

    Returns:
        The resource-group string, or ``None`` if absent or ``null``.

    Raises:
        CatalogueParseError: If ``resourceGroup`` is present, not null, and
            not a non-empty string.
    """
    ctx = f"endpoints[{idx}]"
    raw_rg = raw.get("resourceGroup")
    if raw_rg is None:
        return None
    if not isinstance(raw_rg, str):
        raise CatalogueParseError(f"{ctx}.resourceGroup must be a string or null, got {type(raw_rg).__name__!r}")
    if not raw_rg:
        raise CatalogueParseError(f"{ctx}.resourceGroup must not be an empty string")
    return raw_rg
