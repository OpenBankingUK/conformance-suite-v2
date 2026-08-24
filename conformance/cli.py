"""Command-line workflow for running conformance checks."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

from conformance.api.auth_session_store import auth_session_store
from conformance.catalogue import (
    CatalogueError,
    TestPlanSpec,
    compile_test_plan,
    compile_test_plan_document,
    parse_test_plan_document,
)
from conformance.catalogue_registry import resolve_catalogue, supported_catalogues
from conformance.context import RuntimeConfig
from conformance.execution_log import (
    BufferedExecutionLogger,
    PsuAuthorizationUrlConsoleLogger,
    new_run_id,
    warn_if_developer_mode,
)
from conformance.executor import run_compiled_test_plan
from conformance.http import build_json_http_client
from conformance.model_bank_config import ConfigError, load_model_bank_config
from conformance.runner import run_model_bank_smoke_check

logger = logging.getLogger(__name__)


def run(argv: Sequence[str] | None = None) -> int:
    """Run a conformance check from config and an optional plan spec.

    Args:
        argv: Optional argument list to parse instead of `sys.argv`.

    Returns:
        Process-style exit code: 0 for pass, 1 for conformance failure, 2 for
        invalid input, and 3 when the structured result or execution log
        cannot be written.
    """
    parser = argparse.ArgumentParser(description="Run a conformance check")
    parser.add_argument("config", type=Path, help="Path to the model-bank JSON config")
    parser.add_argument(
        "--plan-spec",
        type=Path,
        help="Optional catalogue plan-spec JSON file to compile and execute",
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

    run_id = new_run_id()
    execution_logger = BufferedExecutionLogger(run_id=run_id)
    logger_sink = PsuAuthorizationUrlConsoleLogger(
        execution_logger,
        stdout=sys.stdout,
        stderr=sys.stderr,
    )

    if args.plan_spec is None:
        result = run_model_bank_smoke_check(config, execution_logger=logger_sink)
    else:
        try:
            raw_spec = json.loads(args.plan_spec.read_text(encoding="utf-8"))
            plan_document = parse_test_plan_document(raw_spec)
            if isinstance(plan_document, TestPlanSpec):
                plan_spec = plan_document
                compiled_plan = compile_test_plan(resolve_catalogue(plan_spec.catalogue_key), plan_spec)
                runtime_inputs = plan_spec.runtime_inputs
            else:
                compiled_plan = compile_test_plan_document(plan_document, supported_catalogues())
                runtime_inputs = plan_document.runtime_inputs
        except json.JSONDecodeError as error:
            logger.error("Plan-spec JSON error: %s", error.msg)
            return 2
        except OSError as error:
            logger.error("Unable to read plan spec: %s", error)
            return 2
        except CatalogueError as error:
            logger.error("Plan-spec error: %s", error)
            return 2

        http_client = build_json_http_client(
            timeout_seconds=config.timeout_seconds,
            ca_bundle_path=config.tls.ca_bundle_path,
            client_certificate_path=config.tls.client_certificate_path,
            client_private_key_path=config.tls.client_private_key_path,
        )
        try:
            result = run_compiled_test_plan(
                compiled_plan,
                runtime_inputs=runtime_inputs,
                runtime_input_base_dir=args.plan_spec.parent,
                client=http_client,
                execution_logger=logger_sink,
                run_id=run_id,
                auth_session_store=auth_session_store,
                runtime_config=RuntimeConfig(
                    discovery_url=config.discovery_url,
                    oauth_resource_base_url=config.oauth.resource_base_url if config.oauth is not None else None,
                    oauth_client_id=config.oauth.client_id if config.oauth is not None else None,
                    oauth_redirect_uri=config.oauth.redirect_uri if config.oauth is not None else None,
                    oauth_authorization_endpoint=(
                        config.oauth.authorization_endpoint if config.oauth is not None else None
                    ),
                    oauth_issuer=config.oauth.issuer if config.oauth is not None else None,
                    oauth_token_endpoint=config.oauth.token_endpoint if config.oauth is not None else None,
                    oauth_open_banking_intent_id=(
                        config.oauth.open_banking_intent_id if config.oauth is not None else None
                    ),
                    oauth_response_type=config.oauth.response_type if config.oauth is not None else None,
                    oauth_request_object_signing_alg=(
                        config.oauth.request_object_signing_alg if config.oauth is not None else None
                    ),
                ),
                fapi_signing_config=config.fapi_signing,
                mtls_client_configured=(
                    config.tls.client_certificate_path is not None and config.tls.client_private_key_path is not None
                ),
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

    run_label = f"Catalogue plan run ({args.plan_spec})" if args.plan_spec is not None else "Model-bank smoke check"
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
