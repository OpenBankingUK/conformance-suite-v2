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
from conformance.model_bank_config import ConfigError, load_model_bank_config
from conformance.run_configuration import compile_run_configuration
from conformance.runner import run_model_bank_smoke_check
from conformance.suite_catalog import SuiteCatalogError, SuiteMetadata, resolve_suite
from conformance.test_plan import TestPlan, build_plan_test_value_context

logger = logging.getLogger(__name__)


def run(argv: Sequence[str] | None = None) -> int:
    """Run a conformance check (model-bank smoke check or manifest run).

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
        "--deselect",
        action="append",
        default=[],
        metavar="STEP_ID",
        help=(
            "Deselect a v1 manifest step from the default test plan. Repeatable. "
            "Deselected steps do not run and produce no step result. Deselecting "
            "a mandatory step flips certificationEligibility to ineligible. "
            "Only valid with --manifest or a config-selected testSuite."
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

    if args.deselect and args.manifest is None and config.test_suite is None:
        logger.error("--deselect requires --manifest or config.testSuite")
        return 2

    run_id = new_run_id()
    execution_logger = BufferedExecutionLogger(run_id=run_id)
    logger_sink = PsuAuthorizationUrlConsoleLogger(
        execution_logger,
        stdout=sys.stdout,
        stderr=sys.stderr,
    )

    suite_metadata: SuiteMetadata | None = None
    if args.manifest is None and config.test_suite is None:
        result = run_model_bank_smoke_check(config, execution_logger=logger_sink)
    else:
        if args.manifest is None:
            suite_selection = config.test_suite
            if suite_selection is None:
                logger.error("No manifest or config.testSuite available to run")
                return 2
            try:
                resolved_suite = resolve_suite(suite_selection)
            except SuiteCatalogError as error:
                logger.error("Suite catalog error: %s", error)
                return 2
            manifest = resolved_suite.manifest
            suite_metadata = resolved_suite.metadata
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
