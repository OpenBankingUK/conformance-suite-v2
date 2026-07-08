"""Schema v2 Run Plan: intent-based endpoint-first participant artifact.

A :class:`RunPlanV2` is the participant-owned artifact produced by the guided
plan-builder UI or a headless CLI run.  It stores *intent* — which target
coordinates the participant selected, which resource groups and endpoints they
chose, and what field values they provided — rather than an explicit list of
executable step IDs.

The engine derives the applicable test list from the frozen catalogue and the
stored selections at execution time.  Storing intent rather than a derived step
list means the plan remains meaningful even when catalogue contents evolve,
and catalogue drift is detected via the stored ``catalogue_hash``.

Contrast with :mod:`conformance.run_plan` (schema version ``"1"``), which is
suite-centric and stores an explicit ``selectedStepIds`` list.

Wire format: JSON uses camelCase keys (``specificationVersion``,
``resourceGroups``, ``endpointSelections``, ``fieldValues``, ``catalogueHash``)
to match the REST API and plan-builder UI.

:func:`parse_run_plan_v2` converts from JSON; :func:`serialise_run_plan_v2`
converts back.  :func:`compute_catalogue_hash` from
:mod:`conformance.catalogue` provides the hash format used in
:attr:`RunPlanV2TargetCoordinates.catalogue_hash`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Literal

from conformance.json_types import JsonValue

# ---------------------------------------------------------------------------
# Public type alias
# ---------------------------------------------------------------------------

type RunPlanV2SchemaVersion = Literal["2"]
"""Wire schema version discriminator for :class:`RunPlanV2` JSON documents.

