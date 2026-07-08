"""Command-line workflow for running conformance checks."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import Sequence
from contextlib import AbstractContextManager, nullcontext
from pathlib import Path

from conformance.api.auth_session_store import auth_session_store
from conformance.cli_callback_server import CliCallbackServer, CliCallbackServerError, needs_cli_callback_listener
from conformance.context import (
    RuntimeConfig,
    build_runtime_test_values,
    validate_test_value_config_contract,
)
from conformance.execution_log import (
    BufferedExecutionLogger,
    PsuAuthorizationUrlConsoleLogger,
    new_run_id,
    warn_if_developer_mode,
)
from conformance.executor import run_manifest
from conformance.http import build_json_http_client
from conformance.manifest import ManifestError, PsuAuthorizationStep, load_manifest
from conformance.model_bank_config import ConfigError, ModelBankConfig, load_model_bank_config
from conformance.plan_executor import (
    check_catalogue_drift,
    dcr_run_result_to_json_object,
    execute_dcr_run,
    resolve_rw_suite_for_plan,
    utc_now,
)
from conformance.run_configuration import compile_run_configuration
from conformance.run_plan_v2 import (
    EndpointSelection,
    RunPlanV2,
    RunPlanV2ParseError,
    RunPlanV2TargetCoordinates,
    parse_run_plan_v2,
)
from conformance.runner import run_model_bank_smoke_check
from conformance.suite_catalog import SuiteCatalogError, SuiteMetadata
from conformance.target_config import TestTargetConfig
from conformance.test_plan import TestPlan, build_plan_test_value_context

logger = logging.getLogger(__name__)


def run(argv: Sequence[str] | None = None) -> int:
    """Run a conformance check (model-bank smoke check, RunPlan v2, or manifest run).

    Args:
        argv: Optional argument list to parse instead of `sys.argv`.

    Returns:
        Process-style exit code: 0 for pass, 1 for conformance failure, 2 for
        invalid input, and 3 when the structured result or execution log
        cannot be written.
    """
    parser = argparse.ArgumentParser(description="Run a conformance check")
    parser.add_argument("config", type=Path, help="Path to the model-bank JSON config")
    parser.add_argument("--manifest", type=Path, help="Optional manifest JSON file (v0 or v1) to execute")
    parser.add_argument(
        "--run-plan",
        type=Path,
        default=None,
        help=(
            "Optional RunPlan v2 JSON file describing target coordinates and endpoint "
            "selections.  When supplied, the run is driven by the plan rather than a "
            "``testTarget`` config section or an inline manifest."
        ),
    )
    parser.add_argument(
        "--deselect",
        action="append",
        default=[],
        metavar="STEP_ID",
        help=(
            "Deselect a v1 manifest step from the default test plan. Repeatable. "
            "Deselected steps do not run and produce no step result. Deselecting "
            "a mandatory step flips certificationEligibility to ineligible. "
            "Only valid with --manifest or a Read/Write run plan/testTarget."
        ),
    )
    try:
        args = parser.parse_args(argv)
    except SystemExit as error:
        return error.code if isinstance(error.code, int) else 2

    warn_if_developer_mode()

    try:
        config = load_model_bank_config(args.config)
    except ConfigError as error:
        logger.error("Config error: %s", error)
        return 2

    run_plan: RunPlanV2 | None = None
    if args.run_plan is not None:
        try:
            raw_plan = json.loads(args.run_plan.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            logger.error("Unable to read run plan file %s: %s", args.run_plan, error)
            return 2
        try:
            run_plan = parse_run_plan_v2(raw_plan)
        except RunPlanV2ParseError as error:
            logger.error("Invalid run plan: %s", error)
            return 2
    elif config.test_target is not None:
        run_plan = _run_plan_from_test_target(config.test_target)

    if args.deselect and args.manifest is None and run_plan is None:
        logger.error("--deselect requires --manifest, --run-plan, or config.testTarget")
        return 2

    if run_plan is not None:
        drift = check_catalogue_drift(run_plan)
        if drift is not None:
            logger.warning("%s", drift)

        if run_plan.target.specification == "dynamic-client-registration":
            return _run_dcr(config=config, plan=run_plan)

    run_id = new_run_id()
    execution_logger = BufferedExecutionLogger(run_id=run_id)
    logger_sink = PsuAuthorizationUrlConsoleLogger(
        execution_logger,
        stdout=sys.stdout,
        stderr=sys.stderr,
    )

    suite_metadata: SuiteMetadata | None = None
    if args.manifest is None and run_plan is None:
        result = run_model_bank_smoke_check(config, execution_logger=logger_sink)
    else:
        if args.manifest is None:
            if run_plan is None:  # pragma: no cover - defended above
                logger.error("Internal error: run plan missing")
                return 2
            try:
                manifest, suite_metadata = resolve_rw_suite_for_plan(run_plan)
            except (ValueError, SuiteCatalogError) as error:
                logger.error("Suite resolution error: %s", error)
                return 2
        else:
            try:
                manifest = load_manifest(args.manifest)
            except ManifestError as error:
                logger.error("Manifest error: %s", error)
                return 2

        try:
            validate_test_value_config_contract(
                manifest=manifest,
                config_test_values=config.test_values,
                config_test_data=config.test_data,
            )
            preflight_run_config = compile_run_configuration(
                manifest=manifest,
                selected_step_ids=None,
                test_data_values=dict(config.test_data.values) if config.test_data is not None else {},
            )
            test_value_ctx = build_plan_test_value_context(
                manifest,
                config.test_values,
                config.test_data,
                run_configuration=preflight_run_config,
            )
            plan = TestPlan.default_plan_from_manifest(manifest, test_value_context=test_value_ctx).with_deselection(
                args.deselect
            )
        except ValueError as error:
            logger.error("Plan error: %s", error)
            return 2

        run_config = compile_run_configuration(
            manifest=manifest,
            selected_step_ids=set(plan.selected_step_ids()),
            test_data_values=dict(config.test_data.values) if config.test_data is not None else {},
        )

        http_client = build_json_http_client(
            timeout_seconds=config.timeout_seconds,
            ca_bundle_path=config.tls.ca_bundle_path,
            client_certificate_path=config.tls.client_certificate_path,
            client_private_key_path=config.tls.client_private_key_path,
        )
        callback_context: AbstractContextManager[object] = nullcontext()
        oauth_redirect_uri = config.oauth.redirect_uri if config.oauth is not None else None
        if needs_cli_callback_listener(
            redirect_uri=oauth_redirect_uri,
            has_manual_psu_step=_plan_has_manual_psu_step(manifest=manifest, plan=plan),
        ):
            if oauth_redirect_uri is None:
                logger.error("oauth.redirectUri is required for CLI callback listener")
                http_client.close()
                return 2
            callback_context = CliCallbackServer(
                redirect_uri=oauth_redirect_uri,
                auth_session_store=auth_session_store,
            )
        try:
            try:
                with callback_context:
                    result = run_manifest(
                        manifest,
                        environment=config.environment,
                        client=http_client,
                        execution_logger=logger_sink,
                        plan=plan,
                        run_id=run_id,
                        auth_session_store=auth_session_store,
                        runtime_config=RuntimeConfig(
                            discovery_url=config.discovery_url,
                            environment=config.environment,
                            oauth_resource_base_url=(
                                config.oauth.resource_base_url if config.oauth is not None else None
                            ),
                            oauth_client_id=config.oauth.client_id if config.oauth is not None else None,
                            oauth_redirect_uri=config.oauth.redirect_uri if config.oauth is not None else None,
                            oauth_authorization_endpoint=(
                                config.oauth.authorization_endpoint if config.oauth is not None else None
                            ),
                            oauth_open_banking_intent_id=(
                                config.oauth.open_banking_intent_id if config.oauth is not None else None
                            ),
                            test_values=build_runtime_test_values(
                                manifest,
                                config.test_values,
                                config.test_data,
                                run_configuration=run_config,
                            ),
                            test_value_profile_id=test_value_ctx.profile_id,
                            test_value_profile_source=test_value_ctx.profile_source,
                            test_value_override_keys=tuple(sorted(test_value_ctx.override_keys)),
                            baseline_delta_keys=(
                                run_config.baseline_delta_keys if run_config is not None else frozenset()
                            ),
                        ),
                        fapi_signing_config=config.fapi_signing,
                        open_banking_config=config.open_banking,
                        mtls_client_configured=(
                            config.tls.client_certificate_path is not None
                            and config.tls.client_private_key_path is not None
                        ),
                        suite_metadata=suite_metadata,
                        approved_release_policy=config.approved_release_policy,
                        custom_test_values_active=(
                            (run_config is not None and run_config.has_custom_values)
                            or (
                                run_config is None
                                and (
                                    (
                                        config.test_values is not None
                                        and (
                                            config.test_values.profile is not None or bool(config.test_values.overrides)
                                        )
                                    )
                                    or (config.test_data is not None and bool(config.test_data.values))
                                )
                            )
                        ),
                    )
            except CliCallbackServerError as error:
                logger.error("Callback listener error: %s", error)
                return 2
        finally:
            http_client.close()
    try:
        config.result_output_path.parent.mkdir(parents=True, exist_ok=True)
        config.result_output_path.write_text(
            json.dumps(result.to_json_object(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except OSError as error:
        logger.error("Unable to write result to %s: %s", config.result_output_path, error)
        return 3

    try:
        execution_logger.flush_to_path(config.execution_log_path)
    except OSError as error:
        logger.error("Unable to write execution log to %s: %s", config.execution_log_path, error)
        return 3

    if args.manifest is not None:
        run_label = f"Manifest run ({args.manifest})"
    elif suite_metadata is not None:
        run_label = f"Suite run ({suite_metadata.label})"
    else:
        run_label = "Model-bank smoke check"
    if result.status == "passed":
        logger.info(
            "%s passed; wrote %s and %s",
            run_label,
            config.result_output_path,
            config.execution_log_path,
        )
        return 0

    logger.error(
        "%s failed; wrote %s and %s",
        run_label,
        config.result_output_path,
        config.execution_log_path,
    )
    return 1


def _run_dcr(*, config: ModelBankConfig, plan: RunPlanV2) -> int:
    """Execute a DCR conformance run driven by a RunPlanV2.

    Args:
        config: Validated participant configuration containing a required
            ``dcr`` section.
        plan: The DCR run plan authored against ``dynamic-client-registration``
            target coordinates.

    Returns:
        Process-style exit code: 0 for pass, 1 for conformance failure, 2 for
        invalid input, and 3 when result artifacts cannot be written.
    """
    run_id = new_run_id()
    started_at = utc_now()
    try:
        dcr_result = execute_dcr_run(plan, config)
    except ConfigError as error:
        logger.error("DCR config error: %s", error)
        return 2
    except Exception as error:  # noqa: BLE001
        logger.error("DCR run error: %s", error)
        return 1
    finished_at = utc_now()

    body = dcr_run_result_to_json_object(
        dcr_result,
        plan=plan,
        environment=config.environment,
        run_id=run_id,
        started_at=started_at,
        finished_at=finished_at,
    )

    try:
        config.result_output_path.parent.mkdir(parents=True, exist_ok=True)
        config.result_output_path.write_text(
            json.dumps(body, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except OSError as error:
        logger.error("Unable to write DCR result to %s: %s", config.result_output_path, error)
        return 3

    try:
        config.execution_log_path.parent.mkdir(parents=True, exist_ok=True)
        config.execution_log_path.write_bytes(b"")
    except OSError as error:
        logger.error("Unable to write DCR execution log to %s: %s", config.execution_log_path, error)
        return 3

    summary = body["summary"]
    assert isinstance(summary, dict)  # noqa: S101 - JSON structure invariant established by dcr_run_result_to_json_object
    passed = summary["passed"]
    failed = summary["failed"]
    if body["status"] == "passed":
        logger.info(
            "DCR run passed (%s passed, %s failed); wrote %s",
            passed,
            failed,
            config.result_output_path,
        )
        return 0
    logger.error(
        "DCR run failed (%s passed, %s failed); wrote %s",
        passed,
        failed,
        config.result_output_path,
    )
    return 1


def _run_plan_from_test_target(target: TestTargetConfig) -> RunPlanV2:
    """Build a minimal RunPlanV2 from a :class:`TestTargetConfig`.

    Used when the CLI or API caller supplies ``testTarget`` in config without a
    ``runPlan`` file.  The generated plan uses an ``sha256:unknown`` catalogue
    hash so :func:`check_catalogue_drift` treats drift detection as
    unavailable rather than emitting a false-positive warning.

    Args:
        target: Validated target coordinates from participant config.

    Returns:
        A :class:`RunPlanV2` with no endpoint selections (all default), the
        supplied resource groups, and a placeholder catalogue hash.
    """
    return RunPlanV2(
        schema_version="2",
        target=RunPlanV2TargetCoordinates(
            standard=target.standard,
            specification=target.specification,
            security_profile=target.security_profile,
            specification_version=target.specification_version,
            catalogue_hash="sha256:unknown",
        ),
        resource_groups=target.resource_groups,
        endpoint_selections=(),
    )


def _plan_has_manual_psu_step(*, manifest: object, plan: TestPlan) -> bool:
    """Return whether the selected plan includes a manual PSU step.

    Args:
        manifest: Parsed manifest whose steps are referenced by ``plan``.
        plan: Effective execution plan.

    Returns:
        ``True`` when any selected plan entry refers to a manual
        :class:`PsuAuthorizationStep`.
    """
    steps = getattr(manifest, "steps", ())
    step_by_id = {step.id: step for step in steps}
    selected_step_ids = {entry.step_id for entry in plan.entries if entry.selected}
    return any(
        isinstance(step, PsuAuthorizationStep) and step.mode == "manual"
        for step_id, step in step_by_id.items()
        if step_id in selected_step_ids
    )


# Endpoint selection re-export for tests that build minimal plans.
__all__ = [
    "EndpointSelection",
    "run",
]
