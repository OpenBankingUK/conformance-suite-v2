"""Lenient loading of test-plan JSON into editable browser builder drafts.

Browser import treats a canonical Open Banking UK JSON-first test plan
(``schemaVersion`` ``1.0``) as an editable draft rather than an accepted plan.
Each section and field is recovered independently: values the builder can
represent are loaded into the draft, and everything else (invalid values,
unknown keys, scope that cannot be resolved against the selected
specification) is preserved verbatim in the draft's unrepresented-field overlay
with a field-level :class:`PlanImportIssue`. Nothing is silently replaced by a
default, so the composed plan JSON keeps failing normal test-plan validation,
and launch stays blocked, until the participant fixes it in the builder or the
review JSON editor.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import cast

from django import forms
from django.core.files.uploadedfile import UploadedFile

from conformance.api.builder_draft_store import (
    BUILDER_STEP_IDS,
    BuilderDraft,
    BuilderStepId,
    PlanImportIssue,
    PlanImportIssueKind,
)
from conformance.api.builder_wizard import (
    builder_plan_json_from_draft_or_skeleton,
    draft_scope_from_plan_document,
    plan_document_from_draft,
)
from conformance.catalogue import (
    CatalogueError,
    PlanDocumentV2,
    PlanExecutionMode,
    canonical_plan_config,
    parse_canonical_specification,
    parse_test_plan_document,
    validate_canonical_security_environment,
)
from conformance.json_types import JsonObject, JsonValue
from conformance.specification_registry import (
    latest_openapi_document_update,
    openapi_document_update_for_boundary,
    specification_for_boundary,
)
from conformance.test_plan_validation import CANONICAL_TEST_PLAN_JSON_SCHEMA, json_schema_error

PLAN_IMPORT_MAX_BYTES = 1_048_576
"""Largest uploaded or pasted plan JSON accepted by browser import (1 MiB)."""


class PlanImportError(ValueError):
    """Raised when import text is not a JSON object and cannot be recovered at all.

    Attributes:
        line: 1-based line of a JSON syntax error, for the editor to mark, or ``None``.
        column: 1-based column of that error, or ``None``.
    """

    def __init__(self, message: str, *, line: int | None = None, column: int | None = None) -> None:
        """Store the message and optional syntax error position."""
        super().__init__(message)
        self.line = line
        self.column = column


def _display_error_position(text: str, error: json.JSONDecodeError) -> tuple[int, int]:
    """Return the line and column where a participant should fix a JSON syntax error.

    The parser reports where it noticed the problem. For a missing comma between
    items, or an error at a blank line or end of text, that is the start of the
    next token, while the mistake is at the end of the previous non-blank line.
    """
    line_start = text.rfind("\n", 0, error.pos) + 1
    at_line_start = not text[line_start : error.pos].strip()
    line_end = text.find("\n", error.pos)
    rest_of_line = text[error.pos : len(text) if line_end == -1 else line_end]
    if at_line_start and (error.msg == "Expecting ',' delimiter" or not rest_of_line.strip()):
        previous_lines = text[:line_start].splitlines()
        for index in range(len(previous_lines) - 1, -1, -1):
            if previous_lines[index].strip():
                return index + 1, len(previous_lines[index].rstrip()) + 1
    return error.lineno, error.colno


def parse_plan_import_text(text: str) -> JsonObject:
    """Parse browser import text into a raw JSON object.

    Only input with nothing to recover is rejected: empty text, invalid JSON,
    or a JSON value that is not an object. Everything else is handed to
    :func:`recover_draft_from_plan_json`.

    Args:
        text: Pasted or uploaded plan JSON.

    Returns:
        Raw JSON object.

    Raises:
        PlanImportError: If the text cannot be loaded as a JSON object.
    """
    if not text.strip():
        raise PlanImportError("Paste plan JSON or choose a .json file to import.")
    if len(text.encode("utf-8")) > PLAN_IMPORT_MAX_BYTES:
        raise PlanImportError("Plan JSON must be 1 MB or smaller.")
    try:
        parsed: object = json.loads(text)
    except json.JSONDecodeError as error:
        line, column = _display_error_position(text, error)
        raise PlanImportError(
            f"Plan JSON must be valid JSON: {error.msg} (line {line})", line=line, column=column
        ) from error
    if not isinstance(parsed, dict):
        raise PlanImportError("Plan JSON must be a JSON object.")
    return cast(JsonObject, parsed)


class PlanImportForm(forms.Form):
    """Browser import form accepting pasted plan JSON or an uploaded ``.json`` file.

    An uploaded file takes precedence over pasted text.
    """

    plan_json = forms.CharField(required=False, strip=False, widget=forms.Textarea)
    plan_file = forms.FileField(required=False)

    raw_plan: JsonObject | None = None

    def clean(self) -> dict[str, object]:
        """Parse the submitted plan into :attr:`raw_plan`.

        Returns:
            Cleaned form data.
        """
        cleaned = super().clean() or {}
        upload = cleaned.get("plan_file")
        text = cast(str, cleaned.get("plan_json") or "")
        try:
            if isinstance(upload, UploadedFile):
                text = _uploaded_plan_text(upload)
            self.raw_plan = parse_plan_import_text(text)
        except PlanImportError as error:
            raise forms.ValidationError(str(error)) from error
        return cleaned

    def import_error(self) -> str:
        """Return the first participant-facing import error.

        Returns:
            Error message for the import page.
        """
        errors = [str(message) for messages in self.errors.values() for message in messages]
        return errors[0] if errors else "Plan JSON could not be imported."


def _uploaded_plan_text(upload: UploadedFile) -> str:
    """Read an uploaded plan file as UTF-8 text within the size limit.

    Args:
        upload: Uploaded file.

    Returns:
        Decoded file text.

    Raises:
        PlanImportError: If the file is too large or not UTF-8.
    """
    if upload.size is not None and upload.size > PLAN_IMPORT_MAX_BYTES:
        raise PlanImportError("Plan JSON must be 1 MB or smaller.")
    content = cast(bytes, upload.read(PLAN_IMPORT_MAX_BYTES + 1))
    if len(content) > PLAN_IMPORT_MAX_BYTES:
        raise PlanImportError("Plan JSON must be 1 MB or smaller.")
    try:
        return content.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise PlanImportError("Plan file must be UTF-8 encoded JSON.") from error


type BuilderStep = BuilderStepId
"""Builder wizard step whose save may take ownership of unrepresented fields."""

_TOP_LEVEL_KEYS: frozenset[str] = frozenset(
    {
        "schemaVersion",
        "specification",
        "securityEnvironment",
        "resourceGroups",
        "endpoints",
        "businessTestData",
        "dynamicClientRegistration",
        "metadata",
        "executionMode",
    }
)
"""Top-level keys defined by the canonical schemaVersion 1.0 test plan."""

_SCOPE_KEYS: tuple[str, ...] = ("resourceGroups", "endpoints")
"""Top-level scope sections, resolved only against a selected specification."""

_EXECUTION_MODES: frozenset[str] = frozenset({"certification", "development"})
"""Canonical ``executionMode`` values."""

_SECURITY_ENVIRONMENT_CONFLICTS: tuple[tuple[str, str], ...] = (
    ("signingCertificatePath", "signingCertificatePem"),
    ("signingPrivateKeyPath", "signingPrivateKeyPem"),
)
"""Mutually exclusive file-reference/inline-PEM pairs in ``securityEnvironment``."""

_MTLS_CONFLICTS: tuple[tuple[str, str], ...] = (
    ("certificatePath", "certificatePem"),
    ("privateKeyPath", "privateKeyPem"),
    ("caBundlePath", "caBundlePem"),
)
"""Mutually exclusive file-reference/inline-PEM pairs in ``securityEnvironment.mtls``."""

_DCR_CONFLICTS: tuple[tuple[str, str], ...] = (
    ("softwareStatementAssertionPath", "softwareStatementAssertion"),
    ("signingCertificatePath", "signingCertificatePem"),
)
"""Mutually exclusive pairs in ``dynamicClientRegistration``."""

_OPENAPI_UPDATE_KEY = "openApiDocumentUpdate"
"""Canonical ``specification`` key selecting the Read/Write OpenAPI document update."""

_DISCOVERY_OWNED_KEYS: frozenset[str] = frozenset({"discoveryUrl", "timeoutSeconds", "followUp"})
"""``securityEnvironment`` keys edited by the discovery builder step."""

_DCR_REGISTER_ENDPOINT: JsonObject = {"method": "POST", "path": "/register", "required": True, "locked": True}
"""Mandatory locked Open Banking DCR registration endpoint used to probe other endpoints."""


@dataclass
class _Recovery:
    """Mutable accumulator for one lenient plan-JSON load.

    Attributes:
        overlay: Canonical-shaped JSON of values the builder cannot represent.
        issues: Field-level problems in encounter order.
    """

    overlay: JsonObject = field(default_factory=dict)
    issues: list[PlanImportIssue] = field(default_factory=list)

    def note(self, *, path: str, ref: tuple[str, ...], kind: PlanImportIssueKind, message: str) -> None:
        """Record an issue without preserving a value.

        Args:
            path: Human-readable JSON path.
            ref: Object-key path for matching against the overlay.
            kind: Issue category.
            message: Participant-facing message.
        """
        self.issues.append(PlanImportIssue(path=path, ref=ref, kind=kind, message=message))

    def keep(
        self,
        ref: tuple[str, ...],
        value: JsonValue,
        *,
        kind: PlanImportIssueKind,
        message: str,
        path: str | None = None,
        append: bool = False,
    ) -> None:
        """Preserve a value in the overlay and record why.

        Args:
            ref: Object-key path where the value belongs in the plan JSON.
            value: Original JSON value to preserve.
            kind: Issue category.
            message: Participant-facing message.
            path: Optional display path; defaults to ``ref`` joined by dots.
            append: Append ``value`` to an array at ``ref`` instead of
                replacing it (used for individual scope items).
        """
        parent = self.overlay
        for key in ref[:-1]:
            child = parent.get(key)
            if not isinstance(child, dict):
                child = {}
                parent[key] = child
            parent = child
        if append:
            existing = parent.get(ref[-1])
            items = existing if isinstance(existing, list) else []
            items.append(value)
            parent[ref[-1]] = items
        else:
            parent[ref[-1]] = value
        self.note(path=path or ".".join(ref), ref=ref, kind=kind, message=message)


def recover_draft_from_plan_json(raw_plan: Mapping[str, JsonValue], *, draft: BuilderDraft) -> BuilderDraft:
    """Load as much of a test-plan JSON object as possible into a blank draft.

    Args:
        raw_plan: Decoded JSON object supplied by the participant. Any object is
            accepted, whatever its ``schemaVersion``; only fields matching the
            current canonical shape are loaded.
        draft: Blank draft to populate (new, or reset with an existing id).

    Returns:
        Draft holding recovered builder values, the unrepresented-field overlay,
        and import issues for the review page. Every builder step is marked as
        saved, so the step bar reports missing imported data as needing
        attention rather than not started.
    """
    recovery = _Recovery()
    _recover_schema_version(raw_plan, recovery)
    for key, value in raw_plan.items():
        if key not in _TOP_LEVEL_KEYS:
            recovery.keep((key,), value, kind="unknown", message=f"{key} is not a recognised test-plan field.")

    draft, uses_resource_groups = _recover_specification(raw_plan, draft, recovery)
    family = None if uses_resource_groups is None else ("OBL_READ_WRITE" if uses_resource_groups else "OBL_DCR")

    execution_mode: PlanExecutionMode = "certification"
    execution_mode_recovered = False
    if "executionMode" in raw_plan:
        raw_mode = raw_plan["executionMode"]
        if isinstance(raw_mode, str) and raw_mode in _EXECUTION_MODES:
            execution_mode = cast(PlanExecutionMode, raw_mode)
            execution_mode_recovered = True
        else:
            recovery.keep(
                ("executionMode",),
                raw_mode,
                kind="invalid",
                message="executionMode must be one of: certification, development.",
            )

    security_environment = _recover_security_environment(raw_plan, recovery)
    business_test_data = _recover_business_test_data(raw_plan, recovery, family=family)
    dynamic_client_registration = _recover_dynamic_client_registration(raw_plan, recovery, family=family)
    metadata = _recover_metadata(raw_plan, recovery)

    draft = draft.with_config(
        config=canonical_plan_config(security_environment=security_environment, business_test_data=business_test_data)
    ).with_plan_context(
        security_environment=security_environment,
        business_test_data=business_test_data,
        metadata=metadata,
        execution_mode=execution_mode,
        dynamic_client_registration=dynamic_client_registration,
    )
    draft = _recover_scope(raw_plan, draft, recovery, uses_resource_groups=uses_resource_groups)
    _keep_business_test_data_hidden_by_scope(draft, business_test_data, recovery)

    recovered_anything = (
        draft.specification is not None
        or execution_mode_recovered
        or any((security_environment, business_test_data, dynamic_client_registration, metadata))
    )
    if not recovered_anything:
        recovery.note(
            path="",
            ref=(),
            kind="notice",
            message=(
                "Nothing usable was imported. Build the plan with the guided builder or fix the plan JSON on this page."
            ),
        )
    return (
        draft.with_unrepresented_plan_fields(unrepresented_plan_fields=recovery.overlay)
        .with_import_issues(import_issues=tuple(recovery.issues))
        .with_steps_saved(*BUILDER_STEP_IDS)
    )


def reconcile_draft_after_builder_save(
    previous: BuilderDraft,
    updated: BuilderDraft,
    *,
    step: BuilderStep,
) -> BuilderDraft:
    """Hand unrepresented fields over to the builder after a guided step save.

    A builder step takes ownership of the plan-JSON paths it edits: overlay
    values at those paths are dropped once the builder produces a value there,
    so builder edits are not shadowed by stale imported values. Overlay values
    the builder never edits (for example unknown keys) are kept. Saving the
    specification step for a draft that had none re-attempts loading the
    imported scope against the newly chosen specification.

    Args:
        previous: Draft before the step save.
        updated: Draft after the step save.
        step: Builder step that was saved.

    Returns:
        Draft with the overlay pruned and stale import issues removed.
    """
    if not updated.unrepresented_plan_fields and not updated.import_issues:
        return updated
    overlay = _copy_json_object(updated.unrepresented_plan_fields)
    issues = list(updated.import_issues)
    draft = updated
    if step == "catalogue":
        _drop_overlay_openapi_document_update(overlay)
        previous_boundary = (previous.scheme, previous.specification, previous.version)
        current_boundary = (updated.scheme, updated.specification, updated.version)
        if previous_boundary != current_boundary:
            overlay.pop("specification", None)
            pending_scope = {key: overlay.pop(key) for key in _SCOPE_KEYS if key in overlay}
            issues = [
                issue for issue in issues if issue.ref[:1] not in {("specification",), *((k,) for k in _SCOPE_KEYS)}
            ]
            if previous.specification is None and pending_scope and not updated.resource_group_ids:
                recovery = _Recovery(overlay=overlay)
                draft = _recover_scope(
                    pending_scope,
                    updated,
                    recovery,
                    uses_resource_groups=_draft_uses_resource_groups(updated),
                )
                issues.extend(recovery.issues)
    elif step == "scope":
        for key in _SCOPE_KEYS:
            overlay.pop(key, None)
        _prune_builder_owned(overlay, builder_plan_json_from_draft_or_skeleton(updated), "businessTestData")
    else:
        builder_json = builder_plan_json_from_draft_or_skeleton(updated)
        if step == "discovery":
            _prune_builder_owned(overlay, builder_json, "securityEnvironment", keys=_DISCOVERY_OWNED_KEYS)
        else:
            sections = {
                "security": ("securityEnvironment", "dynamicClientRegistration", "metadata"),
                "config": ("businessTestData",),
            }[step]
            for section in sections:
                _prune_builder_owned(overlay, builder_json, section)
        if step == "security" and _draft_uses_resource_groups(updated) is False:
            overlay.pop("executionMode", None)
    builder_json = builder_plan_json_from_draft_or_skeleton(draft)
    remaining = tuple(issue for issue in issues if _issue_still_applies(issue, overlay, builder_json))
    return draft.with_unrepresented_plan_fields(unrepresented_plan_fields=overlay).with_import_issues(
        import_issues=remaining
    )


def _drop_overlay_openapi_document_update(overlay: JsonObject) -> None:
    """Hand the imported OpenAPI document update over to the specification step.

    Args:
        overlay: Mutable unrepresented-field overlay.
    """
    specification = overlay.get("specification")
    if not isinstance(specification, dict) or _OPENAPI_UPDATE_KEY not in specification:
        return
    del specification[_OPENAPI_UPDATE_KEY]
    if not specification:
        del overlay["specification"]


def _recover_schema_version(raw_plan: Mapping[str, JsonValue], recovery: _Recovery) -> None:
    """Report a missing or unsupported ``schemaVersion``.

    The draft always uses schemaVersion ``1.0``; only fields matching that
    shape are recovered.

    Args:
        raw_plan: Raw imported JSON object.
        recovery: Recovery accumulator.
    """
    schema_version = raw_plan.get("schemaVersion")
    if schema_version == "1.0":
        return
    if "schemaVersion" not in raw_plan:
        message = "schemaVersion was missing. Fields matching schemaVersion 1.0 were loaded; the draft uses 1.0."
    else:
        message = (
            f"schemaVersion {_json_scalar_text(schema_version)} is not supported. Fields matching schemaVersion 1.0 "
            "were loaded; the draft uses 1.0."
        )
    recovery.note(path="schemaVersion", ref=(), kind="notice", message=message)


def _recover_specification(
    raw_plan: Mapping[str, JsonValue],
    draft: BuilderDraft,
    recovery: _Recovery,
) -> tuple[BuilderDraft, bool | None]:
    """Recover the specification boundary.

    Args:
        raw_plan: Raw imported JSON object.
        draft: Draft being populated.
        recovery: Recovery accumulator.

    Returns:
        Draft with the boundary saved when valid, and whether the family uses
        resource groups (``None`` when no boundary was recovered).
    """
    if "specification" not in raw_plan:
        recovery.note(
            path="specification",
            ref=("specification",),
            kind="missing",
            message="specification is missing. Choose a specification in the builder.",
        )
        return draft, None
    raw_specification = raw_plan["specification"]
    if not isinstance(raw_specification, dict):
        recovery.keep(
            ("specification",),
            raw_specification,
            kind="invalid",
            message="specification must be a JSON object. Choose a specification in the builder.",
        )
        return draft, None
    # The OpenAPI document update is recovered on its own so plans written
    # before updates were selectable (or naming an unknown update) still load
    # their specification boundary instead of losing it entirely.
    base_specification = {key: value for key, value in raw_specification.items() if key != _OPENAPI_UPDATE_KEY}
    latest_update = _latest_openapi_document_update(base_specification)
    schema_candidate: JsonValue = (
        {**base_specification, _OPENAPI_UPDATE_KEY: latest_update} if latest_update is not None else raw_specification
    )
    if _schema_error(_object_properties(CANONICAL_TEST_PLAN_JSON_SCHEMA).get("specification"), schema_candidate):
        recovery.keep(
            ("specification",),
            raw_specification,
            kind="invalid",
            message=(
                "specification does not match a supported schemaVersion 1.0 specification object. Choose a "
                "specification in the builder."
            ),
        )
        return draft, None
    try:
        boundary, uses_resource_groups, _profile = parse_canonical_specification(base_specification)
    except (CatalogueError, ValueError) as error:
        recovery.keep(
            ("specification",),
            raw_specification,
            kind="invalid",
            message=f"{_sentence(error)} Choose a specification in the builder.",
        )
        return draft, None
    update_ref = ("specification", _OPENAPI_UPDATE_KEY)
    update_path = ".".join(update_ref)
    raw_update = raw_specification.get(_OPENAPI_UPDATE_KEY)
    selected_update: str | None = None
    update_error: str | None = None
    if _OPENAPI_UPDATE_KEY in raw_specification:
        if not isinstance(raw_update, str):
            update_error = f"{update_path} must be a string."
        else:
            try:
                openapi_document_update_for_boundary(
                    boundary.scheme, boundary.specification, boundary.version, raw_update
                )
                selected_update = raw_update
            except ValueError as error:
                update_error = _sentence(error)
    try:
        draft = draft.with_catalogue_boundary(
            scheme=boundary.scheme,
            specification=boundary.specification,
            version=boundary.version,
            openapi_document_update=selected_update,
        )
    except (CatalogueError, ValueError) as error:
        recovery.keep(
            ("specification",),
            raw_specification,
            kind="invalid",
            message=f"{_sentence(error)} Choose a specification in the builder.",
        )
        return draft, None
    if update_error is not None:
        recovery.keep(
            update_ref,
            raw_update,
            kind="invalid",
            message=f"{update_error} Choose an OpenAPI document update in the builder.",
        )
    elif _OPENAPI_UPDATE_KEY not in raw_specification and draft.openapi_document_update is not None:
        recovery.note(
            path=update_path,
            ref=update_ref,
            kind="missing",
            message=(
                f"{update_path} is missing. The latest update, {draft.openapi_document_update}, was selected; "
                "choose an earlier one in the builder if you test against an older OpenAPI document."
            ),
        )
    return draft, uses_resource_groups


def _latest_openapi_document_update(raw_specification: Mapping[str, JsonValue]) -> str | None:
    """Return the latest OpenAPI document update for a raw specification, if resolvable.

    Args:
        raw_specification: Raw ``specification`` object without ``openApiDocumentUpdate``.

    Returns:
        Latest published update value, or ``None`` when the boundary cannot be
        parsed or the version publishes no selectable updates.
    """
    try:
        boundary, _uses_resource_groups, _profile = parse_canonical_specification(raw_specification)
        _definition, version_definition = specification_for_boundary(
            boundary.scheme, boundary.specification, boundary.version
        )
    except CatalogueError, ValueError:
        return None
    latest = latest_openapi_document_update(version_definition)
    return latest.update if latest is not None else None


def _sentence(error: Exception) -> str:
    """Return an exception message as a participant-facing sentence.

    Args:
        error: Validation exception.

    Returns:
        Message without the internal ``testPlan.`` prefix, ending in a full stop.
    """
    message = str(error).removeprefix("testPlan.")
    return message if message.endswith(".") else f"{message}."


def _recover_security_environment(raw_plan: Mapping[str, JsonValue], recovery: _Recovery) -> JsonObject:
    """Recover valid ``securityEnvironment`` fields one by one.

    Args:
        raw_plan: Raw imported JSON object.
        recovery: Recovery accumulator.

    Returns:
        Valid security environment fields.
    """
    raw_environment = _section_object(raw_plan, "securityEnvironment", recovery, required=True)
    if raw_environment is None:
        return {}
    properties = _schema_properties("securityEnvironment")
    recovered = _recover_fields(
        raw_environment,
        recovery,
        base_ref=("securityEnvironment",),
        properties={key: value for key, value in properties.items() if key != "mtls"},
        allow_unknown=False,
        conflicts=_SECURITY_ENVIRONMENT_CONFLICTS,
        skip_keys=frozenset({"mtls"}),
        semantic_check=_security_environment_field_error,
    )
    if "mtls" in raw_environment:
        raw_mtls = raw_environment["mtls"]
        if isinstance(raw_mtls, dict):
            mtls_properties = properties.get("mtls")
            recovered["mtls"] = _recover_fields(
                raw_mtls,
                recovery,
                base_ref=("securityEnvironment", "mtls"),
                properties=_object_properties(mtls_properties),
                allow_unknown=False,
                conflicts=_MTLS_CONFLICTS,
            )
        else:
            recovery.keep(
                ("securityEnvironment", "mtls"),
                raw_mtls,
                kind="invalid",
                message="securityEnvironment.mtls must be a JSON object.",
            )
    return recovered


def _security_environment_field_error(key: str, value: JsonValue) -> str | None:
    """Return the semantic validation error for one security environment field.

    Args:
        key: Field name.
        value: Field value.

    Returns:
        Error message, or ``None`` when the field is valid (for example
        ``discoveryUrl`` must be an HTTPS URL).
    """
    try:
        validate_canonical_security_environment({key: value})
    except CatalogueError as error:
        return _sentence(error)
    return None


def _recover_business_test_data(
    raw_plan: Mapping[str, JsonValue],
    recovery: _Recovery,
    *,
    family: str | None,
) -> JsonObject:
    """Recover ``businessTestData`` sections one by one.

    Args:
        raw_plan: Raw imported JSON object.
        recovery: Recovery accumulator.
        family: Recovered specification family, if any.

    Returns:
        Valid business test data sections.
    """
    if family == "OBL_DCR":
        if "businessTestData" in raw_plan:
            recovery.keep(
                ("businessTestData",),
                raw_plan["businessTestData"],
                kind="invalid",
                message="businessTestData is not allowed for OBL_DCR test plans.",
            )
        return {}
    raw_data = _section_object(raw_plan, "businessTestData", recovery, required=family == "OBL_READ_WRITE")
    if raw_data is None:
        return {}
    recovered: JsonObject = {}
    for key, value in raw_data.items():
        try:
            canonical_plan_config(security_environment={}, business_test_data={key: value})
        except CatalogueError as error:
            recovery.keep(
                ("businessTestData", key),
                value,
                kind="invalid",
                message=str(error).removeprefix("testPlan."),
            )
            continue
        recovered[key] = _copy_json_value(value)
    return recovered


def _keep_business_test_data_hidden_by_scope(
    draft: BuilderDraft,
    business_test_data: Mapping[str, JsonValue],
    recovery: _Recovery,
) -> None:
    """Keep imported business data sections the builder hides for the current scope.

    The builder only emits ``businessTestData`` sections for API families in
    scope, so sections for other families would otherwise disappear from the
    composed plan JSON without validation. They are kept verbatim so normal
    validation still sees them.

    Args:
        draft: Draft with recovered scope.
        business_test_data: Recovered business test data sections.
        recovery: Recovery accumulator.
    """
    emitted = builder_plan_json_from_draft_or_skeleton(draft).get("businessTestData")
    emitted_keys = set(emitted) if isinstance(emitted, dict) else set()
    for key, value in business_test_data.items():
        if key in emitted_keys:
            continue
        recovery.keep(
            ("businessTestData", key),
            _copy_json_value(value),
            kind="skipped",
            message=(
                f"businessTestData.{key} is not used by the selected scope. It is kept in the plan JSON; remove it "
                "there or add matching scope in the builder."
            ),
        )


def _recover_dynamic_client_registration(
    raw_plan: Mapping[str, JsonValue],
    recovery: _Recovery,
    *,
    family: str | None,
) -> JsonObject:
    """Recover ``dynamicClientRegistration`` fields one by one.

    Args:
        raw_plan: Raw imported JSON object.
        recovery: Recovery accumulator.
        family: Recovered specification family, if any.

    Returns:
        Valid DCR configuration fields.
    """
    if family == "OBL_READ_WRITE":
        if "dynamicClientRegistration" in raw_plan:
            recovery.keep(
                ("dynamicClientRegistration",),
                raw_plan["dynamicClientRegistration"],
                kind="invalid",
                message="dynamicClientRegistration is not allowed for OBL_READ_WRITE test plans.",
            )
        return {}
    raw_registration = _section_object(raw_plan, "dynamicClientRegistration", recovery, required=family == "OBL_DCR")
    if raw_registration is None:
        return {}
    recovered = _recover_fields(
        raw_registration,
        recovery,
        base_ref=("dynamicClientRegistration",),
        properties=_schema_properties("dynamicClientRegistration"),
        allow_unknown=False,
        conflicts=_DCR_CONFLICTS,
    )
    if family == "OBL_DCR":
        preserved = recovery.overlay.get("dynamicClientRegistration")
        preserved_keys = set(preserved) if isinstance(preserved, dict) else set()
        if "registrationAudience" not in recovered and "registrationAudience" not in preserved_keys:
            recovery.note(
                path="dynamicClientRegistration.registrationAudience",
                ref=("dynamicClientRegistration", "registrationAudience"),
                kind="missing",
                message="dynamicClientRegistration.registrationAudience is required for OBL_DCR test plans.",
            )
        assertion_keys = {"softwareStatementAssertion", "softwareStatementAssertionPath"}
        if not assertion_keys & (set(recovered) | preserved_keys):
            recovery.note(
                path="dynamicClientRegistration.softwareStatementAssertionPath",
                ref=("dynamicClientRegistration", "softwareStatementAssertionPath"),
                kind="missing",
                message=(
                    "dynamicClientRegistration needs softwareStatementAssertionPath or softwareStatementAssertion."
                ),
            )
    return recovered


def _recover_metadata(raw_plan: Mapping[str, JsonValue], recovery: _Recovery) -> JsonObject:
    """Recover ``metadata`` fields; extra metadata keys are allowed.

    Args:
        raw_plan: Raw imported JSON object.
        recovery: Recovery accumulator.

    Returns:
        Valid metadata fields.
    """
    raw_metadata = _section_object(raw_plan, "metadata", recovery, required=True)
    if raw_metadata is None:
        return {}
    return _recover_fields(
        raw_metadata,
        recovery,
        base_ref=("metadata",),
        properties=_schema_properties("metadata"),
        allow_unknown=True,
        conflicts=(),
    )


def _recover_scope(
    raw_plan: Mapping[str, JsonValue],
    draft: BuilderDraft,
    recovery: _Recovery,
    *,
    uses_resource_groups: bool | None,
) -> BuilderDraft:
    """Recover resource-group or direct-endpoint scope item by item.

    Each item is resolved on its own against the draft's specification so one
    unknown endpoint does not discard the rest of the scope.

    Args:
        raw_plan: Raw JSON object holding ``resourceGroups``/``endpoints``.
        draft: Draft being populated, with a boundary when one was recovered.
        recovery: Recovery accumulator.
        uses_resource_groups: Whether the family uses resource groups, or
            ``None`` when no specification was recovered.

    Returns:
        Draft with the recovered scope selection saved.
    """
    if uses_resource_groups is None:
        for key in _SCOPE_KEYS:
            if key in raw_plan:
                recovery.keep(
                    (key,),
                    raw_plan[key],
                    kind="skipped",
                    message=(
                        f"{key} could not be loaded because the specification is missing or invalid. Choose a "
                        "specification in the builder to load it, then check the scope."
                    ),
                )
        return draft
    scope_key, other_key, family = (
        ("resourceGroups", "endpoints", "OBL_READ_WRITE")
        if uses_resource_groups
        else ("endpoints", "resourceGroups", "OBL_DCR")
    )
    if other_key in raw_plan:
        recovery.keep(
            (other_key,),
            raw_plan[other_key],
            kind="invalid",
            message=f"{other_key} is not allowed for {family} test plans.",
        )
    if scope_key not in raw_plan:
        recovery.note(
            path=scope_key,
            ref=(scope_key,),
            kind="missing",
            message=f"{scope_key} is missing. Select the scope in the builder.",
        )
        return draft
    raw_items = raw_plan[scope_key]
    if not isinstance(raw_items, list):
        recovery.keep(
            (scope_key,),
            raw_items,
            kind="invalid",
            message=f"{scope_key} must be an array. Select the scope in the builder.",
        )
        return draft
    if not raw_items:
        recovery.note(
            path=scope_key,
            ref=(scope_key,),
            kind="missing",
            message=f"{scope_key} is empty. Select the scope in the builder.",
        )
        return draft
    specification_json = _probe_specification_json(draft)
    resource_group_ids: list[str] = []
    endpoint_ids: list[str] = []
    capability_ids: dict[str, tuple[str, ...]] = {}
    baseline_endpoint_ids: set[str] = set()
    if not uses_resource_groups:
        baseline_endpoint_ids = set(_probe_scope(specification_json, "endpoints", [_DCR_REGISTER_ENDPOINT])[1])
    for index, item in enumerate(raw_items):
        try:
            if uses_resource_groups:
                item_groups, item_endpoints, item_capabilities = _probe_scope(specification_json, scope_key, [item])
            else:
                probe_items = [item] if _is_dcr_register_item(item) else [_DCR_REGISTER_ENDPOINT, item]
                _groups, probed_endpoints, item_capabilities = _probe_scope(specification_json, scope_key, probe_items)
                item_groups = ()
                item_endpoints = (
                    probed_endpoints
                    if _is_dcr_register_item(item)
                    else tuple(endpoint for endpoint in probed_endpoints if endpoint not in baseline_endpoint_ids)
                )
            duplicates = [group for group in item_groups if group in resource_group_ids] + [
                endpoint for endpoint in item_endpoints if endpoint in endpoint_ids
            ]
            if duplicates:
                raise CatalogueError(f"duplicates scope already loaded: {', '.join(duplicates)}")
            candidate = draft.with_scope_selection(
                resource_group_ids=(*resource_group_ids, *item_groups),
                endpoint_ids=(*endpoint_ids, *item_endpoints),
                endpoint_capability_ids={**capability_ids, **item_capabilities},
            )
            plan_document_from_draft(candidate)
        except (CatalogueError, ValueError, TypeError) as error:
            recovery.keep(
                (scope_key,),
                item,
                kind="invalid",
                path=f"{scope_key}[{index}]",
                message=(
                    f"{scope_key}[{index}] could not be loaded: {_sentence(error)} "
                    "Fix it in the plan JSON or select the scope in the builder."
                ),
                append=True,
            )
            continue
        resource_group_ids.extend(item_groups)
        endpoint_ids.extend(item_endpoints)
        capability_ids.update(item_capabilities)
    return draft.with_scope_selection(
        resource_group_ids=tuple(resource_group_ids),
        endpoint_ids=tuple(endpoint_ids),
        endpoint_capability_ids=capability_ids,
    )


def _probe_scope(
    specification_json: JsonObject,
    scope_key: str,
    items: list[JsonValue],
) -> tuple[tuple[str, ...], tuple[str, ...], dict[str, tuple[str, ...]]]:
    """Resolve scope items to builder ids via a minimal canonical plan.

    Args:
        specification_json: Valid canonical specification object.
        scope_key: ``resourceGroups`` or ``endpoints``.
        items: Scope items to resolve.

    Returns:
        Builder resource-group ids, endpoint ids, and capability ids.

    Raises:
        CatalogueError: If the items are not valid for the specification.
    """
    probe: JsonObject = {
        "schemaVersion": "1.0",
        "specification": specification_json,
        "securityEnvironment": {},
        scope_key: items,
        "metadata": {},
    }
    if scope_key == "resourceGroups":
        probe["businessTestData"] = {}
    document = parse_test_plan_document(probe)
    if not isinstance(document, PlanDocumentV2):
        raise CatalogueError("scope probe did not produce a canonical document")
    return draft_scope_from_plan_document(document)


def _probe_specification_json(draft: BuilderDraft) -> JsonObject:
    """Return the builder's canonical specification JSON for a draft.

    Args:
        draft: Draft with a recovered boundary.

    Returns:
        Canonical ``specification`` object.
    """
    specification = builder_plan_json_from_draft_or_skeleton(draft).get("specification")
    return dict(specification) if isinstance(specification, dict) else {}


def _is_dcr_register_item(item: JsonValue) -> bool:
    """Return whether a raw DCR endpoint item is the registration endpoint.

    Args:
        item: Raw endpoint item.

    Returns:
        True for ``POST /register`` objects.
    """
    return isinstance(item, dict) and item.get("method") == "POST" and item.get("path") == "/register"


def _draft_uses_resource_groups(draft: BuilderDraft) -> bool | None:
    """Return whether a draft's specification uses resource groups.

    Args:
        draft: Builder draft.

    Returns:
        True for ``OBL_READ_WRITE``, False for ``OBL_DCR``, ``None`` without a
        boundary.
    """
    specification = builder_plan_json_from_draft_or_skeleton(draft).get("specification")
    if not isinstance(specification, dict):
        return None
    return specification.get("family") != "OBL_DCR"


def _section_object(
    raw_plan: Mapping[str, JsonValue],
    key: str,
    recovery: _Recovery,
    *,
    required: bool,
) -> Mapping[str, JsonValue] | None:
    """Return a top-level object section, recording missing or invalid shapes.

    Args:
        raw_plan: Raw imported JSON object.
        key: Section key.
        recovery: Recovery accumulator.
        required: Whether the canonical schema requires the section.

    Returns:
        Section object, or ``None`` when absent or not an object.
    """
    if key not in raw_plan:
        if required:
            recovery.note(path=key, ref=(key,), kind="missing", message=f"{key} is missing.")
        return None
    value = raw_plan[key]
    if not isinstance(value, dict):
        recovery.keep((key,), value, kind="invalid", message=f"{key} must be a JSON object.")
        return None
    return value


def _recover_fields(
    raw_object: Mapping[str, JsonValue],
    recovery: _Recovery,
    *,
    base_ref: tuple[str, ...],
    properties: Mapping[str, JsonValue],
    allow_unknown: bool,
    conflicts: tuple[tuple[str, str], ...],
    skip_keys: frozenset[str] = frozenset(),
    semantic_check: Callable[[str, JsonValue], str | None] | None = None,
) -> JsonObject:
    """Recover individually valid fields of a JSON object.

    Args:
        raw_object: Raw JSON object.
        recovery: Recovery accumulator.
        base_ref: Object-key path of ``raw_object`` in the plan.
        properties: Canonical JSON Schema property definitions.
        allow_unknown: Whether keys outside ``properties`` are valid.
        conflicts: Mutually exclusive key pairs; both are preserved in the
            overlay when both are present.
        skip_keys: Keys handled by the caller.
        semantic_check: Optional extra per-field validation.

    Returns:
        Valid fields.
    """
    location = ".".join(base_ref)
    recovered: JsonObject = {}
    for key, value in raw_object.items():
        if key in skip_keys:
            continue
        ref = (*base_ref, key)
        schema = properties.get(key)
        if schema is None:
            if allow_unknown:
                recovered[key] = _copy_json_value(value)
            else:
                recovery.keep(ref, value, kind="unknown", message=f"{location}.{key} is not a recognised field.")
            continue
        error = _schema_error(schema, value)
        if error is None and semantic_check is not None:
            error = semantic_check(key, value)
        if error is not None:
            field_path = f"{location}.{key}"
            message = error if error.startswith(field_path) else f"{field_path}: {error}"
            recovery.keep(ref, value, kind="invalid", message=message)
            continue
        recovered[key] = _copy_json_value(value)
    for first, second in conflicts:
        if first in recovered and second in recovered:
            for key in (first, second):
                recovery.keep(
                    (*base_ref, key),
                    recovered.pop(key),
                    kind="invalid",
                    message=f"{location}: supply either {first} or {second}, not both.",
                )
    return recovered


def _schema_error(schema: JsonValue, value: JsonValue) -> str | None:
    """Return the first JSON Schema error for a value.

    Args:
        schema: Canonical sub-schema.
        value: Value to validate.

    Returns:
        Error message, or ``None`` when valid.
    """
    return json_schema_error(schema, value) if isinstance(schema, dict) else None


def _schema_properties(section: str) -> Mapping[str, JsonValue]:
    """Return canonical schema property definitions for a top-level section.

    Args:
        section: Top-level test-plan key.

    Returns:
        Property definitions keyed by field name.
    """
    properties = CANONICAL_TEST_PLAN_JSON_SCHEMA.get("properties")
    if not isinstance(properties, dict):
        return {}
    return _object_properties(properties.get(section))


def _object_properties(schema: JsonValue | None) -> Mapping[str, JsonValue]:
    """Return ``properties`` from an object schema.

    Args:
        schema: Object JSON Schema.

    Returns:
        Property definitions keyed by field name.
    """
    if not isinstance(schema, dict):
        return {}
    properties = schema.get("properties")
    return properties if isinstance(properties, dict) else {}


def _prune_builder_owned(
    overlay: JsonObject,
    builder_json: Mapping[str, JsonValue],
    section: str,
    *,
    keys: frozenset[str] | None = None,
) -> None:
    """Drop overlay values for paths the builder now produces.

    Args:
        overlay: Mutable unrepresented-field overlay.
        builder_json: Builder-generated plan JSON after the step save.
        section: Top-level section owned by the saved step.
        keys: Keys within ``section`` owned by the step, or ``None`` when the
            step owns the whole section.
    """
    if section not in overlay or section not in builder_json:
        return
    overlay_value = overlay[section]
    builder_value = builder_json[section]
    if not isinstance(overlay_value, dict) or not isinstance(builder_value, dict):
        if keys is None:
            del overlay[section]
        return
    if keys is not None:
        builder_value = {key: value for key, value in builder_value.items() if key in keys}
    pruned = _prune_object(overlay_value, builder_value)
    if pruned:
        overlay[section] = pruned
    else:
        del overlay[section]


def _prune_object(overlay: Mapping[str, JsonValue], builder: Mapping[str, JsonValue]) -> JsonObject:
    """Return overlay entries that the builder does not produce.

    Args:
        overlay: Overlay object.
        builder: Builder-generated object at the same path.

    Returns:
        Remaining overlay entries.
    """
    remaining: JsonObject = {}
    for key, value in overlay.items():
        if key not in builder:
            remaining[key] = value
            continue
        builder_value = builder[key]
        if isinstance(value, dict) and isinstance(builder_value, dict):
            nested = _prune_object(value, builder_value)
            if nested:
                remaining[key] = nested
    return remaining


def _issue_still_applies(
    issue: PlanImportIssue,
    overlay: Mapping[str, JsonValue],
    builder_json: Mapping[str, JsonValue],
) -> bool:
    """Return whether an import issue is still relevant after a builder save.

    Args:
        issue: Import issue.
        overlay: Current unrepresented-field overlay.
        builder_json: Builder-generated plan JSON.

    Returns:
        False for whole-plan notices (they describe the original import),
        missing-field issues the builder now fills, and preserved values the
        builder has taken over.
    """
    if not issue.ref:
        return False
    if issue.kind == "missing":
        return not _has_path(builder_json, issue.ref, require_non_empty_array=True)
    return _has_path(overlay, issue.ref, require_non_empty_array=False)


def _has_path(value: Mapping[str, JsonValue], ref: tuple[str, ...], *, require_non_empty_array: bool) -> bool:
    """Return whether a JSON object holds a value at an object-key path.

    Args:
        value: JSON object to search.
        ref: Object-key path.
        require_non_empty_array: Treat empty arrays as absent.

    Returns:
        True when a value exists at ``ref``.
    """
    current: JsonValue = dict(value)
    for key in ref:
        if not isinstance(current, dict) or key not in current:
            return False
        current = current[key]
    return not (require_non_empty_array and current == [])


def _json_scalar_text(value: JsonValue) -> str:
    """Return a short display form of a JSON value for messages.

    Args:
        value: JSON value.

    Returns:
        Quoted strings, or the JSON type name for structured values.
    """
    if isinstance(value, str):
        return f'"{value[:40]}"'
    if isinstance(value, dict | list):
        return "object" if isinstance(value, dict) else "array"
    return str(value)


def _copy_json_object(value: Mapping[str, JsonValue]) -> JsonObject:
    """Return a deep copy of a JSON object.

    Args:
        value: JSON object.

    Returns:
        Independent copy.
    """
    return {key: _copy_json_value(item) for key, item in value.items()}


def _copy_json_value(value: JsonValue) -> JsonValue:
    """Return a deep copy of a JSON value.

    Args:
        value: JSON value.

    Returns:
        Independent copy.
    """
    if isinstance(value, dict):
        return {key: _copy_json_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_copy_json_value(item) for item in value]
    return value