Currently only ``"2"`` is valid.  Schema version ``"1"`` is the suite-centric
:class:`~conformance.run_plan.RunPlan`.  Readers must reject unknown versions.
"""

# ---------------------------------------------------------------------------
# Domain dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RunPlanV2TargetCoordinates:
    """Target identity coordinates stored inside a :class:`RunPlanV2`.

    Identifies the conformance target the plan was authored against and
    records the content hash of the catalogue at authoring time so the engine
    can detect catalogue drift.

    Attributes:
        standard: Top-level conformance standard (e.g. ``"obl"``).
        specification: Specification under the standard
            (e.g. ``"read-write"``).
        security_profile: Fixed security profile
            (always ``"fapi1-advanced"``).
        specification_version: Standards version string
            (e.g. ``"v4.0.1"``).
        catalogue_hash: SHA-256 hex digest of the catalogue JSON bytes in the
            canonical ``"sha256:<hex>"`` format returned by
            :func:`~conformance.catalogue.compute_catalogue_hash`.
    """

    standard: str
    specification: str
    security_profile: str
    specification_version: str
    catalogue_hash: str


@dataclass(frozen=True)
class EndpointSelection:
    """Participant's coverage choice for a single endpoint operation.

    Stores whether the endpoint was selected plus any per-endpoint field
    values the participant provided (e.g. request body fragments or
    configuration knobs).

    Attributes:
        endpoint_id: Stable catalogue endpoint identifier
            (e.g. ``"get-accounts"``).
        operation: HTTP operation name (e.g. ``"GET"``).
        selected: ``True`` when the participant has chosen to include this
            endpoint in the run.
        field_values: Immutable mapping of endpoint-specific field names to
            participant-supplied string values.  Empty when no fields were
            configured.
    """

    endpoint_id: str
    operation: str
    selected: bool
    field_values: Mapping[str, str]


@dataclass(frozen=True)
class RunPlanV2:
    """Schema v2 intent-based endpoint-first participant execution contract.

    Stores user intent rather than a derived test list.  The engine resolves
    applicable tests from the catalogue and these selections at run time.

    Attributes:
        schema_version: Wire format version discriminator; always ``"2"``.
        target: Target identity coordinates including the catalogue hash.
        resource_groups: Ordered tuple of selected resource-group identifiers.
            Empty for specifications without resource groups (DCR).
        endpoint_selections: Ordered tuple of per-endpoint coverage choices.
        test_data: Participant test-data key/value overrides relative to the
            catalogue baseline.
    """

    schema_version: RunPlanV2SchemaVersion
    target: RunPlanV2TargetCoordinates
    resource_groups: tuple[str, ...]
    endpoint_selections: tuple[EndpointSelection, ...]
    test_data: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))


# ---------------------------------------------------------------------------
# Exception
# ---------------------------------------------------------------------------


class RunPlanV2ParseError(ValueError):
    """Raised when a raw JSON value cannot be parsed into a :class:`RunPlanV2`.

    Wraps :class:`ValueError` so callers can catch either the specific error
    or the generic base class.
    """


# ---------------------------------------------------------------------------
# Public functions
# ---------------------------------------------------------------------------


def parse_run_plan_v2(raw: JsonValue) -> RunPlanV2:
    """Parse and validate a raw JSON value into a :class:`RunPlanV2`.

    Validates the full document structure including schema version, target
    coordinates, resource groups, endpoint selections, and test data.  Raises
    :class:`RunPlanV2ParseError` with a human-readable message for every
    validation failure.

    Parsing rules:

    - The document must be a JSON object.
    - ``schemaVersion`` must be exactly the string ``"2"``.
    - ``target`` must be a JSON object with non-empty string fields
      ``standard``, ``specification``, ``securityProfile``,
      ``specificationVersion``, and ``catalogueHash``.
    - ``resourceGroups`` is optional; when present it must be a JSON array of
      non-empty strings.
    - ``endpointSelections`` is optional; when present it must be a JSON array
      where each element has a non-empty string ``endpointId``, a non-empty
      string ``operation``, a boolean ``selected``, and an optional
      ``fieldValues`` object whose values are all strings.
    - ``testData`` is optional; when present it must be a JSON object whose
      values are all strings.

    Args:
        raw: The parsed JSON value to validate.  Typically the result of
            ``json.loads()``.

    Returns:
        A fully validated :class:`RunPlanV2` instance.

    Raises:
        RunPlanV2ParseError: If the document is structurally invalid, a
            required field is missing or has the wrong type, or
            ``schemaVersion`` is not ``"2"``.
    """
    if not isinstance(raw, dict):
        raise RunPlanV2ParseError("RunPlanV2 must be a JSON object")

    schema_version = _require_string(raw, "schemaVersion")
    if schema_version != "2":
        raise RunPlanV2ParseError(f'Unsupported schemaVersion {schema_version!r}; expected "2"')

    target = _parse_target_coordinates(raw)
    resource_groups = _parse_resource_groups(raw)
    endpoint_selections = _parse_endpoint_selections(raw)
    test_data = _parse_test_data(raw)

    return RunPlanV2(
        schema_version="2",
        target=target,
        resource_groups=resource_groups,
        endpoint_selections=endpoint_selections,
        test_data=test_data,
    )


def serialise_run_plan_v2(plan: RunPlanV2) -> dict[str, JsonValue]:
    """Serialise a :class:`RunPlanV2` to a camelCase JSON-compatible dictionary.

    The output uses camelCase keys to match the wire format expected by the
    REST API and the plan-builder UI.  The result can be passed directly to
    ``json.dumps()``.

    Args:
        plan: The :class:`RunPlanV2` to serialise.

    Returns:
        A ``dict[str, JsonValue]`` ready for JSON serialisation.
    """
    result: dict[str, JsonValue] = {
        "schemaVersion": plan.schema_version,
        "target": {
            "standard": plan.target.standard,
            "specification": plan.target.specification,
            "securityProfile": plan.target.security_profile,
            "specificationVersion": plan.target.specification_version,
            "catalogueHash": plan.target.catalogue_hash,
        },
    }

    if plan.resource_groups:
        result["resourceGroups"] = list(plan.resource_groups)

    if plan.endpoint_selections:
        selections: list[JsonValue] = []
        for sel in plan.endpoint_selections:
            entry: dict[str, JsonValue] = {
                "endpointId": sel.endpoint_id,
                "operation": sel.operation,
                "selected": sel.selected,
            }
            if sel.field_values:
                entry["fieldValues"] = dict(sel.field_values)
            selections.append(entry)
        result["endpointSelections"] = selections

    if plan.test_data:
        result["testData"] = dict(plan.test_data)

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
        RunPlanV2ParseError: If the key is absent or its value is not a
            non-empty string.
    """
    value = obj.get(key)
    if not isinstance(value, str):
        raise RunPlanV2ParseError(
            f"Missing or invalid field {key!r}: expected a non-empty string, got {type(value).__name__!r}"
        )
    if not value:
        raise RunPlanV2ParseError(f"Field {key!r} must not be an empty string")
    return value


def _parse_target_coordinates(doc: dict[str, JsonValue]) -> RunPlanV2TargetCoordinates:
    """Parse the ``target`` object from a raw RunPlanV2 document.

    Args:
        doc: The top-level RunPlanV2 JSON object.

    Returns:
        A validated :class:`RunPlanV2TargetCoordinates` instance.

    Raises:
        RunPlanV2ParseError: If ``target`` is absent, not an object, or any
            required string field is missing or empty.
    """
    raw_target = doc.get("target")
    if not isinstance(raw_target, dict):
        raise RunPlanV2ParseError("Missing or invalid field 'target': expected a JSON object")

    return RunPlanV2TargetCoordinates(
        standard=_require_string(raw_target, "standard"),
        specification=_require_string(raw_target, "specification"),
        security_profile=_require_string(raw_target, "securityProfile"),
        specification_version=_require_string(raw_target, "specificationVersion"),
        catalogue_hash=_require_string(raw_target, "catalogueHash"),
    )


