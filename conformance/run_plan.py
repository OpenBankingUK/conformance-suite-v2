"""Schema-versioned RunPlan artifact: participant-owned suite execution contract.

A :class:`RunPlan` captures the full participant intent for a single conformance
run: which suite (by id and version), a SHA-256 manifest hash for drift detection,
the ordered subset of step ids they wish to execute, and their test-value choices
(a named profile plus an optional map of custom-value deltas).

The JSON wire format uses camelCase keys to remain compatible with the REST API
and plan-builder UI. :func:`parse_run_plan` converts from the JSON representation
to the internal dataclass tree; :func:`serialise_run_plan` converts back.

Drift detection: when the ``suite.manifestHash`` in a saved plan does not match
the hash of the currently-loaded manifest bytes (computed with
:func:`compute_manifest_hash`), plan *preview* is permitted but run *launch* is
blocked by the engine. That check lives in the executor, not here.

Backward-compat adapter: :func:`run_plan_to_test_values_config` converts the
``testValues`` section of a :class:`RunPlan` to the existing
:class:`~conformance.model_bank_config.TestValuesConfig` type so the executor
runtime path does not need to be changed to accept the new domain object
directly.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Literal

from conformance.json_types import JsonValue
from conformance.model_bank_config import TestValuesConfig

# ---------------------------------------------------------------------------
# Public type aliases
# ---------------------------------------------------------------------------

type RunPlanSchemaVersion = Literal["1"]
"""Wire schema version discriminator for :class:`RunPlan` JSON documents.

