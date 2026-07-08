"""Shared run lifecycle helpers for API and browser-triggered runs.

This module owns the process-local lifecycle for starting a conformance run:
reserve the single active run slot, snapshot the initial pending response,
start the background thread, and transition terminal runs while cleaning up
run-scoped PSU authorization sessions.
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Mapping
from datetime import UTC, datetime
from types import MappingProxyType

from conformance.api.auth_session_store import auth_session_store
from conformance.api.run_store import RunPlanStep, RunStore, run_store
from conformance.context import (
    RuntimeConfig,
    build_runtime_test_values,
    validate_test_value_config_contract,
)
from conformance.execution_log import (
    BufferedExecutionLogger,
    EventType,
    ExecutionLogger,
    NullExecutionLogger,
    warn_if_developer_mode,
)
from conformance.executor import run_manifest
from conformance.http import build_json_http_client
from conformance.json_types import JsonObject, JsonValue
from conformance.manifest import Manifest, PsuAuthorizationStep, V1Step
from conformance.model_bank_config import ConfigError, ModelBankConfig, TestDataConfig
from conformance.plan_executor import dcr_run_result_to_json_object, execute_dcr_run, utc_now
from conformance.run_configuration import compile_run_configuration
from conformance.run_plan_v2 import RunPlanV2
from conformance.runner import run_model_bank_smoke_check
from conformance.suite_catalog import SuiteMetadata
from conformance.test_plan import TestPlan, build_plan_test_value_context

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
        self._store.set_participant_action(
            self._run_id,
            step_id=step_id,
            url=url,
            expires_at=self._expires_at_from_payload(payload),
        )

    def _expires_at_from_payload(self, payload: Mapping[str, JsonValue] | None) -> datetime | None:
        """Parse an optional participant-action deadline from an event payload.

        Args:
            payload: Event payload that may contain an ``expires_at`` ISO-8601
                timestamp.

        Returns:
            UTC-aware deadline timestamp, or ``None`` when no valid deadline is
            provided.
        """
        raw_expires_at = (payload or {}).get("expires_at")
        if not isinstance(raw_expires_at, str):
            return None
        try:
            parsed = datetime.fromisoformat(raw_expires_at)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)


def start_run(
    *,
    config: ModelBankConfig,
    manifest: Manifest | None,
    plan: TestPlan | None,
    suite_metadata: SuiteMetadata | None = None,
    browser_psu_prompts: bool = False,
    launch_test_data_values: Mapping[str, str] | None = None,
) -> JsonObject:
    """Reserve a run slot and start asynchronous conformance execution.

    Args:
        config: Validated model-bank configuration.
        manifest: Parsed manifest object, or ``None`` to fall back to a
            smoke-check run.
        plan: Optional :class:`TestPlan` derived from ``manifest`` with any
            caller-supplied deselections already applied.
        suite_metadata: Optional catalog metadata when ``manifest`` was
            resolved from a Read/Write suite catalog lookup.
        browser_psu_prompts: Whether to mirror raw manual PSU authorisation
            URLs into transient in-memory run state for browser-launched runs.
        launch_test_data_values: Optional launch-time test-data snapshot from
            the Plan Builder preview. When provided, these values are used as
            the authoritative ``testData.values`` input for this run instead of
            ``config.test_data``.

    Returns:
        Initial public run-status JSON captured while the record is still in
        ``pending`` state.

    Raises:
        RunConflictError: If another run is already pending or running.
    """
    effective_plan = _effective_plan_for_launch(
        manifest=manifest,
        plan=plan,
        config=config,
        launch_test_data_values=launch_test_data_values,
    )
    planned_steps = _selected_planned_steps_snapshot(manifest=manifest, plan=effective_plan)
    record = run_store.create_run(planned_steps=planned_steps)
    warn_if_developer_mode()
    thread = threading.Thread(
        target=_execute_run,
        args=(record.run_id, config, manifest, effective_plan, suite_metadata, launch_test_data_values),
        kwargs={"browser_psu_prompts": browser_psu_prompts},
        daemon=True,
    )
    initial_status = record.to_status_json()
    thread.start()
    return initial_status


def _effective_plan_for_launch(
    *,
    manifest: Manifest | None,
    plan: TestPlan | None,
    config: ModelBankConfig,
    launch_test_data_values: Mapping[str, str] | None,
) -> TestPlan | None:
    """Return the launch-time execution plan for a run.

    Args:
        manifest: Parsed manifest selected for the run, if any.
        plan: Caller-supplied plan, or ``None`` when the lifecycle should
            derive the default manifest plan.
        config: Parsed participant configuration used to resolve test-value
            profile context for conditional plan defaults.
        launch_test_data_values: Optional launch-time test-data snapshot that
            overrides ``config.test_data`` for new-schema manifests.

    Returns:
        The supplied plan when present, the manifest default plan when
        ``manifest`` is present and ``plan`` is ``None``, otherwise ``None``
        for smoke-check runs.
    """
    if manifest is None:
        return None
    if plan is not None:
        return plan
    effective_test_data_values = _effective_launch_test_data_values(
        config=config,
        launch_test_data_values=launch_test_data_values,
    )
    effective_test_data = (
        TestDataConfig(values=MappingProxyType(effective_test_data_values)) if effective_test_data_values else None
    )
    validate_test_value_config_contract(
        manifest=manifest,
        config_test_values=config.test_values,
        config_test_data=effective_test_data,
    )
    run_config = compile_run_configuration(
        manifest=manifest,
        selected_step_ids=None,
        test_data_values=effective_test_data_values,
    )
    test_value_ctx = build_plan_test_value_context(
        manifest,
        config.test_values,
        effective_test_data,
        run_configuration=run_config,
    )
    return TestPlan.default_plan_from_manifest(manifest, test_value_context=test_value_ctx)


def _effective_launch_test_data_values(
    *,
    config: ModelBankConfig,
    launch_test_data_values: Mapping[str, str] | None,
) -> dict[str, str]:
    """Return launch-time participant test-data values for runtime compilation.

    Args:
        config: Parsed participant configuration for the run.
        launch_test_data_values: Optional preview-snapshot values supplied by
            browser launch. When provided these override ``config.test_data``.

    Returns:
        Copy of launch-effective ``testData.values`` to compile into
        :class:`~conformance.run_configuration.RunConfiguration`.
    """
    if launch_test_data_values is not None:
        return dict(launch_test_data_values)
    if config.test_data is not None:
        return dict(config.test_data.values)
    return {}


def _selected_planned_steps_snapshot(*, manifest: Manifest | None, plan: TestPlan | None) -> tuple[RunPlanStep, ...]:
    """Build an immutable selected-step snapshot for launch-time run records.

    Args:
        manifest: Parsed manifest selected for the run, if any.
        plan: Effective plan used for execution; selected entries are copied
            into the run snapshot.

    Returns:
        Tuple of selected plan-step snapshots in manifest order, or an empty
        tuple for non-manifest runs.
    """
    if manifest is None or plan is None:
        return ()

    selected_step_ids = set(plan.selected_step_ids())
    if not selected_step_ids:
        return ()

    planned_steps: list[RunPlanStep] = []
    for step in manifest.steps:
        if step.id not in selected_step_ids:
            continue
        planned_steps.append(
            RunPlanStep(
                step_id=step.id,
                name=step.name,
                kind=_manifest_step_kind(step),
                group=step.group,
                phase=step.phase,
                mandatory=step.mandatory,
                optional=step.optional,
                order=len(planned_steps),
            )
        )
    return tuple(planned_steps)


def _manifest_step_kind(step: V1Step) -> str:
    """Return the run-store step-kind discriminator for a manifest step.

    Args:
        step: Parsed v1 manifest step.

    Returns:
        ``"psu-authorization"`` for PSU authorization steps, otherwise
        ``"http"``.
    """
    return "psu-authorization" if isinstance(step, PsuAuthorizationStep) else "http"


def _execute_run(
    run_id: str,
    config: ModelBankConfig,
    manifest: Manifest | None,
    plan: TestPlan | None,
    suite_metadata: SuiteMetadata | None = None,
    launch_test_data_values: Mapping[str, str] | None = None,
    *,
    browser_psu_prompts: bool = False,
) -> None:
    """Execute a conformance run in a background thread.

    Transitions the run record through running -> completed/failed.

    Args:
        run_id: The run identifier to update in the store.
        config: Validated model-bank configuration.
        manifest: Parsed manifest object, or ``None`` to run the legacy smoke
            check.
        plan: Optional :class:`TestPlan` derived from ``manifest`` with any
            caller-supplied deselections already applied.
        suite_metadata: Optional catalog metadata when ``manifest`` was
            resolved from a Read/Write suite catalog lookup.
        launch_test_data_values: Optional launch-time test-data snapshot from
            browser preview state. When present, these values override
            ``config.test_data`` for run compilation and execution.
        browser_psu_prompts: Whether to wrap the execution logger so raw manual
            PSU authorisation URLs are exposed only as transient browser
            participant actions.
    """
    try:
        run_store.mark_running(run_id)
        run_record = run_store.get_run(run_id)
        if run_record is None:
            logger.debug(
                "Run %s disappeared before execution started; skipping lifecycle processing",
                run_id,
            )
            return
        # ``get_run`` returns a shallow copy whose ``execution_logger``
        # reference points at the same live buffer the API exposes; either
        # accessing the live record or the copy yields the same logger.
        run_logger = run_record.execution_logger
        logger_sink: ExecutionLogger = run_logger or NullExecutionLogger()
        if browser_psu_prompts:
            logger_sink = BrowserParticipantActionLogger(logger_sink, run_id=run_id, store=run_store)
        effective_manifest = manifest
        effective_plan = plan
        effective_suite_metadata = suite_metadata

        if effective_manifest is None:
            result = run_model_bank_smoke_check(config, execution_logger=logger_sink)
        else:
            effective_test_data_values = _effective_launch_test_data_values(
                config=config,
                launch_test_data_values=launch_test_data_values,
            )
            effective_test_data = (
                TestDataConfig(values=MappingProxyType(effective_test_data_values))
                if effective_test_data_values
                else None
            )
            validate_test_value_config_contract(
                manifest=effective_manifest,
                config_test_values=config.test_values,
                config_test_data=effective_test_data,
            )
            run_config = compile_run_configuration(
                manifest=effective_manifest,
                selected_step_ids=(set(effective_plan.selected_step_ids()) if effective_plan is not None else None),
                test_data_values=effective_test_data_values,
            )
            if effective_plan is None:
                test_value_ctx = build_plan_test_value_context(
                    effective_manifest,
                    config.test_values,
                    effective_test_data,
                    run_configuration=run_config,
                )
                effective_plan = TestPlan.default_plan_from_manifest(
                    effective_manifest,
                    test_value_context=test_value_ctx,
                )
            else:
                test_value_ctx = build_plan_test_value_context(
                    effective_manifest,
                    config.test_values,
                    effective_test_data,
                    run_configuration=run_config,
                )
            if run_config is None:
                run_config = compile_run_configuration(
                    manifest=effective_manifest,
                    selected_step_ids=set(effective_plan.selected_step_ids()),
                    test_data_values=effective_test_data_values,
                )
            try:
                http_client = build_json_http_client(
                    timeout_seconds=config.timeout_seconds,
                    ca_bundle_path=config.tls.ca_bundle_path,
                    client_certificate_path=config.tls.client_certificate_path,
                    client_private_key_path=config.tls.client_private_key_path,
                )
            except ValueError as error:
                logger.error("HTTP client setup failed for run %s: %s", run_id, error)
                run_store.mark_failed(run_id, error=f"HTTP client setup failed: {error}")
                return
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
                        oauth_resource_base_url=config.oauth.resource_base_url if config.oauth is not None else None,
                        oauth_client_id=config.oauth.client_id if config.oauth is not None else None,
                        oauth_redirect_uri=config.oauth.redirect_uri if config.oauth is not None else None,
                        oauth_authorization_endpoint=(
                            config.oauth.authorization_endpoint if config.oauth is not None else None
                        ),
                        oauth_open_banking_intent_id=(
                            config.oauth.open_banking_intent_id if config.oauth is not None else None
                        ),
                        test_values=build_runtime_test_values(
                            effective_manifest,
                            config.test_values,
                            effective_test_data,
                            run_configuration=run_config,
                        ),
                        test_value_profile_id=test_value_ctx.profile_id,
                        test_value_profile_source=test_value_ctx.profile_source,
                        test_value_override_keys=tuple(sorted(test_value_ctx.override_keys)),
                        baseline_delta_keys=(run_config.baseline_delta_keys if run_config is not None else frozenset()),
                    ),
                    fapi_signing_config=config.fapi_signing,
                    open_banking_config=config.open_banking,
                    mtls_client_configured=(
                        config.tls.client_certificate_path is not None
                        and config.tls.client_private_key_path is not None
                    ),
                    suite_metadata=effective_suite_metadata,
                    approved_release_policy=config.approved_release_policy,
                    custom_test_values_active=(
                        (run_config is not None and run_config.has_custom_values)
                        or (
                            run_config is None
                            and (
                                (
                                    config.test_values is not None
                                    and (config.test_values.profile is not None or bool(config.test_values.overrides))
                                )
                                or (config.test_data is not None and bool(config.test_data.values))
                            )
                        )
                    ),
                )
            finally:
                http_client.close()

        result_object = result.to_json_object()
        try:
            _persist_configured_artifacts(
                config=config,
                result_object=result_object,
                execution_logger=run_logger,
            )
        except OSError as error:
            logger.error("Artifact persistence failed for run %s: %s", run_id, error)
            run_store.mark_failed(run_id, error=f"Artifact persistence failed: {error}")
            return

        run_store.mark_completed(run_id, result=result_object)
    except ValueError as error:
        logger.error("Run %s failed due to invalid test-value configuration: %s", run_id, error)
        run_store.mark_failed(run_id, error=str(error))
    except Exception:
        logger.exception("Run %s failed with an internal error", run_id)
        run_store.mark_failed(run_id, error="An internal error occurred")
    finally:
        # Awaiting auth sessions must not outlive their parent run; drop
        # them on terminal transition so a subsequent run cannot inherit
        # stale state. Done in ``finally`` to cover both happy-path and
        # failure-path exits from the run.
        auth_session_store.discard_for_run(run_id)


def _persist_configured_artifacts(
    *,
    config: ModelBankConfig,
    result_object: JsonObject,
    execution_logger: BufferedExecutionLogger | None,
) -> None:
    """Write browser/API run artifacts to config-selected output paths.

    Parsed participant configs resolve output paths to absolute filesystem
    paths. Hand-built test configs often use relative dummy paths; those are
    intentionally ignored here so unit tests that only exercise lifecycle
    transitions do not create files in the repository root.

    Args:
        config: Participant configuration carrying artifact output paths.
        result_object: Structured result JSON object produced by the run.
        execution_logger: Per-run buffered execution logger, when available.

    Raises:
        OSError: If a configured artifact cannot be written.
    """
    if config.result_output_path.is_absolute():
        config.result_output_path.parent.mkdir(parents=True, exist_ok=True)
        config.result_output_path.write_text(
            json.dumps(result_object, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    if config.execution_log_path.is_absolute() and execution_logger is not None:
        execution_logger.flush_to_path(config.execution_log_path)


def start_dcr_run(*, config: ModelBankConfig, plan: RunPlanV2) -> JsonObject:
    """Reserve a run slot and start asynchronous DCR conformance execution.

    Args:
        config: Validated participant configuration containing a required
            ``dcr`` section.
        plan: The DCR run plan describing target coordinates and endpoint
            selections.

    Returns:
        Initial public run-status JSON captured while the record is still in
        ``pending`` state.

    Raises:
        RunConflictError: If another run is already pending or running.
    """
    record = run_store.create_run(planned_steps=())
    warn_if_developer_mode()
    thread = threading.Thread(
        target=_execute_dcr_run,
        args=(record.run_id, config, plan),
        daemon=True,
    )
    initial_status = record.to_status_json()
    thread.start()
    return initial_status


def _execute_dcr_run(run_id: str, config: ModelBankConfig, plan: RunPlanV2) -> None:
    """Execute a DCR conformance run in a background thread.

    Transitions the run record through running -> completed/failed and
    persists the DCR result JSON to the config-selected output path when
    absolute.

    Args:
        run_id: The run identifier to update in the store.
        config: Validated participant configuration containing a required
            ``dcr`` section.
        plan: DCR run plan describing target coordinates and endpoint
            selections.
    """
    try:
        run_store.mark_running(run_id)
        started_at = utc_now()
        try:
            dcr_result = execute_dcr_run(plan, config)
        except ConfigError as error:
            logger.error("DCR run %s failed: %s", run_id, error)
            run_store.mark_failed(run_id, error=str(error))
            return
        finished_at = utc_now()
        result_object = dcr_run_result_to_json_object(
            dcr_result,
            plan=plan,
            environment=config.environment,
            run_id=run_id,
            started_at=started_at,
            finished_at=finished_at,
        )
        try:
            _persist_configured_artifacts(
                config=config,
                result_object=result_object,
                execution_logger=None,
            )
        except OSError as error:
            logger.error(
                "Run %s completed but failed to persist result artifacts: %s",
                run_id,
                error,
            )
        run_store.mark_completed(run_id, result=result_object)
    except Exception:
        logger.exception("DCR run %s failed with an internal error", run_id)
        run_store.mark_failed(run_id, error="An internal error occurred")
    finally:
        auth_session_store.discard_for_run(run_id)
