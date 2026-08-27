"""Shared run lifecycle helpers for API and browser-triggered runs.

This module owns the process-local lifecycle for starting a conformance run:
reserve the single active run slot, snapshot the initial pending response,
start the background thread, and transition terminal runs while cleaning up
run-scoped PSU authorization sessions.
"""

from __future__ import annotations

import copy
import json
import logging
import threading
from collections.abc import Mapping
from pathlib import Path

from conformance.api.auth_session_store import auth_session_store
from conformance.api.run_store import RunPlanStep, RunRecord, RunStore, run_store
from conformance.catalogue import CatalogueRequestStep, CompiledTestPlan
from conformance.context import RuntimeConfig
from conformance.execution_log import (
    BufferedExecutionLogger,
    EventType,
    ExecutionLogger,
    NullExecutionLogger,
    warn_if_developer_mode,
)
from conformance.executor import (
    compiled_plan_synthetic_inline_steps,
    compiled_plan_synthetic_setup_steps,
    run_compiled_test_plan,
    run_manifest,
)
from conformance.http import build_json_http_client
from conformance.json_types import JsonObject, JsonValue
from conformance.manifest import Manifest, PsuAuthorizationStep, V1Step
from conformance.model_bank_config import ModelBankConfig
from conformance.results import mark_development_result_evidence
from conformance.runner import run_model_bank_smoke_check
from conformance.test_plan import TestPlan

logger = logging.getLogger(__name__)

_AIS_CONSENT_TEMPLATE_STEP_ID = "ais-at-setup-consent-request"
"""Catalogue template step id expanded into AIS permission-profile consent steps."""

_AIS_TOKEN_TEMPLATE_STEP_ID = "ais-at-setup-token-request"  # noqa: S105 - step id, not a secret
"""Catalogue template step id expanded into AIS permission-profile token steps."""

_AIS_PERMISSION_PROFILE_TOKEN_IDS = {
    "basic": "ais-account-access-basic",
    "detail": "ais-account-access-detail",
}
"""Semantic AIS access-token ids keyed by legacy permission profile."""


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
    compiled_plan: CompiledTestPlan | None = None,
    runtime_inputs: Mapping[str, JsonValue] | None = None,
    runtime_input_base_dir: Path | None = None,
    manifest: Manifest | None = None,
    plan: TestPlan | None = None,
    browser_psu_prompts: bool = False,
    plan_snapshot: JsonObject | None = None,
    validation_result: JsonObject | None = None,
) -> JsonObject:
    """Reserve a run slot and start asynchronous conformance execution.

    Args:
        config: Validated model-bank configuration.
        compiled_plan: Optional compiled catalogue plan for the new execution
            contract.
        runtime_inputs: Original runtime input mapping for ``compiled_plan``.
            Required when ``compiled_plan`` is supplied.
        runtime_input_base_dir: Directory used to resolve catalogue
            ``file_reference`` runtime inputs. Required when ``compiled_plan``
            is supplied.
        manifest: Parsed manifest object for legacy browser-preview internals,
            or ``None`` for compiled-plan/API/CLI and smoke-check runs.
        plan: Optional :class:`TestPlan` derived from ``manifest`` with any
            caller-supplied deselections already applied.
        browser_psu_prompts: Whether to mirror raw manual PSU authorisation
            URLs into transient in-memory run state for browser-launched runs.
        plan_snapshot: Optional secret-safe JSON-first test-plan snapshot to
            persist into the run result.
        validation_result: Optional pre-run shared validation outcome to persist
            into the run result.

    Returns:
        Initial public run-status JSON captured while the record is still in
        ``pending`` state.

    Raises:
        RunConflictError: If another run is already pending or running.
    """
    if compiled_plan is not None and (runtime_inputs is None or runtime_input_base_dir is None):
        raise ValueError("compiled_plan launches require runtime_inputs and runtime_input_base_dir")
    effective_plan = _effective_plan_for_launch(manifest=manifest, plan=plan)
    planned_steps = _selected_planned_steps_snapshot(
        compiled_plan=compiled_plan,
        manifest=manifest,
        plan=effective_plan,
    )
    record = run_store.create_run(
        planned_steps=planned_steps,
        plan_snapshot=plan_snapshot,
        validation_result=validation_result,
    )
    warn_if_developer_mode()
    thread = threading.Thread(
        target=_execute_run,
        args=(
            record.run_id,
            config,
            compiled_plan,
            runtime_inputs,
            runtime_input_base_dir,
            manifest,
            effective_plan,
        ),
        kwargs={"browser_psu_prompts": browser_psu_prompts},
        daemon=True,
    )
    initial_status = record.to_status_json()
    thread.start()
    return initial_status


