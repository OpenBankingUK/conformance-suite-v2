"""Structured diagnostics shared by versioned configuration contracts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class DiagnosticSeverity(StrEnum):
    """Severity of a configuration diagnostic."""

    ERROR = "error"
    WARNING = "warning"


class DiagnosticCode(StrEnum):
    """Stable machine-readable configuration diagnostic codes."""

    IO_READ_FAILED = "config.io.read-failed"
    JSON_INVALID = "config.json.invalid"
    SCHEMA_VERSION_UNSUPPORTED = "config.schema.version-unsupported"
    SCHEMA_DEFINITION_INVALID = "config.schema.definition-invalid"
    SCHEMA_REFERENCE_UNRESOLVED = "config.schema.reference-unresolved"
    SCHEMA_VALIDATION_FAILED = "config.schema.validation-failed"
    DUPLICATE_ID = "config.semantic.duplicate-id"
    DEPENDENCY_CYCLE = "config.semantic.dependency-cycle"
    RULE_INCONSISTENT = "config.semantic.rule-inconsistent"
    RESOLVED_PLAN_INCONSISTENT = "config.semantic.resolved-plan-inconsistent"
    EXECUTION_MANIFEST_INCONSISTENT = "config.semantic.execution-manifest-inconsistent"
    SUITE_RELEASE_SELF_REFERENCE = "config.semantic.suite-release-self-reference"
    REFERENCE_UNRESOLVED = "config.reference.unresolved"
    ARTIFACT_UNRESOLVED = "config.reference.artifact-unresolved"
    ARTIFACT_DIGEST_MISMATCH = "config.integrity.artifact-digest-mismatch"


@dataclass(frozen=True, slots=True)
class ConfigurationDiagnostic:
    """One deterministic configuration finding.

    ``instance_path`` and ``schema_path`` are RFC 6901 JSON Pointers. The empty
    string identifies the document root.
    """

    code: DiagnosticCode
    severity: DiagnosticSeverity
    message: str
    instance_path: str
    schema_path: str | None = None


class ConfigurationContractError(ValueError):
    """Raised when configuration cannot be loaded into an immutable model."""

    def __init__(self, diagnostics: tuple[ConfigurationDiagnostic, ...]) -> None:
        """Create an error carrying one or more structured diagnostics."""
        if not diagnostics:
            raise ValueError("ConfigurationContractError requires at least one diagnostic")
        self.diagnostics = diagnostics
        summary = "; ".join(
            f"{diagnostic.code} at {diagnostic.instance_path or '<root>'}: {diagnostic.message}"
            for diagnostic in diagnostics
        )
        super().__init__(summary)
