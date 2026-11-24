"""Command-line workflow for running conformance checks."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import tempfile
import time
import webbrowser
from collections.abc import Iterator, Mapping, Sequence
from contextlib import AbstractContextManager, contextmanager, nullcontext
from pathlib import Path

from conformance import BETA_NOTICE, FEEDBACK_EMAIL
from conformance.api.auth_session_store import auth_session_store
from conformance.catalogue import CompiledTestPlan
from conformance.cli_callback import CallbackListenerError, psu_callback_listener
from conformance.cli_cancellation import current_cancellation_check, run_cancellable
from conformance.cli_console import supports_colour, write_notice
from conformance.cli_summary import render_run_summary
from conformance.context import RuntimeConfig
from conformance.execution_log import (
    BufferedExecutionLogger,
    PsuAuthorizationUrlConsoleLogger,
    new_run_id,
    warn_if_developer_mode,
)
from conformance.executor import compiled_plan_requires_psu_callback, run_compiled_test_plan
from conformance.http import build_json_http_client
from conformance.json_types import JsonObject, JsonValue
from conformance.model_bank_config import ConfigError, ModelBankConfig, load_model_bank_config
from conformance.results import SmokeCheckResult, mark_development_result_evidence
from conformance.runner import run_model_bank_smoke_check
from conformance.test_plan_validation import TestPlanValidationError, prepare_test_plan_for_run

logger = logging.getLogger(__name__)


def run(argv: Sequence[str] | None = None) -> int:
    """Run a conformance check from config input or a canonical test plan.

    A concise run summary (status, counts, PSU authorisation mode,
    certification-eligibility reasons, failed-step digest, and artefact paths)
    is printed to stdout once the result file is written. Manual PSU
    authorisation URLs are printed to stderr, and a built-in HTTPS callback
    listener on the configured ``redirectUri`` receives the ASPSP redirect.

    Args:
        argv: Optional argument list to parse instead of `sys.argv`.

    Returns:
        Process-style exit code: 0 for pass, 1 for conformance failure, 2 for
        invalid input, and 3 when the structured result or execution log
        cannot be written. Cancellation returns 130 for SIGINT or 143 for SIGTERM.
    """
    return run_cancellable(lambda: _run(argv))


def _run(argv: Sequence[str] | None) -> int:
    parser = argparse.ArgumentParser(description="Run a conformance check")
    parser.add_argument("config", nargs="?", type=Path, help="Path to the model-bank JSON config")
    parser.add_argument(
        "--test-plan",
        type=Path,
        help="Canonical schemaVersion 1.0 test plan JSON file to validate and execute",
    )
    parser.add_argument(
        "--open-browser",
        action="store_true",
        help="Best-effort: open manual PSU authorisation URLs in the default browser",
    )
    parser.add_argument(
        "--callback-listen",
        metavar="HOST:PORT",
        help=(
            "Bind address for the manual PSU callback listener; defaults to loopback "
            "(or 0.0.0.0 in the container) on the redirectUri port"
        ),
    )
    try:
        args = parser.parse_args(argv)
        if args.config is None and args.test_plan is None:
            parser.error("config is required unless --test-plan is supplied")
        if args.test_plan is not None and args.config is not None:
            parser.error("--test-plan already contains execution config; do not pass a separate config")
    except SystemExit as error:
        return error.code if isinstance(error.code, int) else 2

    write_notice("[BETA]", BETA_NOTICE, tone="warning")
    write_notice(
        "[BETA]", f"Any feedback? Please email {FEEDBACK_EMAIL}. Do not include credentials or tokens.", tone="warning"
    )
    write_notice("[CLI]", f"Cancel: Ctrl+C, or send SIGTERM to Python PID {os.getpid()} (exit codes 130/143).")
    warn_if_developer_mode()

    run_id = new_run_id()
    execution_logger = BufferedExecutionLogger(run_id=run_id)
    logger_sink = PsuAuthorizationUrlConsoleLogger(
        execution_logger,
        stdout=sys.stdout,
        stderr=sys.stderr,
        open_browser=webbrowser.open if args.open_browser else None,
        progress=True,
        cancellation_check=current_cancellation_check(),
    )

    plan_snapshot: JsonObject | None = None
    validation_result: JsonObject | None = None

    if args.test_plan is not None:
        write_notice("[CLI]", "Reading, validating and compiling test plan...")
        preparation_started = time.perf_counter()
        try:
            raw_test_plan = json.loads(args.test_plan.read_text(encoding="utf-8"))
            prepared = prepare_test_plan_for_run(raw_test_plan, base_dir=args.test_plan.parent)
        except json.JSONDecodeError as error:
            logger.error("Test-plan JSON error: %s", error.msg)
            return 2
        except OSError as error:
            logger.error("Unable to read test plan: %s", error)
            return 2
        except TestPlanValidationError as error:
            logger.error("Test-plan validation error: %s", error)
            return 2

        config = prepared.config
        compiled_plan = prepared.compiled_plan
        runtime_inputs = prepared.runtime_inputs
        runtime_input_base_dir = args.test_plan.parent
        plan_snapshot = prepared.snapshot
        validation_result = prepared.validation.to_json_object()
        write_notice(
            "[CLI]",
            f"Test plan ready in {time.perf_counter() - preparation_started:.2f}s "
            f"({len(compiled_plan.test_cases)} test cases).",
        )
        try:
            with _callback_listener_for(config, compiled_plan, listen=args.callback_listen):
                result = _run_cli_compiled_plan(
                    config=config,
                    compiled_plan=compiled_plan,
                    runtime_inputs=runtime_inputs,
                    runtime_input_base_dir=runtime_input_base_dir,
                    logger_sink=logger_sink,
                    run_id=run_id,
                )
        except CallbackListenerError as error:
            logger.error("%s", error)
            return 2
    else:
        assert args.config is not None  # noqa: S101 - argparse validation above
        write_notice("[CLI]", "Loading model-bank configuration...")
        try:
            config = load_model_bank_config(args.config)
        except ConfigError as error:
            logger.error("Config error: %s", error)
            return 2

        result = run_model_bank_smoke_check(config, execution_logger=logger_sink)

    write_notice("[CLI]", "Writing result and execution log...")
    result_object = result.to_json_object()
    if plan_snapshot is not None:
        result_object["testPlanSnapshot"] = plan_snapshot
    if validation_result is not None:
        result_object["testPlanValidation"] = validation_result
        mark_development_result_evidence(validation_result, result_object)
    try:
        config.result_output_path.parent.mkdir(parents=True, exist_ok=True)
        _write_result(
            config.result_output_path,
            json.dumps(result_object, indent=2, sort_keys=True) + "\n",
        )
    except OSError as error:
        logger.error("Unable to write result to %s: %s", config.result_output_path, error)
        return 3

    try:
        execution_logger.flush_to_path(config.execution_log_path)
    except OSError as error:
        logger.error("Unable to write execution log to %s: %s", config.execution_log_path, error)
        return 3

    run_label = f"Test plan run ({args.test_plan})" if args.test_plan is not None else "Model-bank smoke check"
    sys.stdout.write(
        render_run_summary(
            result_object,
            run_label=str(args.test_plan) if args.test_plan is not None else "model-bank smoke check",
            result_path=config.result_output_path,
            execution_log_path=config.execution_log_path,
            colour=supports_colour(sys.stdout),
        )
    )
    sys.stdout.flush()
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


def _write_result(path: Path, content: str) -> None:
    """Publish only complete result evidence, cleaning staging files on cancellation."""
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, prefix=path.name + ".", suffix=".tmp", delete=False
    ) as temporary:
        temporary_path = Path(temporary.name)
        try:
            temporary.write(content)
            temporary.close()
            temporary_path.replace(path)
        finally:
            temporary.close()
            temporary_path.unlink(missing_ok=True)


def _callback_listener_for(
    config: ModelBankConfig,
    compiled_plan: CompiledTestPlan,
    *,
    listen: str | None,
) -> AbstractContextManager[None]:
    """Return the PSU callback listener context for a CLI plan run.

    The listener is only started when the plan will wait for a manual PSU
    authorisation callback and a ``redirectUri`` is configured; otherwise a
    no-op context is returned so auto-approve and non-PSU runs never bind a port.

    Args:
        config: Parsed run config carrying the OAuth ``redirectUri``.
        compiled_plan: Compiled plan to inspect for manual PSU steps.
        listen: Optional ``HOST:PORT`` bind override.

    Returns:
        Context manager that keeps the listener running while entered.
    """
    redirect_uri = config.oauth.redirect_uri if config.oauth is not None else None
    if redirect_uri is None or not compiled_plan_requires_psu_callback(compiled_plan):
        return nullcontext()
    return _announced_listener(redirect_uri, listen=listen)


@contextmanager
def _announced_listener(redirect_uri: str, *, listen: str | None) -> Iterator[None]:
    """Run the callback listener and tell the operator where it is bound.

    Args:
        redirect_uri: Configured OAuth ``redirectUri``.
        listen: Optional ``HOST:PORT`` bind override.

    Yields:
        Control while the listener is running.
    """
    write_notice("[PSU]", "Starting HTTPS callback listener...")
    with psu_callback_listener(redirect_uri, session_store=auth_session_store, listen=listen) as (host, port):
        write_notice("[PSU]", f"Callback listener ready on https://{host}:{port} for {redirect_uri}")
        yield


def _run_cli_compiled_plan(
    *,
    config: ModelBankConfig,
    compiled_plan: CompiledTestPlan,
    runtime_inputs: Mapping[str, JsonValue],
    runtime_input_base_dir: Path,
    logger_sink: PsuAuthorizationUrlConsoleLogger,
    run_id: str,
) -> SmokeCheckResult:
    """Run a compiled catalogue plan from the CLI.

    Args:
        config: Parsed model-bank config.
        compiled_plan: Compiled catalogue plan.
        runtime_inputs: Plan-derived runtime input values.
        runtime_input_base_dir: Directory used for runtime file references.
        logger_sink: Execution logger used by the CLI.
        run_id: Run id used for log/auth correlation.

    Returns:
        Smoke-check result returned by the executor.
    """
    write_notice("[CLI]", "Initialising HTTP/TLS client...")
    http_client = build_json_http_client(
        ca_bundle=config.tls.ca_bundle,
        client_certificate=config.tls.client_certificate,
        client_private_key=config.tls.client_private_key,
    )
    try:
        return run_compiled_test_plan(
            compiled_plan,
            runtime_inputs=runtime_inputs,
            runtime_input_base_dir=runtime_input_base_dir,
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
                oauth_response_type=config.oauth.response_type if config.oauth is not None else None,
                oauth_request_object_signing_alg=(
                    config.oauth.request_object_signing_alg if config.oauth is not None else None
                ),
            ),
            fapi_signing_config=config.fapi_signing,
            mtls_client_configured=(
                config.tls.client_certificate is not None and config.tls.client_private_key is not None
            ),
            approved_release_policy=config.approved_release_policy,
        )
    finally:
        http_client.close()