def _effective_plan_for_launch(*, manifest: Manifest | None, plan: TestPlan | None) -> TestPlan | None:
    """Return the launch-time execution plan for a run.

    Args:
        manifest: Parsed manifest selected for the run, if any.
        plan: Caller-supplied plan, or ``None`` when the lifecycle should
            derive the default manifest plan.

    Returns:
        The supplied plan when present, the manifest default plan when
        ``manifest`` is present and ``plan`` is ``None``, otherwise ``None``
        for smoke-check runs.
    """
    if manifest is None:
        return None
    if plan is not None:
        return plan
    return TestPlan.default_plan_from_manifest(manifest)


def _selected_planned_steps_snapshot(
    *,
    compiled_plan: CompiledTestPlan | None = None,
    manifest: Manifest | None,
    plan: TestPlan | None,
) -> tuple[RunPlanStep, ...]:
    """Build an immutable selected-step snapshot for launch-time run records.

    Args:
        compiled_plan: Compiled catalogue plan selected for the run, if any.
        manifest: Parsed manifest selected for the run, if any.
        plan: Effective plan used for execution; selected entries are copied
            into the run snapshot.

    Returns:
        Tuple of selected plan-step snapshots in manifest order, or an empty
        tuple for non-manifest runs.
    """
    if compiled_plan is not None:
        return _compiled_plan_steps_snapshot(compiled_plan)
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


def _compiled_plan_steps_snapshot(compiled_plan: CompiledTestPlan) -> tuple[RunPlanStep, ...]:
    """Build a selected-step snapshot for a compiled catalogue plan.

    Args:
        compiled_plan: Compiled catalogue plan selected for launch.

    Returns:
        Tuple of selected setup and request-step snapshots in compiled execution
        order.
    """
    planned_steps: list[RunPlanStep] = []
    for setup_step in compiled_plan_synthetic_setup_steps(compiled_plan):
        planned_steps.append(
            RunPlanStep(
                step_id=setup_step.id,
                name=setup_step.name,
                kind=_manifest_step_kind(setup_step),
                group=setup_step.group,
                phase=setup_step.phase,
                mandatory=setup_step.mandatory,
                optional=setup_step.optional,
                order=len(planned_steps),
            )
        )
    for test_case in compiled_plan.test_cases:
        for request_step in test_case.request_steps:
            replacement_steps = _compiled_plan_replacement_steps_snapshot(
                compiled_plan,
                request_step,
                start_order=len(planned_steps),
            )
            if replacement_steps:
                planned_steps.extend(replacement_steps)
                continue
            planned_steps.append(
                RunPlanStep(
                    step_id=request_step.step_id,
                    name=request_step.name,
                    kind="http",
                    group=test_case.test_case_id,
                    phase="setup" if test_case.role in {"setup", "security", "token"} else "execution",
                    mandatory=test_case.mandatory,
                    optional=not test_case.mandatory,
                    order=len(planned_steps),
                )
            )
            planned_steps.extend(
                _compiled_plan_inline_steps_snapshot(
                    compiled_plan,
                    request_step,
                    start_order=len(planned_steps),
                )
            )
    return tuple(planned_steps)


