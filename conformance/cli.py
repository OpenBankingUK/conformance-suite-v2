"""Command-line workflow for running conformance checks."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

from conformance.api.auth_session_store import auth_session_store
from conformance.context import RuntimeConfig
from conformance.execution_log import (
    BufferedExecutionLogger,
    PsuAuthorizationUrlConsoleLogger,
    new_run_id,
    warn_if_developer_mode,
)
from conformance.executor import run_manifest
from conformance.http import build_json_http_client
from conformance.manifest import ManifestError, load_manifest
from conformance.model_bank_config import ConfigError, load_model_bank_config
from conformance.runner import run_model_bank_smoke_check
from conformance.suite_catalog import SuiteCatalogError, SuiteMetadata, resolve_suite
from conformance.test_plan import TestPlan

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
            plan = TestPlan.default_plan_from_manifest(manifest).with_deselection(args.deselect)
        except ValueError as error:
            logger.error("Plan error: %s", error)
            return 2

        http_client = build_json_http_client(
            timeout_seconds=config.timeout_seconds,
            ca_bundle_path=config.tls.ca_bundle_path,
            client_certificate_path=config.tls.client_certificate_path,
            client_private_key_path=config.tls.client_private_key_path,
        )
        try:
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
                    oauth_resource_base_url=config.oauth.resource_base_url if config.oauth is not None else None,
                    oauth_client_id=config.oauth.client_id if config.oauth is not None else None,
                    oauth_redirect_uri=config.oauth.redirect_uri if config.oauth is not None else None,
                ),
                suite_metadata=suite_metadata,
                approved_release_policy=config.approved_release_policy,
            )
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
