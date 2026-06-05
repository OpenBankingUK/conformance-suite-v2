"""Shared run lifecycle helpers for API and browser-triggered runs.

This module owns the process-local lifecycle for starting a conformance run:
reserve the single active run slot, snapshot the initial pending response,
start the background thread, and transition terminal runs while cleaning up
run-scoped PSU authorization sessions.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Mapping

from conformance.api.auth_session_store import auth_session_store
from conformance.api.run_store import RunStore, run_store
from conformance.context import RuntimeConfig
from conformance.execution_log import EventType, ExecutionLogger, NullExecutionLogger, warn_if_developer_mode
from conformance.executor import run_manifest
from conformance.http import build_json_http_client
from conformance.json_types import JsonObject, JsonValue
from conformance.manifest import Manifest
from conformance.model_bank_config import ModelBankConfig
from conformance.runner import run_model_bank_smoke_check
from conformance.suite_catalog import SuiteCatalogError, SuiteMetadata, resolve_suite
from conformance.test_plan import TestPlan

logger = logging.getLogger(__name__)


class BrowserParticipantActionLogger(ExecutionLogger):
    """Decorator that mirrors browser-required PSU actions into run state.

    The wrapped logger remains the durable execution-log sink. This API-layer
    decorator observes raw events before the wrapped logger applies masking so
    the browser UI can temporarily render the raw PSU authorisation URL from
    in-memory run state without persisting it to logs or result JSON.
    """

    def __init__(self, wrapped: ExecutionLogger, *, run_id: str, store: RunStore) -> None:
        """Initialise the browser participant action decorator.

        Args:
            wrapped: Execution-log sink that receives every event unchanged.
            run_id: Run identifier whose participant action state is updated.
            store: In-memory run store that holds browser participant actions.
        """
        self._wrapped = wrapped
        self._run_id = run_id
        self._store = store

    def emit(
        self,
        event_type: EventType,
        *,
        step_id: str | None = None,
        payload: Mapping[str, JsonValue] | None = None,
    ) -> None:
        """Forward an event and update browser participant action state.

        Args:
            event_type: Event type from the closed execution-log taxonomy.
            step_id: Optional manifest step identifier associated with the
                event.
            payload: Optional event-specific data. The raw
                ``psu-authorization-url`` payload is inspected before the
                wrapped logger masks it for persistence.
        """
        if event_type == "psu-authorization-url":
            self._set_participant_action(step_id=step_id, payload=payload)
        elif event_type == "step-completed" and step_id is not None:
            self._store.clear_participant_action(self._run_id, step_id=step_id)
        elif event_type in {"auth-callback-received", "run-completed", "application-error"}:
            self._store.clear_participant_action(self._run_id)
        self._wrapped.emit(event_type, step_id=step_id, payload=payload)

    @property
    def run_id(self) -> str:
        """Run identifier whose browser action state is updated."""
        return self._run_id

    def _set_participant_action(
        self,
        *,
        step_id: str | None,
        payload: Mapping[str, JsonValue] | None,
    ) -> None:
        """Store a raw PSU authorisation URL when the event is well formed.

        Args:
            step_id: Manifest step that emitted the PSU URL. Missing step IDs
                are ignored because browser action state is step-scoped.
            payload: Raw event payload containing the ``url`` field before
                masking.
        """
        url = (payload or {}).get("url")
        if step_id is None or not isinstance(url, str):
            return
        self._store.set_participant_action(self._run_id, step_id=step_id, url=url)


def start_run(
    *,
    config: ModelBankConfig,
    manifest: Manifest | None,
    plan: TestPlan | None,
    suite_metadata: SuiteMetadata | None = None,
    browser_psu_prompts: bool = False,
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
        browser_psu_prompts: Whether to mirror raw manual PSU authorisation
            URLs into transient in-memory run state for browser-launched runs.

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
        kwargs={"browser_psu_prompts": browser_psu_prompts},
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
    *,
    browser_psu_prompts: bool = False,
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
        browser_psu_prompts: Whether to wrap the execution logger so raw manual
            PSU authorisation URLs are exposed only as transient browser
            participant actions.
    """
    run_store.mark_running(run_id)
    try:
        run_record = run_store.get_run(run_id)
        # ``get_run`` returns a shallow copy whose ``execution_logger``
        # reference points at the same live buffer the API exposes; either
        # accessing the live record or the copy yields the same logger.
        run_logger = run_record.execution_logger if run_record is not None else None
        logger_sink: ExecutionLogger = run_logger or NullExecutionLogger()
        if browser_psu_prompts:
            logger_sink = BrowserParticipantActionLogger(logger_sink, run_id=run_id, store=run_store)
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
                        oauth_client_id=config.oauth.client_id if config.oauth is not None else None,
                        oauth_redirect_uri=config.oauth.redirect_uri if config.oauth is not None else None,
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