def _compiled_plan_replacement_steps_snapshot(
    compiled_plan: CompiledTestPlan,
    request_step: CatalogueRequestStep,
    *,
    start_order: int,
) -> list[RunPlanStep]:
    """Build planned rows for catalogue request steps replaced at runtime.

    Args:
        compiled_plan: Compiled catalogue plan selected for launch.
        request_step: Catalogue request step being considered for snapshotting.
        start_order: Snapshot order assigned to the first replacement step.

    Returns:
        Ordered replacement run-plan entries, or an empty list when the
        catalogue request step executes as-is.
    """
    profiles = _compiled_plan_ais_permission_profiles(compiled_plan)
    if request_step.step_id == _AIS_CONSENT_TEMPLATE_STEP_ID:
        replacement_steps: list[RunPlanStep] = []
        for profile in profiles:
            replacement_steps.extend(
                (
                    RunPlanStep(
                        step_id=f"ais-at-setup-{profile}-consent-request",
                        name=f"Create AIS {profile} account-access consent",
                        kind="http",
                        group=f"ais-at-setup-{profile}-consent",
                        phase="setup",
                        mandatory=True,
                        optional=False,
                        order=start_order + len(replacement_steps),
                    ),
                    RunPlanStep(
                        step_id=f"setup-ais-{profile}-consent-authorisation",
                        name=f"Authorise AIS {profile} account-access consent",
                        kind="psu-authorization",
                        group=f"ais-at-setup-{profile}-consent",
                        phase="setup",
                        mandatory=True,
                        optional=False,
                        order=start_order + len(replacement_steps) + 1,
                    ),
                )
            )
        return replacement_steps
    if request_step.step_id == _AIS_TOKEN_TEMPLATE_STEP_ID:
        return [
            RunPlanStep(
                step_id=f"ais-at-setup-{profile}-token-request",
                name=f"Exchange AIS {profile} authorisation code for account-access token",
                kind="http",
                group=f"ais-at-setup-{profile}-token",
                phase="setup",
                mandatory=True,
                optional=False,
                order=start_order + offset,
            )
            for offset, profile in enumerate(profiles)
        ]
    return []


def _compiled_plan_ais_permission_profiles(compiled_plan: CompiledTestPlan) -> tuple[str, ...]:
    """Return selected AIS permission profiles in deterministic execution order.

    Args:
        compiled_plan: Compiled catalogue plan selected for launch.

    Returns:
        Permission profile labels required by selected AIS resource requests.
    """
    required_token_ids = {
        request_step.required_token_id
        for test_case in compiled_plan.test_cases
        for request_step in test_case.request_steps
        if request_step.required_token_id is not None
    }
    return tuple(
        profile for profile, token_id in _AIS_PERMISSION_PROFILE_TOKEN_IDS.items() if token_id in required_token_ids
    )


