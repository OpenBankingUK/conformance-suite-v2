"""Versioned configuration contracts for the replacement plan architecture."""

from conformance.configuration_contracts.diagnostics import (
    ConfigurationContractError,
    ConfigurationDiagnostic,
    DiagnosticCode,
    DiagnosticSeverity,
)
from conformance.configuration_contracts.loader import (
    SUITE_RELEASE_SCHEMA_VERSION,
    dump_suite_release,
    load_suite_release,
    parse_suite_release,
    suite_release_to_document,
    validate_bundled_schemas,
    verify_suite_release_artifacts,
)
from conformance.configuration_contracts.models import (
    ArtifactReference,
    Sha256Digest,
    StableId,
    SuiteRelease,
    ToolRelease,
)

__all__ = [
    "ArtifactReference",
    "ConfigurationContractError",
    "ConfigurationDiagnostic",
    "DiagnosticCode",
    "DiagnosticSeverity",
    "SUITE_RELEASE_SCHEMA_VERSION",
    "Sha256Digest",
    "StableId",
    "SuiteRelease",
    "ToolRelease",
    "dump_suite_release",
    "load_suite_release",
    "parse_suite_release",
    "suite_release_to_document",
    "validate_bundled_schemas",
    "verify_suite_release_artifacts",
]
