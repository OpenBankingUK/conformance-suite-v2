"""Session-backed draft storage for the browser test-plan wizard."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Literal, cast, get_args
from uuid import uuid4

from django.contrib.sessions.backends.base import SessionBase

from conformance.catalogue import PlanExecutionMode, SecurityProfile
from conformance.json_types import JsonObject, JsonValue
from conformance.specification_registry import (
    derived_security_profile_for_boundary,
    latest_openapi_document_update,
    openapi_document_update_for_boundary,
    specification_for_boundary,
)

_LOGGER = logging.getLogger(__name__)
"""Logger for malformed browser wizard draft state."""

_SESSION_KEY = "conformance_builder_drafts"
"""Django session key containing browser wizard draft records."""

_MAX_SESSION_DRAFTS = 5
"""Maximum active browser wizard drafts retained per Django session."""

_DEFAULT_SECURITY_PROFILE: SecurityProfile = "fapi1-advanced"
"""Placeholder profile used only before a specification boundary is selected."""

type PlanImportIssueKind = Literal["missing", "invalid", "unknown", "skipped", "notice"]
"""Category of a field-level problem found while loading plan JSON into a draft."""

_PLAN_IMPORT_ISSUE_KINDS: frozenset[str] = frozenset(get_args(PlanImportIssueKind.__value__))
"""Accepted import-issue kinds when decoding session state."""

type BuilderStepId = Literal["catalogue", "scope", "discovery", "security", "config"]
"""Guided builder wizard step that the participant saves (review is not a saved step)."""

BUILDER_STEP_IDS: tuple[BuilderStepId, ...] = get_args(BuilderStepId.__value__)
"""Every saved builder step id, in declaration order."""


@dataclass(frozen=True)
class PlanImportIssue:
    """One field-level problem found while loading test-plan JSON into a draft.

    Attributes:
        path: Human-readable JSON path, for example
            ``securityEnvironment.discoveryUrl`` or ``resourceGroups[1]``.
        ref: Object-key path used to match the issue against the draft's
            unrepresented plan fields; empty for whole-plan notices.
        kind: ``missing`` (required field absent), ``invalid`` (value kept but
            fails validation), ``unknown`` (key not in the canonical schema),
            ``skipped`` (value kept but could not be loaded into the builder),
            or ``notice`` (whole-plan information).
        message: Participant-facing explanation of the problem.
    """

    path: str
    ref: tuple[str, ...]
    kind: PlanImportIssueKind
    message: str

    def to_session_object(self) -> JsonObject:
        """Serialise this issue into a Django-session-safe JSON object.

        Returns:
            JSON object stored with the draft.
        """
        return {"path": self.path, "ref": list(self.ref), "kind": self.kind, "message": self.message}

    @classmethod
    def from_session_object(cls, raw_value: object) -> PlanImportIssue | None:
        """Decode an import issue from session state.

        Args:
            raw_value: Value read from the Django session.

        Returns:
            Parsed issue, or ``None`` when the value is malformed.
        """
        if not isinstance(raw_value, dict):
            return None
        path = raw_value.get("path")
        kind = raw_value.get("kind")
        message = raw_value.get("message")
        if not (isinstance(path, str) and isinstance(message, str) and kind in _PLAN_IMPORT_ISSUE_KINDS):
            return None
        return cls(
            path=path,
            ref=_string_tuple(raw_value.get("ref")),
            kind=cast(PlanImportIssueKind, kind),
            message=message,
        )


@dataclass(frozen=True)
class BuilderDraft:
    """Partially completed browser test-plan builder draft.

    Attributes:
        draft_id: Opaque identifier for this draft inside the browser session.
        scheme: Selected standards scheme, or ``None`` before step one is
            saved.
        specification: Selected standards specification family, or ``None``
            before step one is saved.
        version: Selected specification version, or ``None`` before step one
            is saved.
        security_profile: Security profile carried into the eventual v2 plan
            document.
        resource_group_ids: Selected resource-group ids from the scope step.
        endpoint_ids: Selected endpoint option ids from the scope step.
        endpoint_capability_ids: Selected optional capability ids keyed by
            endpoint option id.
        config: Draft executable config object retained for review,
            import/export, and launch.
        security_environment: Canonical security environment metadata preserved
            from imported JSON-first plans when it is not represented directly
            by executable config fields.
        business_test_data: Canonical business test data preserved from imported
            JSON-first plans when it is not represented directly by executable
            config fields.
        dynamic_client_registration: Canonical DCR-only configuration preserved
            across guided editing and import/export.
        metadata: Optional participant/export metadata retained with the plan.
        execution_mode: Canonical execution mode retained with the plan.
        discovery_metadata: Session-only non-secret discovery helper state used
            to prefill later config steps without exporting raw metadata.
        created_at: UTC ISO timestamp for draft creation.
        updated_at: UTC ISO timestamp for the latest draft write.
        openapi_document_update: Selected Read/Write OpenAPI document update
            (for example ``"Update-1"``), or ``None`` for specifications that
            do not publish selectable updates or before step one is saved.
        unrepresented_plan_fields: Canonical-shaped test-plan JSON overlay for
            values the builder cannot represent (invalid values, unknown keys,
            scope that could not be resolved). It is merged over the
            builder-generated plan JSON so loaded values are never silently
            replaced; normal launch validation fails until they are fixed.
        import_issues: Field-level problems reported by the latest plan JSON
            load, shown as review warnings.
        invalid_field_values: Raw, non-secret values typed into a builder
            step that failed format validation, keyed by step id then form
            field name. They are kept so leaving a page never loses input,
            shown again with their errors on that page, and listed on review.
    """

    draft_id: str
    scheme: str | None
    specification: str | None
    version: str | None
    security_profile: SecurityProfile
    resource_group_ids: tuple[str, ...]
    endpoint_ids: tuple[str, ...]
    endpoint_capability_ids: Mapping[str, tuple[str, ...]]
    config: Mapping[str, JsonValue]
    security_environment: Mapping[str, JsonValue]
    business_test_data: Mapping[str, JsonValue]
    dynamic_client_registration: Mapping[str, JsonValue]
    metadata: Mapping[str, JsonValue]
    execution_mode: PlanExecutionMode
    discovery_metadata: Mapping[str, JsonValue]
    created_at: str
    updated_at: str
    unrepresented_plan_fields: Mapping[str, JsonValue] = field(default_factory=dict)
    import_issues: tuple[PlanImportIssue, ...] = ()
    openapi_document_update: str | None = None
    invalid_field_values: Mapping[str, Mapping[str, str]] = field(default_factory=dict)

    @classmethod
    def create(cls) -> BuilderDraft:
        """Create a blank wizard draft with a fresh session-scoped id.

        Returns:
            New draft ready to be persisted.
        """
        timestamp = _utc_timestamp()
        return cls(
            draft_id=uuid4().hex,
            scheme=None,
            specification=None,
            version=None,
            security_profile=_DEFAULT_SECURITY_PROFILE,
            resource_group_ids=(),
            endpoint_ids=(),
            endpoint_capability_ids={},
            config={},
            security_environment={},
            business_test_data={},
            dynamic_client_registration={},
            metadata={},
            execution_mode="certification",
            discovery_metadata={},
            created_at=timestamp,
            updated_at=timestamp,
        )

    @classmethod
    def from_session_object(cls, raw_value: object) -> BuilderDraft | None:
        """Decode a draft from a Django session value.

        Args:
            raw_value: Value read from the Django session.

        Returns:
            Parsed draft, or ``None`` when the session value is malformed.
        """
        if not isinstance(raw_value, dict):
            return None
        draft_id = raw_value.get("draftId")
        security_profile = raw_value.get("securityProfile")
        created_at = raw_value.get("createdAt")
        updated_at = raw_value.get("updatedAt")
        if not (
            isinstance(draft_id, str)
            and isinstance(security_profile, str)
            and security_profile in {"fapi1-advanced", "fapi2", "all"}
            and isinstance(created_at, str)
            and isinstance(updated_at, str)
        ):
            return None
        return cls(
            draft_id=draft_id,
            scheme=_optional_string(raw_value.get("scheme")),
            specification=_optional_string(raw_value.get("specification")),
            version=_optional_string(raw_value.get("version")),
            security_profile=cast(SecurityProfile, security_profile),
            resource_group_ids=_string_tuple(raw_value.get("resourceGroupIds")),
            endpoint_ids=_string_tuple(raw_value.get("endpointIds")),
            endpoint_capability_ids=_string_tuple_mapping(raw_value.get("endpointCapabilityIds")),
            config=_config_object(raw_value.get("config")),
            security_environment=_json_object(raw_value.get("securityEnvironment")),
            business_test_data=_json_object(raw_value.get("businessTestData")),
            dynamic_client_registration=_json_object(raw_value.get("dynamicClientRegistration")),
            metadata=_json_object(raw_value.get("metadata")),
            execution_mode=_execution_mode(raw_value.get("executionMode")),
            discovery_metadata=_json_object(raw_value.get("discoveryMetadata")),
            created_at=created_at,
            updated_at=updated_at,
            openapi_document_update=_optional_string(raw_value.get("openApiDocumentUpdate")),
            unrepresented_plan_fields=_json_object(raw_value.get("unrepresentedPlanFields")),
            import_issues=_import_issues(raw_value.get("importIssues")),
            invalid_field_values=_invalid_field_values(raw_value.get("invalidFieldValues")),
        )

    def with_catalogue_boundary(
        self,
        *,
        scheme: str,
        specification: str,
        version: str,
        openapi_document_update: str | None = None,
    ) -> BuilderDraft:
        """Return a copy with the specification boundary and derived profile saved.

        Args:
            scheme: Selected standards scheme.
            specification: Selected standards specification family.
            version: Selected specification version.
            openapi_document_update: Selected OpenAPI document update. When
                omitted, the latest published update for the version is used.

        Returns:
            Updated draft with a refreshed ``updated_at`` timestamp.

        Raises:
            ValueError: If the boundary is unsupported or does not declare
                exactly one security profile.
        """
        security_profile = derived_security_profile_for_boundary(scheme, specification, version)
        _definition, version_definition = specification_for_boundary(scheme, specification, version)
        if openapi_document_update is None:
            latest = latest_openapi_document_update(version_definition)
            openapi_document_update = latest.update if latest is not None else None
        selected_update = openapi_document_update_for_boundary(scheme, specification, version, openapi_document_update)
        return replace(
            self,
            scheme=scheme,
            specification=specification,
            version=version,
            security_profile=security_profile,
            updated_at=_utc_timestamp(),
            openapi_document_update=selected_update.update if selected_update is not None else None,
        )

    def with_scope_selection(
        self,
        *,
        resource_group_ids: tuple[str, ...],
        endpoint_ids: tuple[str, ...],
        endpoint_capability_ids: Mapping[str, tuple[str, ...]],
    ) -> BuilderDraft:
        """Return a copy with the resource/endpoints/features step saved.

        Args:
            resource_group_ids: Selected resource-group ids in submitted order.
            endpoint_ids: Selected endpoint option ids in submitted order.
            endpoint_capability_ids: Selected optional capability ids keyed by
                endpoint option id.

        Returns:
            Updated draft with a refreshed ``updated_at`` timestamp.
        """
        return replace(
            self,
            resource_group_ids=resource_group_ids,
            endpoint_ids=endpoint_ids,
            endpoint_capability_ids={
                endpoint_id: tuple(capability_ids) for endpoint_id, capability_ids in endpoint_capability_ids.items()
            },
            updated_at=_utc_timestamp(),
        )

    def with_config(self, *, config: Mapping[str, JsonValue]) -> BuilderDraft:
        """Return a copy with grouped execution config saved.

        Args:
            config: Draft executable config object built from the grouped
                configuration step or imported plan JSON.

        Returns:
            Updated draft with a refreshed ``updated_at`` timestamp.
        """
        return replace(
            self,
            config=_config_object(config),
            updated_at=_utc_timestamp(),
        )

    def with_plan_context(
        self,
        *,
        security_environment: Mapping[str, JsonValue],
        business_test_data: Mapping[str, JsonValue],
        metadata: Mapping[str, JsonValue],
        execution_mode: PlanExecutionMode,
        dynamic_client_registration: Mapping[str, JsonValue] | None = None,
    ) -> BuilderDraft:
        """Return a copy with canonical plan-only context saved.

        Args:
            security_environment: Canonical security environment metadata from
                an imported plan.
            business_test_data: Canonical business test data from an imported
                plan.
            metadata: Optional participant/export metadata from an imported plan.
            execution_mode: Canonical execution mode from an imported plan.
            dynamic_client_registration: Optional canonical DCR-only
                configuration. When omitted, the existing value is preserved.

        Returns:
            Updated draft with a refreshed ``updated_at`` timestamp.
        """
        return replace(
            self,
            security_environment=_json_object(security_environment),
            business_test_data=_json_object(business_test_data),
            dynamic_client_registration=(
                self.dynamic_client_registration
                if dynamic_client_registration is None
                else _json_object(dynamic_client_registration)
            ),
            metadata=_json_object(metadata),
            execution_mode=execution_mode,
            updated_at=_utc_timestamp(),
        )

    def with_discovery_metadata(self, *, discovery_metadata: Mapping[str, JsonValue]) -> BuilderDraft:
        """Return a copy with session-only discovery metadata saved.

        Args:
            discovery_metadata: Non-secret discovery metadata used to prefill
                later guided config fields.

        Returns:
            Updated draft with a refreshed ``updated_at`` timestamp.
        """
        return replace(
            self,
            discovery_metadata=_json_object(discovery_metadata),
            updated_at=_utc_timestamp(),
        )

    def with_unrepresented_plan_fields(self, *, unrepresented_plan_fields: Mapping[str, JsonValue]) -> BuilderDraft:
        """Return a copy with the unrepresented plan-JSON overlay replaced.

        Args:
            unrepresented_plan_fields: Canonical-shaped JSON overlay of values
                the builder cannot represent.

        Returns:
            Updated draft with a refreshed ``updated_at`` timestamp.
        """
        return replace(
            self,
            unrepresented_plan_fields=_json_object(unrepresented_plan_fields),
            updated_at=_utc_timestamp(),
        )

    def with_import_issues(self, *, import_issues: tuple[PlanImportIssue, ...]) -> BuilderDraft:
        """Return a copy with plan-JSON import issues replaced.

        Args:
            import_issues: Field-level problems to show on review.

        Returns:
            Updated draft with a refreshed ``updated_at`` timestamp.
        """
        return replace(self, import_issues=tuple(import_issues), updated_at=_utc_timestamp())

    def with_invalid_field_values(self, step: BuilderStepId, values: Mapping[str, str]) -> BuilderDraft:
        """Return a copy with one step's retained invalid field values replaced.

        Args:
            step: Builder step whose values are replaced.
            values: Raw typed values that failed format validation, keyed by
                form field name; empty clears the step.

        Returns:
            Updated draft with a refreshed ``updated_at`` timestamp.
        """
        updated = {key: dict(value) for key, value in self.invalid_field_values.items() if key != step}
        if values:
            updated[step] = dict(values)
        return replace(self, invalid_field_values=updated, updated_at=_utc_timestamp())

    def to_session_object(self) -> JsonObject:
        """Serialise this draft into a Django-session-safe JSON object.

        Returns:
            JSON object stored under the browser session draft key.
        """
        return {
            "draftId": self.draft_id,
            "scheme": self.scheme,
            "specification": self.specification,
            "version": self.version,
            "securityProfile": self.security_profile,
            "resourceGroupIds": list(self.resource_group_ids),
            "endpointIds": list(self.endpoint_ids),
            "endpointCapabilityIds": {
                endpoint_id: list(capability_ids)
                for endpoint_id, capability_ids in self.endpoint_capability_ids.items()
            },
            "config": _config_object(self.config),
            "securityEnvironment": _json_object(self.security_environment),
            "businessTestData": _json_object(self.business_test_data),
            "dynamicClientRegistration": _json_object(self.dynamic_client_registration),
            "metadata": _json_object(self.metadata),
            "executionMode": self.execution_mode,
            "discoveryMetadata": _json_object(self.discovery_metadata),
            "openApiDocumentUpdate": self.openapi_document_update,
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
            "unrepresentedPlanFields": _json_object(self.unrepresented_plan_fields),
            "importIssues": [issue.to_session_object() for issue in self.import_issues],
            "invalidFieldValues": {step: dict(values) for step, values in self.invalid_field_values.items()},
        }


class SessionBuilderDraftStore:
    """Builder-draft store implementation backed by a Django session."""

    def __init__(self, session: SessionBase) -> None:
        """Initialise the session-backed store.

        Args:
            session: Django session object for the current browser user.
        """
        self.session = session

    def create(self) -> BuilderDraft:
        """Create and persist a blank builder draft.

        Returns:
            The newly persisted draft.
        """
        draft = BuilderDraft.create()
        self.save(draft)
        return draft

    def get(self, draft_id: str) -> BuilderDraft | None:
        """Return a draft by id from the current browser session.

        Args:
            draft_id: Opaque draft id from the route.

        Returns:
            Matching draft, or ``None`` when the id is unknown.
        """
        return self._drafts().get(draft_id)

    def save(self, draft: BuilderDraft) -> None:
        """Persist a draft and prune older session drafts.

        Args:
            draft: Draft to persist into the current Django session.
        """
        drafts = self._drafts()
        drafts[draft.draft_id] = draft
        sorted_drafts = sorted(drafts.values(), key=lambda candidate: candidate.updated_at, reverse=True)
        limited_drafts = sorted_drafts[:_MAX_SESSION_DRAFTS]
        self.session[_SESSION_KEY] = {
            "drafts": {candidate.draft_id: candidate.to_session_object() for candidate in limited_drafts}
        }
        self.session.modified = True

    def _drafts(self) -> dict[str, BuilderDraft]:
        """Load all valid draft records from the session.

        Returns:
            Mapping of draft id to parsed draft records.
        """
        raw_state = self.session.get(_SESSION_KEY)
        if raw_state is None:
            return {}
        if not isinstance(raw_state, dict):
            _LOGGER.warning("Ignoring malformed builder draft session state")
            return {}
        raw_drafts = raw_state.get("drafts")
        if not isinstance(raw_drafts, dict):
            _LOGGER.warning("Ignoring malformed builder draft list in session state")
            return {}
        drafts: dict[str, BuilderDraft] = {}
        for draft_id, raw_draft in raw_drafts.items():
            draft = BuilderDraft.from_session_object(raw_draft)
            if isinstance(draft_id, str) and draft is not None and draft.draft_id == draft_id:
                drafts[draft_id] = draft
            else:
                _LOGGER.warning("Ignoring malformed builder draft record in session state")
        return drafts


def _optional_string(value: object) -> str | None:
    """Return an optional non-empty session string.

    Args:
        value: Raw value decoded from the Django session.

    Returns:
        String value, or ``None`` when absent or empty.
    """
    if not isinstance(value, str) or value == "":
        return None
    return value


def _string_tuple(value: object) -> tuple[str, ...]:
    """Return a tuple of strings decoded from session JSON.

    Args:
        value: Raw value decoded from the Django session.

    Returns:
        Tuple of string values, or an empty tuple when malformed.
    """
    if not isinstance(value, list):
        return ()
    values: list[str] = []
    for item in value:
        if not isinstance(item, str):
            return ()
        values.append(item)
    return tuple(values)


def _invalid_field_values(value: object) -> dict[str, dict[str, str]]:
    """Decode retained invalid field values from session state.

    Args:
        value: Session value.

    Returns:
        Mapping of known step id to field name to raw string; malformed
        entries are dropped.
    """
    if not isinstance(value, dict):
        return {}
    decoded: dict[str, dict[str, str]] = {}
    for step, fields in value.items():
        if step not in BUILDER_STEP_IDS or not isinstance(fields, dict):
            continue
        strings = {name: raw for name, raw in fields.items() if isinstance(name, str) and isinstance(raw, str)}
        if strings:
            decoded[step] = strings
    return decoded


def _string_tuple_mapping(value: object) -> dict[str, tuple[str, ...]]:
    """Return a mapping of string tuples decoded from session JSON.

    Args:
        value: Raw value decoded from the Django session.

    Returns:
        Mapping of endpoint id to capability id tuple, or an empty mapping when
        malformed.
    """
    if not isinstance(value, dict):
        return {}
    decoded: dict[str, tuple[str, ...]] = {}
    for key, raw_items in value.items():
        if not isinstance(key, str):
            return {}
        items = _string_tuple(raw_items)
        if raw_items is not None and not items and raw_items != []:
            return {}
        decoded[key] = items
    return decoded


def _json_object(value: object) -> JsonObject:
    """Return a JSON-object copy decoded from session state.

    Args:
        value: Raw value decoded from the Django session.

    Returns:
        Deep-copied JSON object, or an empty object when malformed.
    """
    if not isinstance(value, Mapping):
        return {}
    return {key: _copy_json_value(item) for key, item in value.items() if isinstance(key, str) and _is_json_value(item)}


def _import_issues(value: object) -> tuple[PlanImportIssue, ...]:
    """Return import issues decoded from session JSON.

    Args:
        value: Raw value decoded from the Django session.

    Returns:
        Parsed issues; malformed entries are dropped.
    """
    if not isinstance(value, list):
        return ()
    return tuple(issue for issue in map(PlanImportIssue.from_session_object, value) if issue is not None)


def _config_object(value: object) -> JsonObject:
    """Return a draft config object with legacy metadata removed.

    Args:
        value: Raw config value decoded from session state or supplied by a
            builder form.

    Returns:
        JSON config object without the obsolete ``environment`` metadata key.
    """
    config = _json_object(value)
    config.pop("environment", None)
    return config


def _execution_mode(value: object) -> PlanExecutionMode:
    """Return a persisted execution mode or the default certification mode.

    Args:
        value: Raw value decoded from the Django session.

    Returns:
        Canonical execution mode.
    """
    return "development" if value == "development" else "certification"


def _is_json_value(value: object) -> bool:
    """Return whether ``value`` can be stored as JSON in a session draft.

    Args:
        value: Value to inspect.

    Returns:
        True when the value is a supported JSON scalar, list, or object.
    """
    if value is None or isinstance(value, str | int | float | bool):
        return True
    if isinstance(value, list):
        return all(_is_json_value(item) for item in value)
    if isinstance(value, dict):
        return all(isinstance(key, str) and _is_json_value(item) for key, item in value.items())
    return False


def _copy_json_value(value: JsonValue) -> JsonValue:
    """Return a deep copy of a JSON value.

    Args:
        value: JSON value to copy.

    Returns:
        Independent JSON value.
    """
    if isinstance(value, dict):
        return {key: _copy_json_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_copy_json_value(item) for item in value]
    return value


def _utc_timestamp() -> str:
    """Return the current UTC time as an ISO 8601 string.

    Returns:
        UTC timestamp suitable for lexicographic ordering.
    """
    return datetime.now(UTC).isoformat()