def _compiled_plan_inline_steps_snapshot(
    compiled_plan: CompiledTestPlan,
    request_step: CatalogueRequestStep,
    *,
    start_order: int,
) -> list[RunPlanStep]:
    """Build pending-run snapshot entries for synthetic inline runtime steps.

    Args:
        compiled_plan: Compiled catalogue plan selected for launch.
        request_step: Catalogue request step most recently added to the
            snapshot.
        start_order: Snapshot order assigned to the first synthetic inline
            step.

    Returns:
        Ordered snapshot entries for synthetic runtime steps inserted after
        ``request_step``.
    """
    return [
        RunPlanStep(
            step_id=inline_step.id,
            name=inline_step.name,
            kind=_manifest_step_kind(inline_step),
            group=inline_step.group,
            phase=inline_step.phase,
            mandatory=inline_step.mandatory,
            optional=inline_step.optional,
            order=start_order + offset,
        )
        for offset, inline_step in enumerate(compiled_plan_synthetic_inline_steps(compiled_plan, request_step))
    ]


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
    compiled_plan: CompiledTestPlan | None = None,
    runtime_inputs: Mapping[str, JsonValue] | None = None,
    runtime_input_base_dir: Path | None = None,
    manifest: Manifest | None = None,
    plan: TestPlan | None = None,
    *,
    browser_psu_prompts: bool = False,
) -> None:
    """Execute a conformance run in a background thread.

    Transitions the run record through running -> completed/failed.

    Args:
        run_id: The run identifier to update in the store.
        config: Validated model-bank configuration.
        compiled_plan: Optional compiled catalogue plan to execute.
        runtime_inputs: Runtime input mapping for ``compiled_plan``.
        runtime_input_base_dir: Directory for catalogue file references.
        manifest: Parsed manifest object, or ``None`` to run a compiled plan
            or legacy smoke check.
        plan: Optional :class:`TestPlan` derived from ``manifest`` with any
            caller-supplied deselections already applied.
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
        if compiled_plan is None and manifest is None:
            result = run_model_bank_smoke_check(config, execution_logger=logger_sink)
        else:
            try:
                http_client = build_json_http_client(
                    ca_bundle_path=config.tls.ca_bundle_path,
                    client_certificate_path=config.tls.client_certificate_path,
                    client_private_key_path=config.tls.client_private_key_path,
                )
            except ValueError as error:
                logger.error("HTTP client setup failed for run %s: %s", run_id, error)
                run_store.mark_failed(run_id, error=f"HTTP client setup failed: {error}")
                return
            try:
                runtime_config = RuntimeConfig(
                    discovery_url=config.discovery_url,
                    oauth_resource_base_url=config.oauth.resource_base_url if config.oauth is not None else None,
                    oauth_client_id=config.oauth.client_id if config.oauth is not None else None,
                    oauth_redirect_uri=config.oauth.redirect_uri if config.oauth is not None else None,
                    oauth_authorization_endpoint=(
                        config.oauth.authorization_endpoint if config.oauth is not None else None
                    ),
                    oauth_issuer=config.oauth.issuer if config.oauth is not None else None,
                    oauth_token_endpoint=config.oauth.token_endpoint if config.oauth is not None else None,
                    oauth_response_type=config.oauth.response_type if config.oauth is not None else None,
                    oauth_request_object_signing_alg=(
                        config.oauth.request_object_signing_alg if config.oauth is not None else None
                    ),
                )
                mtls_configured = (
                    config.tls.client_certificate_path is not None and config.tls.client_private_key_path is not None
                )
                if compiled_plan is not None:
                    if runtime_inputs is None or runtime_input_base_dir is None:
                        raise ValueError("compiled plan execution requires runtime inputs")
                    result = run_compiled_test_plan(
                        compiled_plan,
                        runtime_inputs=runtime_inputs,
                        runtime_input_base_dir=runtime_input_base_dir,
                        client=http_client,
                        execution_logger=logger_sink,
                        run_id=run_id,
                        auth_session_store=auth_session_store,
                        runtime_config=runtime_config,
                        fapi_signing_config=config.fapi_signing,
                        mtls_client_configured=mtls_configured,
                        approved_release_policy=config.approved_release_policy,
                    )
                else:
                    effective_manifest = manifest
                    if effective_manifest is None:
                        raise ValueError("manifest execution requires a manifest")
                    effective_plan = (
                        plan if plan is not None else TestPlan.default_plan_from_manifest(effective_manifest)
                    )
                    result = run_manifest(
                        effective_manifest,
                        client=http_client,
                        execution_logger=logger_sink,
                        plan=effective_plan,
                        run_id=run_id,
                        auth_session_store=auth_session_store,
                        runtime_config=runtime_config,
                        fapi_signing_config=config.fapi_signing,
                        mtls_client_configured=mtls_configured,
                        approved_release_policy=config.approved_release_policy,
                    )
            finally:
                http_client.close()

        result_object = result.to_json_object()
        _attach_plan_evidence(result_object, run_record)
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
    except Exception:
        logger.exception("Run %s failed with an internal error", run_id)
        run_store.mark_failed(run_id, error="An internal error occurred")
    finally:
        # Awaiting auth sessions must not outlive their parent run; drop
        # them on terminal transition so a subsequent run cannot inherit
        # stale state. Done in ``finally`` to cover both happy-path and
        # failure-path exits from the run.
        auth_session_store.discard_for_run(run_id)


def _attach_plan_evidence(result_object: JsonObject, run_record: RunRecord) -> None:
    """Attach test-plan snapshot and validation evidence to a result object.

    Args:
        result_object: Mutable result JSON object generated by the runner.
        run_record: Run record snapshot captured after the run starts.
    """
    plan_snapshot = getattr(run_record, "plan_snapshot", None)
    validation_result = getattr(run_record, "validation_result", None)
    if isinstance(plan_snapshot, dict):
        result_object["testPlanSnapshot"] = copy.deepcopy(plan_snapshot)
    if isinstance(validation_result, dict):
        result_object["testPlanValidation"] = copy.deepcopy(validation_result)
        mark_development_result_evidence(validation_result, result_object)


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