def _parse_resource_groups(doc: dict[str, JsonValue]) -> tuple[str, ...]:
    """Parse the optional ``resourceGroups`` array from a RunPlanV2 document.

    Args:
        doc: The top-level RunPlanV2 JSON object.

    Returns:
        A tuple of non-empty resource-group identifier strings, or an empty
        tuple when ``resourceGroups`` is absent.

    Raises:
        RunPlanV2ParseError: If ``resourceGroups`` is present but not a JSON
            array, or any element is not a non-empty string.
    """
    raw = doc.get("resourceGroups")
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise RunPlanV2ParseError("Field 'resourceGroups' must be a JSON array when present")
    groups: list[str] = []
    for idx, item in enumerate(raw):
        if not isinstance(item, str):
            raise RunPlanV2ParseError(f"resourceGroups[{idx}] must be a string, got {type(item).__name__!r}")
        if not item:
            raise RunPlanV2ParseError(f"resourceGroups[{idx}] must not be an empty string")
        groups.append(item)
    return tuple(groups)


def _parse_endpoint_selections(doc: dict[str, JsonValue]) -> tuple[EndpointSelection, ...]:
    """Parse the optional ``endpointSelections`` array from a RunPlanV2 document.

    Args:
        doc: The top-level RunPlanV2 JSON object.

    Returns:
        A tuple of :class:`EndpointSelection` instances in document order, or
        an empty tuple when ``endpointSelections`` is absent.

    Raises:
        RunPlanV2ParseError: If ``endpointSelections`` is present but not a
            JSON array, or any element is structurally invalid.
    """
    raw = doc.get("endpointSelections")
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise RunPlanV2ParseError("Field 'endpointSelections' must be a JSON array when present")
    selections: list[EndpointSelection] = []
    for idx, item in enumerate(raw):
        if not isinstance(item, dict):
            raise RunPlanV2ParseError(f"endpointSelections[{idx}] must be a JSON object, got {type(item).__name__!r}")
        selections.append(_parse_one_endpoint_selection(item, idx))
    return tuple(selections)


def _parse_one_endpoint_selection(raw: dict[str, JsonValue], idx: int) -> EndpointSelection:
    """Parse a single entry from the ``endpointSelections`` array.

    Args:
        raw: The raw JSON object for one endpoint selection.
        idx: Zero-based index used in error messages.

    Returns:
        A validated :class:`EndpointSelection`.

    Raises:
        RunPlanV2ParseError: If any required field is missing, has the wrong
            type, or an invalid value.
    """
    ctx = f"endpointSelections[{idx}]"
    endpoint_id = _require_string(raw, "endpointId")
    operation = _require_string(raw, "operation")

    raw_selected = raw.get("selected")
    if not isinstance(raw_selected, bool):
        raise RunPlanV2ParseError(f"{ctx}.selected must be a boolean, got {type(raw_selected).__name__!r}")

    field_values = _parse_field_values(raw, idx)

    return EndpointSelection(
        endpoint_id=endpoint_id,
        operation=operation,
        selected=raw_selected,
        field_values=field_values,
    )


def _parse_field_values(raw: dict[str, JsonValue], idx: int) -> Mapping[str, str]:
    """Parse the optional ``fieldValues`` object from an endpoint selection entry.

    Args:
        raw: The raw JSON object for one endpoint selection.
        idx: Zero-based index used in error messages.

    Returns:
        An immutable mapping of field names to string values, or an empty
        mapping when ``fieldValues`` is absent.

    Raises:
        RunPlanV2ParseError: If ``fieldValues`` is present but not a JSON
            object, or any value is not a string.
    """
    ctx = f"endpointSelections[{idx}]"
    raw_fv = raw.get("fieldValues")
    if raw_fv is None:
        return MappingProxyType({})
    if not isinstance(raw_fv, dict):
        raise RunPlanV2ParseError(f"{ctx}.fieldValues must be a JSON object when present")
    values: dict[str, str] = {}
    for k, v in raw_fv.items():
        if not isinstance(v, str):
            raise RunPlanV2ParseError(f"{ctx}.fieldValues[{k!r}] must be a string, got {type(v).__name__!r}")
        values[k] = v
    return MappingProxyType(values)


def _parse_test_data(doc: dict[str, JsonValue]) -> Mapping[str, str]:
    """Parse the optional ``testData`` object from a RunPlanV2 document.

    Args:
        doc: The top-level RunPlanV2 JSON object.

    Returns:
        An immutable mapping of test-data key names to string values, or an
        empty mapping when ``testData`` is absent.

    Raises:
        RunPlanV2ParseError: If ``testData`` is present but not a JSON object,
            or any value is not a string.
    """
    raw = doc.get("testData")
    if raw is None:
        return MappingProxyType({})
    if not isinstance(raw, dict):
        raise RunPlanV2ParseError("Field 'testData' must be a JSON object when present")
    values: dict[str, str] = {}
    for k, v in raw.items():
        if not isinstance(v, str):
            raise RunPlanV2ParseError(f"testData[{k!r}] must be a string, got {type(v).__name__!r}")
        values[k] = v
    return MappingProxyType(values)
