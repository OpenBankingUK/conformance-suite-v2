"""Shared run lifecycle helpers for API and browser-triggered runs.

This module owns the process-local lifecycle for starting a conformance run:
reserve the single active run slot, snapshot the initial pending response,
start the background thread, and transition terminal runs while cleaning up
run-scoped PSU authorization sessions.
"""

from __future__ import annotations

import logging
import threading

from conformance.api.auth_session_store import auth_session_store
from conformance.api.run_store import run_store
from conformance.context import RuntimeConfig
from conformance.execution_log import NullExecutionLogger, warn_if_developer_mode
from conformance.executor import run_manifest
from conformance.http import build_json_http_client
from conformance.json_types import JsonObject
from conformance.manifest import Manifest
from conformance.model_bank_config import ModelBankConfig
from conformance.runner import run_model_bank_smoke_check
from conformance.suite_catalog import SuiteCatalogError, SuiteMetadata, resolve_suite
from conformance.test_plan import TestPlan

logger = logging.getLogger(__name__)


def start_run(
    *,
    config: ModelBankConfig,
    manifest: Manifest | None,
    plan: TestPlan | None,
    suite_metadata: SuiteMetadata | None = None,
) -> JsonObject:
    """Reserve a run slot and start asynchronous conformance execution.

    Args:
        config: Validated model-bank configuration.
        manifest: Parsed manifest object, or ``None`` to resolve a suite from
            ``config.test_suite`` or fall back to a smoke-check run.
        plan: Optional :class:`TestPlan` derived from ``manifest`` with any
            caller-supplied deselections already applied. When ``manifest`` is
            ``None`` but ``config.test_suite`` is present, ``None`` selects the
            suite manifest's default plan.
        suite_metadata: Optional catalog metadata when ``manifest`` came from
            config-driven suite resolution.

    Returns:
        Initial public run-status JSON captured while the record is still in
        ``pending`` state.

    Raises:
        RunConflictError: If another run is already pending or running.
    """
    record = run_store.create_run()
    warn_if_developer_mode()
    thread = threading.Thread(
        target=_execute_run,
        args=(record.run_id, config, manifest, plan, suite_metadata),
        daemon=True,
    )
    initial_status = record.to_status_json()
    thread.start()
    return initial_status


def _execute_run(
    run_id: str,
    config: ModelBankConfig,
    manifest: Manifest | None,
    plan: TestPlan | None,
    suite_metadata: SuiteMetadata | None = None,
) -> None:
    """Execute a conformance run in a background thread.

    Transitions the run record through running -> completed/failed.

    Args:
        run_id: The run identifier to update in the store.
        config: Validated model-bank configuration.
        manifest: Parsed manifest object, or ``None`` to resolve a suite from
            config or run the legacy smoke check.
        plan: Optional :class:`TestPlan` derived from ``manifest`` with any
            caller-supplied deselections already applied. When ``manifest`` is
            ``None`` but ``config.test_suite`` is present, ``None`` selects the
            suite manifest's default plan.
        suite_metadata: Optional catalog metadata when ``manifest`` came from
            config-driven suite resolution.
    """
    run_store.mark_running(run_id)
    try:
        run_record = run_store.get_run(run_id)
        # ``get_run`` returns a shallow copy whose ``execution_logger``
        # reference points at the same live buffer the API exposes; either
        # accessing the live record or the copy yields the same logger.
        logger_sink = (run_record.execution_logger if run_record is not None else None) or NullExecutionLogger()
        effective_manifest = manifest
        effective_plan = plan
        effective_suite_metadata = suite_metadata
        if effective_manifest is None and config.test_suite is not None:
            try:
                resolved_suite = resolve_suite(config.test_suite)
            except SuiteCatalogError as error:
                logger.error("Suite resolution failed for run %s: %s", run_id, error)
                run_store.mark_failed(run_id, error=f"Suite resolution failed: {error}")
                return
            effective_manifest = resolved_suite.manifest
            effective_suite_metadata = resolved_suite.metadata

        if effective_manifest is None:
            result = run_model_bank_smoke_check(config, execution_logger=logger_sink)
        else:
            if effective_plan is None:
                effective_plan = TestPlan.default_plan_from_manifest(effective_manifest)
            http_client = build_json_http_client(
                timeout_seconds=config.timeout_seconds,
                ca_bundle_path=config.tls.ca_bundle_path,
                client_certificate_path=config.tls.client_certificate_path,
                client_private_key_path=config.tls.client_private_key_path,
            )
            try:
                result = run_manifest(
                    effective_manifest,
                    environment=config.environment,
                    client=http_client,
                    execution_logger=logger_sink,
                    plan=effective_plan,
                    run_id=run_id,
                    auth_session_store=auth_session_store,
                    runtime_config=RuntimeConfig(
                        discovery_url=config.discovery_url,
                        environment=config.environment,
                    ),
                    suite_metadata=effective_suite_metadata,
                    approved_release_policy=config.approved_release_policy,
                )
            finally:
                http_client.close()

        run_store.mark_completed(run_id, result=result.to_json_object())
    except Exception:
        logger.exception("Run %s failed with an internal error", run_id)
        run_store.mark_failed(run_id, error="An internal error occurred")
    finally:
        # Awaiting auth sessions must not outlive their parent run; drop
        # them on terminal transition so a subsequent run cannot inherit
        # stale state. Done in ``finally`` to cover both happy-path and
        # failure-path exits from the run.
        auth_session_store.discard_for_run(run_id)
