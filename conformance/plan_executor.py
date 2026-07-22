"""Run plan executor: bridges RunPlanV2 to plugin-specific execution paths.

This module provides the bridge from an intent-based
:class:`~conformance.run_plan_v2.RunPlanV2` to the appropriate plugin-specific
execution engine, and exposes a module-level shared plugin registry.

For catalogue-native targets: compiles RunPlanV2 intent into a plugin catalogue
dependency graph, with shared prerequisite de-duplication and resource-group
isolation.

For DCR targets: builds and executes a
:class:`~conformance.plugins.dcr.runner.DcrRunner` directly from the validated
credential/transport config in :class:`~conformance.model_bank_config.DcrConfig`.

For Read/Write targets still entering the legacy CLI/API execution path, the
temporary compatibility resolver maps to a suite manifest.  The participant
RunPlanV2-to-catalogue path lives in :func:`compile_catalogue_graph_for_plan`;
CLI/API convergence is handled separately.

Security note: credential loading happens immediately before runner construction;
no credential material is stored between function calls.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

from conformance.catalogue_execution import CompiledCatalogueRun, compile_catalogue_run_plan
from conformance.dcr.credentials import load_dcr_credentials
from conformance.execution_log import ExecutionLogger
from conformance.json_types import JsonObject, JsonValue
from conformance.manifest import Manifest
from conformance.masking import mask_headers
from conformance.model_bank_config import ConfigError, DcrConfig
from conformance.plugins.dcr.client_state import DcrStepEvidence
from conformance.plugins.dcr.plugin import DcrPlugin
from conformance.plugins.dcr.runner import DcrRunner, DcrRunResult
from conformance.plugins.read_write.plugin import ReadWritePlugin
from conformance.plugins.registry import PluginRegistry, PluginRegistryError
from conformance.results import (
    ResourceGroupExecutionSummary,
    RunReadinessReport,
    SmokeCheckResult,
    build_dcr_readiness_status,
    build_readiness_report_from_compiled_run,
    serialise_readiness_report,
)
from conformance.run_plan_v2 import RunPlanV2, RunPlanV2TargetCoordinates
from conformance.suite_catalog import (
    SuiteApiFamily,
    SuiteCatalogError,
    SuiteMetadata,
    SuiteName,
    SuiteProfile,
    SuiteSelection,
    SuiteSpecVersion,
    SuiteStandard,
    resolve_suite,
)
from conformance.target_config import TestTargetConfig

if TYPE_CHECKING:
    from conformance.model_bank_config import ModelBankConfig

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level registry singleton
# ---------------------------------------------------------------------------

_registry: PluginRegistry | None = None
"""Lazily-initialised shared plugin registry returned by :func:`build_default_registry`."""


def build_default_registry() -> PluginRegistry:
    """Return the shared singleton plugin registry with all built-in plugins registered.

    Registers :class:`~conformance.plugins.read_write.plugin.ReadWritePlugin`
    and :class:`~conformance.plugins.dcr.plugin.DcrPlugin` on first call and
    returns the cached instance on subsequent calls.  Thread safety: the
    assignment is idempotent under CPython's GIL; for strict thread safety,
    callers should initialise the registry at process startup before launching
    worker threads.

    Returns:
        The singleton :class:`~conformance.plugins.registry.PluginRegistry`
        with Read/Write and DCR plugins registered.
    """
    global _registry  # noqa: PLW0603
    if _registry is None:
        registry = PluginRegistry()
        registry.register(ReadWritePlugin())
        registry.register(DcrPlugin())
        _registry = registry
    return _registry


# ---------------------------------------------------------------------------
# Catalogue drift detection
# ---------------------------------------------------------------------------

_DCR_ENDPOINT_IDS_ADVERTISE_GET = frozenset({"dcr.register.get"})
"""Endpoint IDs in DCR catalogues that indicate GET /register/{clientId} support."""

_DCR_ENDPOINT_IDS_ADVERTISE_PUT = frozenset({"dcr.register.put"})
"""Endpoint IDs in DCR catalogues that indicate PUT /register/{clientId} support."""

_DCR_ENDPOINT_IDS_ADVERTISE_DELETE = frozenset({"dcr.register.delete"})
"""Endpoint IDs in DCR catalogues that indicate DELETE /register/{clientId} support."""


_RW_SUITE_MAP: dict[
    tuple[str, str],
    tuple[SuiteStandard, SuiteSpecVersion, SuiteProfile, SuiteApiFamily, SuiteName],
] = {
    ("v4.0.1", "ais"): ("ob-read-write", "v4.0.1", "fapi1-advanced", "ais", "ais-certification-baseline"),
    ("v4.0.1", "pis"): ("ob-read-write", "v4.0.1", "fapi1-advanced", "pis", "psu-auth-starter"),
    ("v4.0.1", "cbpii"): ("ob-read-write", "v4.0.1", "fapi1-advanced", "cbpii", "psu-auth-starter"),
    ("v4.0.1", "vrp"): ("ob-read-write", "v4.0.1", "fapi1-advanced", "vrp", "psu-auth-starter"),
    ("v4.0", "ais"): ("ob-read-write", "v4.0", "fapi1-advanced", "ais", "ais-certification-baseline"),
    ("v4.0", "pis"): ("ob-read-write", "v4.0", "fapi1-advanced", "pis", "pis-domestic-payment-starter"),
    ("v4.0", "cbpii"): ("ob-read-write", "v4.0", "fapi1-advanced", "cbpii", "psu-auth-starter"),
    ("v4.0", "vrp"): ("ob-read-write", "v4.0", "fapi1-advanced", "vrp", "psu-auth-starter"),
    ("v3.1.11", "ais"): ("ob-read-write", "v3.1.11", "fapi1-advanced", "ais", "psu-auth-starter"),
}
"""Mapping from (specification_version, resource_group) to suite catalog selection tuple."""


def check_catalogue_drift(plan: RunPlanV2, *, registry: PluginRegistry | None = None) -> str | None:
    """Check whether the stored catalogue hash matches the live catalogue.

    Resolves the plugin for the plan's target coordinates, computes the live
    catalogue hash, and compares it with the hash stored in
    :attr:`~conformance.run_plan_v2.RunPlanV2TargetCoordinates.catalogue_hash`.

    A missing or empty ``catalogue_hash`` (e.g. in a plan built from a
    :class:`~conformance.target_config.TestTargetConfig` without full hash
    computation) is treated as "no hash to compare" and returns ``None``
    without raising.

    Args:
        plan: The :class:`~conformance.run_plan_v2.RunPlanV2` whose
            ``target.catalogue_hash`` to validate.
        registry: Plugin registry to use.  Defaults to the module-level
            singleton from :func:`build_default_registry` when ``None``.

    Returns:
        A human-readable drift warning string when the hashes differ, or
        ``None`` when the hashes match or no stored hash is available.
    """
    stored_hash = plan.target.catalogue_hash
    if not stored_hash or stored_hash == "sha256:unknown":
        return None
    effective_registry = registry or build_default_registry()
    from conformance.target_config import SecurityProfile, Specification, Standard

    target = TestTargetConfig(
        standard=cast("Standard", plan.target.standard),
        specification=cast("Specification", plan.target.specification),
        security_profile=cast("SecurityProfile", plan.target.security_profile),
        specification_version=plan.target.specification_version,
    )
    try:
        plugin = effective_registry.resolve(target)
    except PluginRegistryError:
        return None
    live_identity = plugin.catalogue_identity(target)
    if live_identity.content_hash != stored_hash:
        return (
            f"Catalogue drift detected: plan was authored against catalogue hash "
            f"{stored_hash!r} but the current catalogue hash is "
            f"{live_identity.content_hash!r}. Re-open the plan builder to update."
        )
    return None


def require_no_catalogue_drift(plan: RunPlanV2, *, registry: PluginRegistry | None = None) -> None:
    """Require a stored catalogue hash to match the current bundled catalogue.

    Args:
        plan: The :class:`~conformance.run_plan_v2.RunPlanV2` whose stored
            catalogue hash should be enforced.
        registry: Plugin registry to use. Defaults to the module-level
            singleton from :func:`build_default_registry` when ``None``.

    Raises:
        ValueError: If the plan stores a catalogue hash that differs from the
            current bundled catalogue hash.
    """
    drift = check_catalogue_drift(plan, registry=registry)
    if drift is not None:
        raise ValueError(drift)


# ---------------------------------------------------------------------------
# DCR execution
# ---------------------------------------------------------------------------


def compile_catalogue_graph_for_plan(
    plan: RunPlanV2,
    *,
    registry: PluginRegistry | None = None,
) -> CompiledCatalogueRun:
    """Compile a RunPlanV2 through the catalogue-native planner.

    Args:
        plan: Intent-based RunPlanV2 to compile.
        registry: Plugin registry to use.  Defaults to the module-level
            singleton from :func:`build_default_registry` when ``None``.

    Returns:
        A :class:`conformance.catalogue_execution.CompiledCatalogueRun`
        containing the executable dependency graph.
    """
    effective_registry = registry or build_default_registry()
    return compile_catalogue_run_plan(plan, registry=effective_registry)


def readiness_report_for_smoke_result(
    compiled_run: CompiledCatalogueRun,
    result: SmokeCheckResult,
    *,
    run_id: str,
) -> RunReadinessReport:
    """Build catalogue readiness reporting for a bridged manifest result.

    Args:
        compiled_run: Catalogue-native graph used to validate the target run.
        result: Manifest-bridge execution result produced for the run.
        run_id: Run identifier to include in the readiness report.

    Returns:
        Readiness report derived from the compiled catalogue coverage and
        selected manifest execution outcomes.
    """
    return build_readiness_report_from_compiled_run(
        compiled_run,
        run_id=run_id,
        generated_at=result.finished_at,
        execution_summary_by_resource_group=_bridge_execution_summary_by_resource_group(compiled_run, result),
    )


def _bridge_execution_summary_by_resource_group(
    compiled_run: CompiledCatalogueRun,
    result: SmokeCheckResult,
) -> dict[str, ResourceGroupExecutionSummary]:
    """Summarise temporary Read/Write bridge outcomes by resource group.

    The current Read/Write runtime bridge resolves one resource group to one
    bundled manifest. Until the catalogue-native primitive runtime replaces
    that bridge, all manifest step outcomes are attributed to that selected
    resource group so readiness can still be generated from catalogue policy.

    Args:
        compiled_run: Catalogue graph validated before manifest resolution.
        result: Manifest execution result to summarise.

    Returns:
        Per-resource-group execution counters, or an empty mapping when the
        compiled graph has no resource-group scope.
    """
    if not compiled_run.selected_resource_groups:
        return {}
    resource_group = compiled_run.selected_resource_groups[0]
    return {
        resource_group: ResourceGroupExecutionSummary(
            selected_test_count=len(result.steps),
            passed_count=sum(1 for step in result.steps if step.status == "passed"),
            failed_count=sum(1 for step in result.steps if step.status == "failed"),
            skipped_count=sum(1 for step in result.steps if step.status == "skipped"),
        )
    }


def run_plan_from_test_target(
    target: TestTargetConfig,
    *,
    registry: PluginRegistry | None = None,
) -> RunPlanV2:
    """Build a RunPlanV2 from config ``testTarget`` using live catalogue identity.

    Args:
        target: Parsed participant target coordinates from config
            ``testTarget``.
        registry: Plugin registry to use. Defaults to the module-level
            singleton from :func:`build_default_registry` when ``None``.

    Returns:
        A RunPlanV2 with canonical catalogue coordinates, selected resource
        groups from ``target``, no explicit endpoint selections, and the live
        catalogue hash.

    Raises:
        PluginRegistryError: If no plugin supports ``target``.
        ValueError: If the plugin cannot load catalogue identity for the
            requested version.
    """
    effective_registry = registry or build_default_registry()
    plugin = effective_registry.resolve(target)
    identity = plugin.catalogue_identity(target)
    return RunPlanV2(
        schema_version="2",
        target=RunPlanV2TargetCoordinates(
            standard=identity.standard or target.standard,
            specification=identity.specification,
            security_profile=identity.security_profile or target.security_profile,
            specification_version=identity.specification_version,
            catalogue_hash=identity.content_hash,
        ),
        resource_groups=target.resource_groups,
        endpoint_selections=(),
    )


def execute_dcr_run(plan: RunPlanV2, config: ModelBankConfig) -> DcrRunResult:
    """Execute a DCR conformance run from a RunPlanV2 and model-bank config.

    Validates that the config supplies a ``dcr`` section, derives which DCR
    operations are advertised from the plan's endpoint selections, loads
    credentials from disk, and runs all applicable DCR scenarios.

    The ``advertise_get``, ``advertise_put``, and ``advertise_delete`` flags
    are set to ``True`` when the corresponding endpoint ID appears as a
    selected entry in ``plan.endpoint_selections``, or when no endpoint
    selections are present (all operations advertised by default).

    Args:
        plan: The intent-based DCR run plan describing target coordinates and
            selected operations.
        config: Validated model-bank configuration.  Must supply a ``dcr``
            section; raises :class:`~conformance.model_bank_config.ConfigError`
            when absent.

    Returns:
        A :class:`~conformance.plugins.dcr.runner.DcrRunResult` with scenario
        outcomes, discovery result, and cleanup status.

    Raises:
        ConfigError: If ``config.dcr`` is ``None``.
    """
    dcr_config: DcrConfig | None = config.dcr
    if dcr_config is None:
        raise ConfigError(
            "A 'dcr' section is required in the config for DCR runs; "
            "supply dcr.ssaPath, dcr.signingPrivateKeyPath, dcr.signingCertificatePath, "
            "dcr.transportCertificatePath, and dcr.transportPrivateKeyPath."
        )

    selected_ids = {sel.endpoint_id for sel in plan.endpoint_selections if sel.selected}
    all_selected = not plan.endpoint_selections

    advertise_get = all_selected or bool(selected_ids & _DCR_ENDPOINT_IDS_ADVERTISE_GET)
    advertise_put = all_selected or bool(selected_ids & _DCR_ENDPOINT_IDS_ADVERTISE_PUT)
    advertise_delete = all_selected or bool(selected_ids & _DCR_ENDPOINT_IDS_ADVERTISE_DELETE)

    credentials = load_dcr_credentials(dcr_config.credential_paths)

    runner = DcrRunner(
        credential_paths=dcr_config.credential_paths,
        credentials=credentials,
        transport_config=dcr_config.transport,
        issuer_url=config.discovery_url,
        dcr_version=plan.target.specification_version,
        advertise_get=advertise_get,
        advertise_put=advertise_put,
        advertise_delete=advertise_delete,
    )
    return runner.run()


def dcr_run_result_to_json_object(
    dcr_result: DcrRunResult,
    *,
    plan: RunPlanV2,
    environment: str | None,
    run_id: str,
    started_at: datetime,
    finished_at: datetime,
    compiled_run: CompiledCatalogueRun | None = None,
) -> JsonObject:
    """Convert a :class:`DcrRunResult` into the public result-file JSON shape.

    Builds a report with the standard ``status``/``summary`` envelope used by
    manifest runs and a ``readiness.dcr`` block containing per-scenario
    outcomes and the fixed non-certifying policy fields.

    Args:
        dcr_result: The completed DCR run result to serialise.
        plan: The plan that drove the run; its target coordinates are echoed
            back in the report.
        environment: Optional legacy environment label copied from the config.
        run_id: The run identifier assigned by the caller.
        started_at: UTC timestamp when execution started.
        finished_at: UTC timestamp when execution finished.
        compiled_run: Optional catalogue-native graph used to derive the
            endpoint-first readiness report.

    Returns:
        A JSON-serialisable dict suitable for writing to ``resultOutputPath``.
    """
    passed = sum(1 for r in dcr_result.scenario_results if r.outcome == "passed")
    failed = sum(1 for r in dcr_result.scenario_results if r.outcome == "failed")
    skipped = sum(1 for r in dcr_result.scenario_results if r.outcome == "skipped")
    total = len(dcr_result.scenario_results)

    status = "failed" if failed > 0 or total == 0 or (passed == 0 and skipped == total) else "passed"

    scenarios: list[JsonValue] = []
    for scenario in dcr_result.scenario_results:
        entry: JsonObject = {
            "scenarioId": scenario.scenario_id,
            "outcome": scenario.outcome,
            "assertionDetail": scenario.assertion_detail,
        }
        if scenario.evidence is not None:
            entry["evidence"] = {
                "requestUrl": scenario.evidence.request_url,
                "requestMethod": scenario.evidence.request_method,
                "requestContentType": scenario.evidence.request_content_type,
                "requestHeaders": dict(scenario.evidence.request_headers_masked),
                "responseStatus": scenario.evidence.response_status,
                "responseHeaders": dict(scenario.evidence.response_headers_masked),
                "responseBody": dict(scenario.evidence.response_body_masked),
            }
        scenarios.append(entry)

    body: JsonObject = {
        "status": status,
        "runId": run_id,
        "startedAt": started_at.isoformat(),
        "finishedAt": finished_at.isoformat(),
        "target": {
            "standard": plan.target.standard,
            "specification": plan.target.specification,
            "securityProfile": plan.target.security_profile,
            "specificationVersion": plan.target.specification_version,
        },
        "summary": {
            "total": total,
            "passed": passed,
            "failed": failed,
            "skipped": skipped,
        },
        "readiness": {
            "dcr": {
                "certifying": False,
                "certifyingBlockedReason": "No DCR certification policy exists for this tool",
                "passedCount": passed,
                "failedCount": failed,
                "skippedCount": skipped,
            }
        },
        "cleanup": {
            "attempted": dcr_result.cleanup_attempted,
            "succeeded": dcr_result.cleanup_succeeded,
            "detail": dcr_result.cleanup_detail,
        },
        "scenarios": scenarios,
    }
    if environment is not None:
        body["environment"] = environment
    if dcr_result.discovery is not None:
        body["discovery"] = {
            "issuer": dcr_result.discovery.issuer,
            "registrationEndpoint": dcr_result.discovery.registration_endpoint,
            "tokenEndpoint": dcr_result.discovery.token_endpoint,
            "selectedAuthMethod": dcr_result.discovery.selected_auth_method,
        }
    if compiled_run is not None:
        readiness_report = build_readiness_report_from_compiled_run(
            compiled_run,
            run_id=run_id,
            generated_at=finished_at,
            dcr_status=build_dcr_readiness_status(
                passed_count=passed,
                failed_count=failed,
                skipped_count=skipped,
            ),
        )
        body["readinessReport"] = serialise_readiness_report(readiness_report)
    return body


def emit_dcr_execution_log(dcr_result: DcrRunResult, *, execution_logger: ExecutionLogger) -> None:
    """Emit masked DCR run events to the shared execution-log pipeline.

    Args:
        dcr_result: Completed DCR run result to log.
        execution_logger: Logger sink that applies the standard masking rules.
    """
    summary = _dcr_result_counts(dcr_result)
    execution_logger.emit(
        "run-started",
        payload={
            "specification": "dynamic-client-registration",
            "scenarioCount": summary["total"],
        },
    )
    for scenario in dcr_result.scenario_results:
        payload: JsonObject = {
            "scenarioId": scenario.scenario_id,
            "outcome": scenario.outcome,
            "assertionDetail": scenario.assertion_detail,
        }
        if scenario.evidence is not None:
            payload["evidence"] = _dcr_evidence_to_json_object(scenario.evidence)
        execution_logger.emit("step-completed", step_id=scenario.scenario_id, payload=payload)
    execution_logger.emit(
        "run-completed",
        payload={
            "specification": "dynamic-client-registration",
            "summary": summary,
            "cleanup": {
                "attempted": dcr_result.cleanup_attempted,
                "succeeded": dcr_result.cleanup_succeeded,
                "detail": dcr_result.cleanup_detail,
            },
        },
    )


def _dcr_result_counts(dcr_result: DcrRunResult) -> JsonObject:
    """Count DCR scenario outcomes for result and log summaries.

    Args:
        dcr_result: DCR run result to summarise.

    Returns:
        JSON object containing total, passed, failed, and skipped counts.
    """
    passed = sum(1 for r in dcr_result.scenario_results if r.outcome == "passed")
    failed = sum(1 for r in dcr_result.scenario_results if r.outcome == "failed")
    skipped = sum(1 for r in dcr_result.scenario_results if r.outcome == "skipped")
    total = len(dcr_result.scenario_results)
    return {
        "total": total,
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
    }


def _dcr_evidence_to_json_object(evidence: DcrStepEvidence) -> JsonObject:
    """Convert DCR scenario evidence into the public JSON evidence shape.

    Args:
        evidence: DCR evidence object attached to a scenario result.

    Returns:
        JSON object containing request and response evidence.
    """
    return {
        "requestUrl": evidence.request_url,
        "requestMethod": evidence.request_method,
        "requestContentType": evidence.request_content_type,
        "requestHeaders": cast("JsonObject", mask_headers(evidence.request_headers_masked)),
        "responseStatus": evidence.response_status,
        "responseHeaders": cast("JsonObject", mask_headers(evidence.response_headers_masked)),
        "responseBody": dict(evidence.response_body_masked),
    }


# ---------------------------------------------------------------------------
# Read/Write suite resolution
# ---------------------------------------------------------------------------


def resolve_rw_suite_for_plan(plan: RunPlanV2) -> tuple[Manifest, SuiteMetadata]:
    """Resolve the best-match Read/Write suite manifest for a RunPlanV2.

    Maps the plan's specification version and first selected resource group to
    a bundled suite manifest via the internal lookup table, then resolves the
    manifest from the suite catalog.

    Multi-resource-group plans are not yet supported in a single manifest run.
    Callers with multiple resource groups must submit one resource group per
    run.

    Args:
        plan: The Read/Write run plan to resolve a suite for.  Must have at
            least one entry in ``resource_groups``.

    Returns:
        A tuple of ``(manifest, suite_metadata)`` for the resolved bundled
        suite.

    Raises:
        ValueError: If ``plan.resource_groups`` is empty, if the plan specifies
            multiple resource groups (multi-RG runs must be submitted one at a
            time), or if no suite mapping exists for the version/resource-group
            combination.
        SuiteCatalogError: If suite resolution fails (manifest not found).
    """
    if not plan.resource_groups:
        raise ValueError("Read/Write run plans must specify at least one resource group in 'resourceGroups'.")
    if len(plan.resource_groups) > 1:
        raise ValueError(
            "Multi-resource-group plans must be submitted one resource group per run. "
            f"Received: {list(plan.resource_groups)!r}. "
            "Submit separate runs for each resource group."
        )
    resource_group = plan.resource_groups[0]
    version = plan.target.specification_version

    key = (version, resource_group)
    suite_coords = _RW_SUITE_MAP.get(key)
    if suite_coords is None:
        supported = sorted(_RW_SUITE_MAP.keys())
        raise ValueError(
            f"No bundled suite available for Read/Write target "
            f"(version={version!r}, resourceGroup={resource_group!r}). "
            f"Supported combinations: {supported}"
        )

    standard, spec_version, profile, api, suite_name = suite_coords
    selection = SuiteSelection(
        standard=standard,
        spec_version=spec_version,
        profile=profile,
        api=api,
        suite=suite_name,
    )
    try:
        resolved = resolve_suite(selection)
    except SuiteCatalogError:
        raise
    return resolved.manifest, resolved.metadata


def utc_now() -> datetime:
    """Return the current UTC-aware timestamp.

    Small indirection introduced so tests can patch clock behaviour without
    monkey-patching :mod:`datetime` module state directly.

    Returns:
        Current time as a timezone-aware :class:`~datetime.datetime` in UTC.
    """
    return datetime.now(UTC)