Currently only ``"1"`` is valid.  Future incompatible changes will increment
this to ``"2"``, ``"3"``, etc.  Readers must reject unknown versions so that
stale clients do not silently misinterpret new fields.
"""

# ---------------------------------------------------------------------------
# Domain dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RunPlanSuiteCoordinates:
    """Suite identity coordinates stored inside a :class:`RunPlan`.

    Uniquely identifies the Certification Test Set / Suite Manifest that the
    plan was authored against, and records a content hash of the manifest bytes
    so the engine can detect if the manifest has drifted since the plan was
    saved.

    Attributes:
        id: Suite identifier string (e.g. ``"aisp-v3-2"``).
        version: Suite version string (e.g. ``"4.0.1"``).
        manifest_hash: SHA-256 hex digest of the raw manifest bytes, in the
            canonical ``"sha256:<hex>"`` format returned by
            :func:`compute_manifest_hash`.
    """

    id: str
    version: str
    manifest_hash: str


@dataclass(frozen=True)
class RunPlanTestValues:
    """Participant test-value choices stored inside a :class:`RunPlan`.

    Captures both the profile selection (which named set of default values to
    activate) and the delta of custom override keys that differ from that
    profile.  An absent profile means "use the manifest's declared default
    profile".  An empty ``custom_values`` mapping means "no overrides".

    Empty-string values inside ``custom_values`` are explicitly valid — they
    signal that the participant has chosen to supply an empty string, which is
    semantically distinct from omitting the key entirely.

    Attributes:
        profile: Optional profile identifier.  ``None`` means use the
            manifest's ``defaultProfileId``.
        custom_values: Immutable mapping of override key names to override
            string values.  Contains only the *delta* keys; keys not present
            here fall back to the profile's defaults.
    """

    profile: str | None
    custom_values: Mapping[str, str]


@dataclass(frozen=True)
class RunPlanTestData:
    """Test-data values carried in an exported or imported run plan.

    Only stores values that are effective deltas from the suite baseline.
    Same-as-baseline values are excluded at compile time and are not stored here.

    Attributes:
        values: Immutable mapping of key names to custom string values.
    """

    values: Mapping[str, str]


def _empty_run_plan_test_data() -> RunPlanTestData:
    """Return the canonical empty ``RunPlanTestData`` value.

    Returns:
        Empty immutable run-plan test-data wrapper.
    """
    return RunPlanTestData(values=MappingProxyType({}))


@dataclass(frozen=True)
class RunPlan:
    """Schema-versioned, participant-owned suite execution contract.

    A :class:`RunPlan` is the foundational artifact that binds together suite
    coordinates, a manifest content hash for drift detection, the ordered subset
    of step ids to execute, and the participant's test-value choices.

    The dataclass is intentionally immutable (``frozen=True``) to ensure that
    plans are treated as value objects throughout the pipeline.

    Attributes:
        schema_version: Wire format version discriminator; currently always
            ``"1"``.
        suite: Suite identity coordinates including the manifest hash.
        selected_step_ids: Ordered tuple of step id strings the participant
            has chosen to run.  Stale ids (not present in the current manifest)
            are reported as inline blockers by the engine, not silently dropped.
        test_values: Participant test-value choices (profile + custom deltas).
        test_data: Participant test-data deltas relative to the suite baseline.
    """

    schema_version: RunPlanSchemaVersion
    suite: RunPlanSuiteCoordinates
    selected_step_ids: tuple[str, ...]
    test_values: RunPlanTestValues
    test_data: RunPlanTestData = field(default_factory=_empty_run_plan_test_data)


# ---------------------------------------------------------------------------
# Exception
# ---------------------------------------------------------------------------


class RunPlanParseError(ValueError):
    """Raised when a raw JSON value cannot be parsed into a :class:`RunPlan`.

    Wraps ``ValueError`` so callers can catch either the specific error or the
    generic base class depending on how much granularity they need.
    """


# ---------------------------------------------------------------------------
# Public functions
# ---------------------------------------------------------------------------


def compute_manifest_hash(manifest_bytes: bytes) -> str:
    """Compute the canonical SHA-256 hash of raw manifest bytes.

    Returns a string in the format ``"sha256:<hex>"`` where ``<hex>`` is the
    lowercase hexadecimal representation of the digest.  This format is used
    in :attr:`RunPlanSuiteCoordinates.manifest_hash` and is compared against
    the hash of the live manifest bytes when the engine decides whether a saved
    plan is still valid.

    Args:
        manifest_bytes: The raw bytes of the manifest document to hash.

    Returns:
        A string of the form ``"sha256:<64-hex-chars>"``.
    """
    digest = hashlib.sha256(manifest_bytes).hexdigest()
    return f"sha256:{digest}"


def parse_run_plan(raw: JsonValue) -> RunPlan:
    """Parse and validate a raw JSON value into a :class:`RunPlan`.

    Validates the full document structure, including the schema version
    discriminator, required fields, and type constraints.  Raises
    :class:`RunPlanParseError` with a human-readable message for every
    validation failure so that API error responses can surface actionable
    detail to the participant.

    Parsing rules:
    - ``schemaVersion`` must be exactly the string ``"1"``.
    - ``suite.id``, ``suite.version``, and ``suite.manifestHash`` must all be
      non-empty strings.
    - ``selectedStepIds`` must be a list; each element must be a non-empty
      string.
    - ``testValues`` is optional; when absent it defaults to no profile and no
      custom values.
    - ``testValues.profile`` must be a non-empty string when present; the JSON
      ``null`` value decodes to ``None`` (no profile override).
    - ``testValues.customValues`` must be an object whose values are all
      strings (including empty string).

    Args:
        raw: The parsed JSON value to validate.  Typically the result of
            ``json.loads()``.

    Returns:
        A fully validated :class:`RunPlan` instance.

    Raises:
        RunPlanParseError: If ``raw`` is not a JSON object, if any required
            field is missing or has the wrong type, or if ``schemaVersion``
            is not ``"1"``.
    """
    if not isinstance(raw, dict):
        raise RunPlanParseError("RunPlan must be a JSON object")

    schema_version = _require_string(raw, "schemaVersion")
    if schema_version != "1":
        raise RunPlanParseError(f'Unsupported schemaVersion {schema_version!r}; expected "1"')

    suite = _parse_suite_coordinates(raw)
    selected_step_ids = _parse_selected_step_ids(raw)
    test_values = _parse_test_values(raw)
    test_data = _parse_test_data(raw)

    return RunPlan(
        schema_version="1",
        suite=suite,
        selected_step_ids=selected_step_ids,
        test_values=test_values,
        test_data=test_data,
    )


def serialise_run_plan(plan: RunPlan) -> dict[str, JsonValue]:
    """Serialise a :class:`RunPlan` to a camelCase JSON-compatible dictionary.

    The output uses camelCase keys to match the wire format expected by the
    REST API and the plan-builder UI.  The result can be passed directly to
    ``json.dumps()``.

    Args:
        plan: The :class:`RunPlan` to serialise.

    Returns:
        A ``dict[str, JsonValue]`` ready for JSON serialisation.
    """
    result: dict[str, JsonValue] = {
        "schemaVersion": plan.schema_version,
        "suite": {
            "id": plan.suite.id,
            "version": plan.suite.version,
            "manifestHash": plan.suite.manifest_hash,
        },
        "selectedStepIds": list(plan.selected_step_ids),
    }
    if plan.test_values.profile is not None or plan.test_values.custom_values:
        custom_values: dict[str, JsonValue] = dict(plan.test_values.custom_values)
        test_values: dict[str, JsonValue] = {"customValues": custom_values}
        if plan.test_values.profile is not None:
            test_values["profile"] = plan.test_values.profile
        result["testValues"] = test_values
    if plan.test_data.values:
        result["testData"] = {"values": dict(plan.test_data.values)}
    return result


def run_plan_to_test_values_config(plan: RunPlan) -> TestValuesConfig | None:
    """Convert a :class:`RunPlan`'s test-value section to a :class:`TestValuesConfig`.

    Adapter for backward-compatibility with the executor runtime path, which
    consumes a :class:`~conformance.model_bank_config.TestValuesConfig` rather
    than the new :class:`RunPlan` domain object.

    Returns ``None`` when the plan specifies neither a profile nor any custom
    values, signalling to the executor that it should use the manifest's
    declared defaults with no participant overrides.

    Args:
        plan: The :class:`RunPlan` whose ``testValues`` section should be
            converted.

    Returns:
        A :class:`~conformance.model_bank_config.TestValuesConfig` when the
        plan carries a profile selection or custom-value overrides, or ``None``
        when neither is present.
    """
    tv = plan.test_values
    if tv.profile is None and not tv.custom_values:
        return None
    return TestValuesConfig(
        profile=tv.profile,
        overrides=MappingProxyType(dict(tv.custom_values)),
    )


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
        RunPlanParseError: If the key is absent or its value is not a
            non-empty string.
    """
    value = obj.get(key)
    if not isinstance(value, str):
        raise RunPlanParseError(
            f"Missing or invalid field {key!r}: expected a non-empty string, got {type(value).__name__!r}"
        )
    if not value:
        raise RunPlanParseError(f"Field {key!r} must not be an empty string")
    return value


