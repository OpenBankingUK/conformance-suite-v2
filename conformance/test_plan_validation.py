"""Shared validation service for JSON-first Open Banking test plans."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from jsonschema import Draft202012Validator  # type: ignore[import-untyped]  # runtime schema library lacks stubs

from conformance.catalogue import (
    CatalogueError,
    CompiledTestPlan,
    PlanDocumentV2,
    PlanExecutionMode,
    TestCatalogue,
    compile_test_plan_document,
    model_bank_config_from_plan_document,
    parse_test_plan_document,
    plan_document_to_json_object,
)
from conformance.catalogue_registry import supported_catalogues
from conformance.json_types import JsonObject, JsonValue
from conformance.model_bank_config import ConfigError, ModelBankConfig, parse_model_bank_config

ValidationLayer = Literal["schema", "semantic", "security", "business", "execution"]
"""Validation layers reported for JSON-first test-plan checks."""

ValidationSeverity = Literal["error", "warning"]
"""Validation severities emitted by the shared test-plan validator."""

CANONICAL_TEST_PLAN_JSON_SCHEMA: JsonObject = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://openbankinguk.github.io/fcs/test-plan.schema.json",
    "title": "Open Banking UK JSON-first test plan",
    "type": "object",
    "required": [
        "schemaVersion",
        "specification",
        "securityEnvironment",
        "resourceGroups",
        "businessTestData",
        "metadata",
    ],
    "additionalProperties": False,
    "properties": {
        "schemaVersion": {"const": "1.0"},
        "executionMode": {"enum": ["certification", "development"]},
        "specification": {
            "type": "object",
            "required": ["family", "version"],
            "additionalProperties": False,
            "properties": {
                "family": {"const": "OBL_READ_WRITE"},
                "version": {"type": "string", "minLength": 1},
                "profile": {"enum": ["FAPI1_ADVANCED", "FAPI2", "ALL", "fapi1-advanced", "fapi2", "all"]},
                "securityProfile": {"enum": ["FAPI1_ADVANCED", "FAPI2", "ALL", "fapi1-advanced", "fapi2", "all"]},
            },
        },
        "securityEnvironment": {
            "type": "object",
            "required": ["discoveryUrl"],
            "additionalProperties": False,
            "properties": {
                "name": {"type": "string", "minLength": 1},
                "discoveryUrl": {"type": "string", "minLength": 1},
                "issuer": {"type": "string", "minLength": 1},
                "authorizationEndpoint": {"type": "string", "minLength": 1},
                "tokenEndpoint": {"type": "string", "minLength": 1},
                "jwksUri": {"type": "string", "minLength": 1},
                "clientAuthMethod": {"enum": ["private_key_jwt", "tls_client_auth"]},
                "signingAlgorithm": {"type": "string", "minLength": 1},
                "mtls": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "enabled": {"type": "boolean"},
                        "certificateRef": {"type": "string", "minLength": 1},
                        "privateKeyRef": {"type": "string", "minLength": 1},
                        "caBundleRef": {"type": "string", "minLength": 1},
                        "certificatePathRoot": {"type": "string", "minLength": 1},
                    },
                },
                "clientId": {"type": "string", "minLength": 1},
                "redirectUri": {"type": "string", "minLength": 1},
                "openBankingIntentId": {"type": "string", "minLength": 1},
                "resourceBaseUrl": {"type": "string", "minLength": 1},
                "responseType": {"type": "string", "minLength": 1},
                "acrValuesSupported": {"type": "array", "items": {"type": "string", "minLength": 1}},
                "signingCertificateRef": {"type": "string", "minLength": 1},
                "signingPrivateKeyRef": {"type": "string", "minLength": 1},
                "signingKeyId": {"type": "string", "minLength": 1},
                "clientAssertionIssuer": {"type": "string", "minLength": 1},
                "clientAssertionSubject": {"type": "string", "minLength": 1},
                "tppSignatureIssuer": {"type": "string", "minLength": 1},
                "tppSignatureTan": {"type": "string", "minLength": 1},
                "caBundleRef": {"type": "string", "minLength": 1},
                "xFapiFinancialId": {"type": "string", "minLength": 1},
                "sendXFapiCustomerIpAddress": {"type": "boolean"},
                "xFapiCustomerIpAddress": {"type": "string", "minLength": 1},
                "timeoutSeconds": {"type": "number", "exclusiveMinimum": 0},
            },
        },
        "resourceGroups": {
            "type": "array",
            "minItems": 1,
            "items": {
                "oneOf": [
                    {"enum": ["AIS", "PIS", "CBPII", "VRP"]},
                    {
                        "type": "object",
                        "required": ["id"],
                        "additionalProperties": False,
                        "properties": {
                            "id": {"enum": ["AIS", "PIS", "CBPII", "VRP"]},
                            "label": {"type": "string", "minLength": 1},
                            "endpoints": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "required": ["method", "path"],
                                    "additionalProperties": False,
                                    "properties": {
                                        "method": {"enum": ["GET", "POST", "PUT", "PATCH", "DELETE"]},
                                        "path": {"type": "string", "pattern": "^/"},
                                        "operationId": {"type": "string", "minLength": 1},
                                        "capabilities": {
                                            "type": "array",
                                            "items": {"type": "string", "minLength": 1},
                                        },
                                    },
                                },
                            },
                        },
                    },
                ]
            },
        },
        "businessTestData": {"type": "object"},
        "metadata": {"type": "object"},
    },
}
"""JSON Schema for canonical Open Banking UK JSON-first test plans."""

_CANONICAL_TEST_PLAN_SCHEMA_VALIDATOR = Draft202012Validator(CANONICAL_TEST_PLAN_JSON_SCHEMA)
"""Reusable JSON Schema validator for canonical test plans."""


class TestPlanValidationError(ValueError):
    """Raised when a JSON-first test plan cannot be prepared for execution.

    Attributes:
        result: Structured validation result describing the blocking issues.
    """

    result: TestPlanValidationResult

    def __init__(self, result: TestPlanValidationResult) -> None:
        """Initialise the validation error.

        Args:
            result: Validation result containing at least one blocking issue.
        """
        super().__init__(result.summary_message())
        self.result = result


@dataclass(frozen=True)
class TestPlanValidationIssue:
    """One issue found while validating a JSON-first test plan.

    Attributes:
        layer: Validation layer that produced the issue.
        severity: Whether the issue blocks execution or is advisory.
        message: Human-readable validation message.
    """

    layer: ValidationLayer
    severity: ValidationSeverity
    message: str

    @property
    def blocking(self) -> bool:
        """Return whether this issue blocks execution.

        Returns:
            True for ``error`` issues, otherwise false.
        """
        return self.severity == "error"

    def to_json_object(self) -> JsonObject:
        """Serialise the issue to JSON evidence.

        Returns:
            JSON object containing layer, severity, blocking flag, and message.
        """
        return {
            "layer": self.layer,
            "severity": self.severity,
            "blocking": self.blocking,
            "message": self.message,
        }


@dataclass(frozen=True)
class TestPlanValidationResult:
    """Structured validation outcome for a JSON-first test plan.

    Attributes:
        schema_version: Test-plan schema version that was validated.
        execution_mode: Execution mode declared by the test plan.
        issues: Validation issues collected across schema, semantic, security,
            business-data, and execution-mode checks.
    """

    schema_version: str
    execution_mode: PlanExecutionMode
    issues: tuple[TestPlanValidationIssue, ...]

    @property
    def valid(self) -> bool:
        """Return whether the plan passed all blocking checks.

        Returns:
            True when no issue has ``severity == "error"``.
        """
        return not any(issue.blocking for issue in self.issues)

    def summary_message(self) -> str:
        """Return a concise validation summary message.

        Returns:
            First blocking issue message, first warning message, or a success
            summary when no issues were emitted.
        """
        for issue in self.issues:
            if issue.blocking:
                return issue.message
        if self.issues:
            return self.issues[0].message
        return "Test plan validation passed"

    def to_json_object(self) -> JsonObject:
        """Serialise the validation result to JSON evidence.

        Returns:
            JSON object suitable for embedding in a run result.
        """
        return {
            "schemaVersion": self.schema_version,
            "executionMode": self.execution_mode,
            "valid": self.valid,
            "issues": [issue.to_json_object() for issue in self.issues],
        }


@dataclass(frozen=True)
class PreparedTestPlan:
    """Validated execution bundle derived from a JSON-first test plan.

    Attributes:
        document: Parsed canonical test plan.
        config: Validated model-bank config derived from the plan.
        compiled_plan: Compiled executable catalogue plan.
        runtime_inputs: Runtime inputs derived from the plan's business data and
            non-secret references.
        snapshot: Secret-safe canonical plan JSON captured at launch time.
        validation: Structured validation outcome.
    """

    document: PlanDocumentV2
    config: ModelBankConfig
    compiled_plan: CompiledTestPlan
    runtime_inputs: Mapping[str, JsonValue]
    snapshot: JsonObject
    validation: TestPlanValidationResult


def prepare_test_plan_for_run(
    raw_plan: object,
    *,
    base_dir: Path,
    catalogues: Iterable[TestCatalogue] | None = None,
) -> PreparedTestPlan:
    """Parse, validate, compile, and snapshot a canonical JSON test plan.

    Args:
        raw_plan: Decoded JSON test-plan object.
        base_dir: Directory used to resolve local certificate or file-reference
            paths when preparing the plan for execution.
        catalogues: Optional catalogue set to compile against.

    Returns:
        Prepared test-plan execution bundle.

    Raises:
        TestPlanValidationError: If schema, semantic, security, or business-data
            validation blocks execution.
    """
    available_catalogues = tuple(catalogues) if catalogues is not None else supported_catalogues()
    schema_issues = _canonical_schema_issues(raw_plan)
    if schema_issues:
        result = TestPlanValidationResult(
            schema_version=_schema_version_from_raw(raw_plan),
            execution_mode="certification",
            issues=schema_issues,
        )
        raise TestPlanValidationError(result)
    try:
        parsed = parse_test_plan_document(raw_plan)
    except CatalogueError as error:
        result = TestPlanValidationResult(
            schema_version="unknown",
            execution_mode="certification",
            issues=(TestPlanValidationIssue("schema", "error", str(error)),),
        )
        raise TestPlanValidationError(result) from error
    if not isinstance(parsed, PlanDocumentV2) or parsed.schema_version != "1.0":
        result = TestPlanValidationResult(
            schema_version=parsed.schema_version,
            execution_mode="certification",
            issues=(
                TestPlanValidationIssue(
                    "schema",
                    "error",
                    "JSON-first execution requires a schemaVersion 1.0 test plan document.",
                ),
            ),
        )
        raise TestPlanValidationError(result)

    issues = list(_execution_mode_issues(parsed))
    try:
        config = parse_model_bank_config(model_bank_config_from_plan_document(parsed), base_dir=base_dir)
    except ConfigError as error:
        issues.append(TestPlanValidationIssue("security", "error", f"Security environment validation failed: {error}"))
        raise TestPlanValidationError(_validation_result(parsed, issues)) from error

    try:
        compiled_plan = compile_test_plan_document(parsed, available_catalogues)
    except CatalogueError as error:
        issues.append(TestPlanValidationIssue(_catalogue_error_layer(str(error)), "error", str(error)))
        raise TestPlanValidationError(_validation_result(parsed, issues)) from error

    issues.extend(_compiled_plan_issues(parsed, compiled_plan))
    validation = _validation_result(parsed, issues)
    if not validation.valid:
        raise TestPlanValidationError(validation)
    return PreparedTestPlan(
        document=parsed,
        config=config,
        compiled_plan=compiled_plan,
        runtime_inputs=parsed.runtime_inputs,
        snapshot=safe_test_plan_snapshot(parsed, compiled_plan=compiled_plan),
        validation=validation,
    )


def validate_test_plan_for_run(
    raw_plan: object,
    *,
    base_dir: Path,
    catalogues: Iterable[TestCatalogue] | None = None,
) -> TestPlanValidationResult:
    """Validate a canonical JSON test plan without returning execution state.

    Args:
        raw_plan: Decoded JSON test-plan object.
        base_dir: Directory used to resolve local certificate or file-reference
            paths when preparing the plan for execution.
        catalogues: Optional catalogue set to compile against.

    Returns:
        Structured validation outcome.
    """
    try:
        return prepare_test_plan_for_run(raw_plan, base_dir=base_dir, catalogues=catalogues).validation
    except TestPlanValidationError as error:
        return error.result


def validate_test_plan_for_load(raw_plan: object) -> TestPlanValidationResult:
    """Validate a JSON-first plan enough to import or edit it.

    Args:
        raw_plan: Decoded JSON test-plan object.

    Returns:
        Structured validation outcome for schema and basic semantic checks.
    """
    schema_issues = _canonical_schema_issues(raw_plan)
    if schema_issues:
        return TestPlanValidationResult(
            schema_version=_schema_version_from_raw(raw_plan),
            execution_mode="certification",
            issues=schema_issues,
        )
    try:
        parsed = parse_test_plan_document(raw_plan)
    except CatalogueError as error:
        return TestPlanValidationResult(
            schema_version=_schema_version_from_raw(raw_plan),
            execution_mode="certification",
            issues=(TestPlanValidationIssue("schema", "error", str(error)),),
        )
    if not isinstance(parsed, PlanDocumentV2):
        return TestPlanValidationResult(
            schema_version=parsed.schema_version,
            execution_mode="certification",
            issues=(
                TestPlanValidationIssue(
                    "schema",
                    "error",
                    "Browser import requires a shared JSON-first test plan document.",
                ),
            ),
        )
    return _validation_result(parsed, _execution_mode_issues(parsed))


def safe_test_plan_snapshot(
    document: PlanDocumentV2,
    *,
    compiled_plan: CompiledTestPlan | None = None,
    sensitive_runtime_input_ids: Iterable[str] = (),
) -> JsonObject:
    """Return a secret-safe canonical test-plan snapshot.

    Args:
        document: Parsed test-plan document to snapshot.
        compiled_plan: Optional compiled plan used to identify sensitive
            runtime inputs.
        sensitive_runtime_input_ids: Additional sensitive runtime input ids from
            callers that already computed compiler trace metadata.

    Returns:
        Canonical plan JSON with secret-bearing scalar values removed.
    """
    sensitive_input_ids = {
        trace.input_id
        for trace in (compiled_plan.traceability.runtime_input_snapshot if compiled_plan is not None else ())
        if trace.sensitive
    }
    sensitive_input_ids.update(sensitive_runtime_input_ids)
    return _safe_json_object(plan_document_to_json_object(document), sensitive_input_ids)


def _validation_result(
    document: PlanDocumentV2,
    issues: Iterable[TestPlanValidationIssue],
) -> TestPlanValidationResult:
    """Build a validation result for a parsed canonical test plan.

    Args:
        document: Parsed test-plan document.
        issues: Validation issues to include.

    Returns:
        Structured validation result.
    """
    return TestPlanValidationResult(
        schema_version=document.schema_version,
        execution_mode=document.execution_mode,
        issues=tuple(issues),
    )


def _canonical_schema_issues(raw_plan: object) -> tuple[TestPlanValidationIssue, ...]:
    """Return JSON Schema issues for canonical test plans.

    Args:
        raw_plan: Decoded JSON value to inspect.

    Returns:
        Schema validation issues, or an empty tuple when the value is not a
        canonical schemaVersion ``1.0`` plan and should use a compatibility
        parser path instead.
    """
    if not isinstance(raw_plan, dict) or raw_plan.get("schemaVersion") != "1.0":
        return ()
    return tuple(
        TestPlanValidationIssue("schema", "error", f"{_json_schema_error_path(error)}: {error.message}")
        for error in sorted(
            _CANONICAL_TEST_PLAN_SCHEMA_VALIDATOR.iter_errors(raw_plan),
            key=_json_schema_error_sort_key,
        )
    )


def _schema_version_from_raw(raw_plan: object) -> str:
    """Return the schema version string from a raw plan when available.

    Args:
        raw_plan: Decoded JSON value to inspect.

    Returns:
        Schema version string, or ``"unknown"`` when absent or non-string.
    """
    if isinstance(raw_plan, dict):
        schema_version = raw_plan.get("schemaVersion")
        if isinstance(schema_version, str):
            return schema_version
    return "unknown"


def _json_schema_error_path(error: object) -> str:
    """Return a readable JSON path for a jsonschema validation error.

    Args:
        error: Validation error object from ``jsonschema``.

    Returns:
        Dot/bracket path, or ``"testPlan"`` for root-level errors.
    """
    path = getattr(error, "absolute_path", getattr(error, "path", ()))
    segments = list(path)
    if not segments:
        return "testPlan"
    rendered = "testPlan"
    for segment in segments:
        rendered += f"[{segment}]" if isinstance(segment, int) else f".{segment}"
    return rendered


def _json_schema_error_sort_key(error: object) -> tuple[str, str, str]:
    """Return a stable sort key for JSON Schema validation errors.

    Args:
        error: Validation error object from ``jsonschema``.

    Returns:
        Tuple built from rendered instance path, schema path, and message.
    """
    path = ".".join(str(segment) for segment in getattr(error, "absolute_path", ()))
    schema_path = ".".join(str(segment) for segment in getattr(error, "absolute_schema_path", ()))
    message = getattr(error, "message", "")
    return (path, schema_path, message if isinstance(message, str) else "")


def _execution_mode_issues(document: PlanDocumentV2) -> tuple[TestPlanValidationIssue, ...]:
    """Return validation issues implied solely by execution mode.

    Args:
        document: Parsed test-plan document.

    Returns:
        Execution-mode validation warnings.
    """
    if document.execution_mode != "development":
        return ()
    return (
        TestPlanValidationIssue(
            "execution",
            "warning",
            "Development-mode runs are marked as non-certification evidence.",
        ),
    )


def _compiled_plan_issues(
    document: PlanDocumentV2,
    compiled_plan: CompiledTestPlan,
) -> tuple[TestPlanValidationIssue, ...]:
    """Return validation issues derived from a compiled plan.

    Args:
        document: Parsed test-plan document.
        compiled_plan: Compiled catalogue plan.

    Returns:
        Certification/development validation issues.
    """
    issues: list[TestPlanValidationIssue] = []
    severity: ValidationSeverity = "warning" if document.execution_mode == "development" else "error"
    for reason in compiled_plan.traceability.non_certifying_reasons:
        issues.append(TestPlanValidationIssue("semantic", severity, reason))
    if not compiled_plan.test_cases:
        issues.append(TestPlanValidationIssue("semantic", "error", "Test plan does not select any executable tests."))
    return tuple(issues)


def _catalogue_error_layer(message: str) -> ValidationLayer:
    """Classify a catalogue compiler error into a validation layer.

    Args:
        message: Catalogue error message.

    Returns:
        Best-effort validation layer for the error.
    """
    normalized = message.lower()
    if "runtime input" in normalized:
        return "business"
    if "security profile" in normalized or "clientauthmethod" in normalized:
        return "security"
    if "schemaversion" in normalized:
        return "schema"
    return "semantic"


def _safe_json_object(value: Mapping[str, JsonValue], sensitive_input_ids: set[str]) -> JsonObject:
    """Return a recursively redacted copy of a JSON object.

    Args:
        value: JSON object to redact.
        sensitive_input_ids: Runtime input ids known to hold sensitive values.

    Returns:
        Redacted JSON object safe for persisted result evidence.
    """
    return {key: _safe_json_value(key, item, sensitive_input_ids) for key, item in value.items()}


def _safe_json_value(key: str, value: JsonValue, sensitive_input_ids: set[str]) -> JsonValue:
    """Return one redacted JSON value.

    Args:
        key: Field key associated with ``value``.
        value: JSON value to redact.
        sensitive_input_ids: Runtime input ids known to hold sensitive values.

    Returns:
        Redacted JSON value.
    """
    if key in sensitive_input_ids or _is_sensitive_key(key):
        if isinstance(value, dict) and "value" in value:
            redacted = _safe_json_object(value, sensitive_input_ids)
            redacted["value"] = ""
            return redacted
        if isinstance(value, str):
            return ""
    if isinstance(value, dict):
        return _safe_json_object(value, sensitive_input_ids)
    if isinstance(value, list):
        return [_safe_json_value(key, item, sensitive_input_ids) for item in value]
    return value


def _is_sensitive_key(key: str) -> bool:
    """Return whether a JSON key conventionally carries sensitive material.

    Args:
        key: JSON field name.

    Returns:
        True when the field should be redacted from persisted plan snapshots.
    """
    normalized = key.replace("-", "").replace("_", "").lower()
    return (
        normalized.endswith("token")
        or "secret" in normalized
        or "password" in normalized
        or "privatekey" in normalized
        or normalized
        in {
            "accountid",
            "accountids",
            "identification",
            "xfapifinancialid",
        }
    )