def _parse_suite_coordinates(doc: dict[str, JsonValue]) -> RunPlanSuiteCoordinates:
    """Parse the ``suite`` object from a raw RunPlan document.

    Args:
        doc: The top-level RunPlan JSON object.

    Returns:
        A validated :class:`RunPlanSuiteCoordinates` instance.

    Raises:
        RunPlanParseError: If ``suite`` is absent, not an object, or any of
            its required string fields are missing or empty.
    """
    raw_suite = doc.get("suite")
    if not isinstance(raw_suite, dict):
        raise RunPlanParseError("Missing or invalid field 'suite': expected a JSON object")
    suite_id = _require_string(raw_suite, "id")
    suite_version = _require_string(raw_suite, "version")
    manifest_hash = _require_string(raw_suite, "manifestHash")
    return RunPlanSuiteCoordinates(
        id=suite_id,
        version=suite_version,
        manifest_hash=manifest_hash,
    )


def _parse_selected_step_ids(doc: dict[str, JsonValue]) -> tuple[str, ...]:
    """Parse the ``selectedStepIds`` array from a raw RunPlan document.

    Args:
        doc: The top-level RunPlan JSON object.

    Returns:
        A tuple of non-empty step-id strings in the order they were listed.

    Raises:
        RunPlanParseError: If ``selectedStepIds`` is absent, not a list, or
            contains any value that is not a non-empty string.
    """
    raw_ids = doc.get("selectedStepIds")
    if not isinstance(raw_ids, list):
        raise RunPlanParseError("Missing or invalid field 'selectedStepIds': expected a JSON array")
    step_ids: list[str] = []
    for idx, item in enumerate(raw_ids):
        if not isinstance(item, str):
            raise RunPlanParseError(f"selectedStepIds[{idx}] must be a string, got {type(item).__name__!r}")
        if not item:
            raise RunPlanParseError(f"selectedStepIds[{idx}] must not be an empty string")
        step_ids.append(item)
    return tuple(step_ids)


def _parse_test_values(doc: dict[str, JsonValue]) -> RunPlanTestValues:
    """Parse the optional ``testValues`` object from a raw RunPlan document.

    When ``testValues`` is absent from the document the result is a
    :class:`RunPlanTestValues` with no profile and no custom values.

    Args:
        doc: The top-level RunPlan JSON object.

    Returns:
        A :class:`RunPlanTestValues` instance, using defaults when the key is
        absent from ``doc``.

    Raises:
        RunPlanParseError: If ``testValues`` is present but not a JSON object,
            if ``profile`` is present but not a non-empty string, or if
            ``customValues`` is present but not an object whose values are all
            strings.
    """
    raw_tv = doc.get("testValues")
    if raw_tv is None:
        return RunPlanTestValues(profile=None, custom_values=MappingProxyType({}))
    if not isinstance(raw_tv, dict):
        raise RunPlanParseError("Field 'testValues' must be a JSON object when present")

    profile = _parse_test_values_profile(raw_tv)
    custom_values = _parse_custom_values(raw_tv)
    return RunPlanTestValues(profile=profile, custom_values=custom_values)


def _parse_test_data(doc: dict[str, JsonValue]) -> RunPlanTestData:
    """Parse the optional ``testData`` object from a raw RunPlan document.

    Args:
        doc: The top-level RunPlan JSON object.

    Returns:
        A :class:`RunPlanTestData` instance, using an empty immutable mapping
        when ``testData`` is absent.

    Raises:
        RunPlanParseError: If ``testData`` is present but not a JSON object, or
            if ``values`` is present but not an object whose values are all
            strings.
    """
    raw_test_data = doc.get("testData")
    if raw_test_data is None:
        return _empty_run_plan_test_data()
    if not isinstance(raw_test_data, dict):
        raise RunPlanParseError("Field 'testData' must be a JSON object when present")
    raw_values = raw_test_data.get("values")
    if raw_values is None:
        return _empty_run_plan_test_data()
    if not isinstance(raw_values, dict):
        raise RunPlanParseError("testData.values must be a JSON object when present")

    values: dict[str, str] = {}
    for key, value in raw_values.items():
        if not isinstance(value, str):
            raise RunPlanParseError(f"testData.values[{key!r}] must be a string, got {type(value).__name__!r}")
        values[key] = value
    return RunPlanTestData(values=MappingProxyType(values))


def _parse_test_values_profile(tv: dict[str, JsonValue]) -> str | None:
    """Parse the optional ``profile`` field inside a ``testValues`` object.

    ``null`` (JSON) and absent are both treated as "no profile override" and
    return ``None``.  An explicit non-empty string is returned as-is.

    Args:
        tv: The ``testValues`` JSON object.

    Returns:
        The profile string, or ``None`` if absent or ``null``.

    Raises:
        RunPlanParseError: If ``profile`` is present but is not a string or is
            an empty string.
    """
    if "profile" not in tv:
        return None
    raw_profile = tv["profile"]
    if raw_profile is None:
        return None
    if not isinstance(raw_profile, str):
        raise RunPlanParseError(f"testValues.profile must be a string or null, got {type(raw_profile).__name__!r}")
    if not raw_profile:
        raise RunPlanParseError("testValues.profile must not be an empty string")
    return raw_profile


def _parse_custom_values(tv: dict[str, JsonValue]) -> Mapping[str, str]:
    """Parse the optional ``customValues`` object inside a ``testValues`` object.

    Empty-string values are valid — they represent an explicit participant
    choice to supply an empty override, distinct from the key being absent.

    Args:
        tv: The ``testValues`` JSON object.

    Returns:
        An immutable mapping of override key names to string values.  Returns
        an empty mapping when ``customValues`` is absent.

    Raises:
        RunPlanParseError: If ``customValues`` is present but not a JSON
            object, or if any value in the object is not a string.
    """
    raw_cv = tv.get("customValues")
    if raw_cv is None:
        return MappingProxyType({})
    if not isinstance(raw_cv, dict):
        raise RunPlanParseError("testValues.customValues must be a JSON object when present")
    overrides: dict[str, str] = {}
    for k, v in raw_cv.items():
        if not isinstance(v, str):
            raise RunPlanParseError(f"testValues.customValues[{k!r}] must be a string, got {type(v).__name__!r}")
        overrides[k] = v
    return MappingProxyType(overrides)
