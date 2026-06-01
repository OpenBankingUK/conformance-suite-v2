"""Orchestrate the current model-bank discovery and JWKS smoke check."""

from __future__ import annotations

from datetime import UTC, datetime

from conformance.execution_log import ExecutionLogger, NullExecutionLogger
from conformance.model_bank_config import ModelBankConfig
from conformance.ozone_client import OzoneClientError, OzoneModelBankClient
from conformance.results import SmokeCheckResult, StepResult, build_smoke_check_result


def run_model_bank_smoke_check(
    config: ModelBankConfig,
    *,
    client: OzoneModelBankClient | None = None,
    execution_logger: ExecutionLogger | None = None,
) -> SmokeCheckResult:
    """Run the model-bank smoke check and return a structured result.

    Args:
        config: Validated model-bank runtime configuration.
        client: Optional preconfigured client used by tests or callers that own
            the HTTP client lifecycle.
        execution_logger: Optional structured execution-log sink. Defaults to
            a :class:`NullExecutionLogger` so existing callers keep working.

    Returns:
        Smoke-check result containing ordered discovery and optional JWKS steps.
    """
    logger_sink: ExecutionLogger = execution_logger or NullExecutionLogger()
    logger_sink.emit(
        "run-started",
        payload={"environment": config.environment, "mode": "model-bank-smoke-check"},
    )
    started_at = datetime.now(UTC)
    steps: list[StepResult] = []
    owns_client = client is None
    model_bank_client: OzoneModelBankClient | None = client

    try:
        try:
            if model_bank_client is None:
                model_bank_client = OzoneModelBankClient.from_config(config)

            logger_sink.emit(
                "step-started",
                step_id="openid-discovery",
                payload={"url": config.discovery_url},
            )
            try:
                discovery_document, discovery_response = model_bank_client.fetch_discovery_document(
                    config.discovery_url
                )
            except OzoneClientError as error:
                logger_sink.emit(
                    "application-error",
                    step_id="openid-discovery",
                    payload={"message": str(error)},
                )
                steps.append(
                    StepResult(
                        name="openid-discovery",
                        status="failed",
                        message=str(error),
                        url=config.discovery_url,
                    )
                )
                logger_sink.emit(
                    "step-completed",
                    step_id="openid-discovery",
                    payload={"status": "failed", "message": str(error)},
                )
                return _finalise(config.environment, steps, started_at=started_at, logger_sink=logger_sink)

            logger_sink.emit(
                "response-received",
                step_id="openid-discovery",
                payload={"statusCode": discovery_response.status_code, "url": discovery_response.url},
            )
            steps.append(
                StepResult(
                    name="openid-discovery",
                    status="passed",
                    message="Fetched OpenID discovery document",
                    url=discovery_response.url,
                    status_code=discovery_response.status_code,
                    details={"issuer": discovery_document.issuer, "jwksUri": discovery_document.jwks_uri},
                )
            )
            logger_sink.emit(
                "step-completed",
                step_id="openid-discovery",
                payload={"status": "passed", "statusCode": discovery_response.status_code},
            )

            if config.follow_up_mode == "discovery_only":
                return _finalise(config.environment, steps, started_at=started_at, logger_sink=logger_sink)

            logger_sink.emit("step-started", step_id="jwks", payload={"url": discovery_document.jwks_uri})
            try:
                jwks_response = model_bank_client.fetch_jwks(discovery_document.jwks_uri)
            except OzoneClientError as error:
                logger_sink.emit(
                    "application-error",
                    step_id="jwks",
                    payload={"message": str(error)},
                )
                steps.append(
                    StepResult(
                        name="jwks",
                        status="failed",
                        message=str(error),
                        url=discovery_document.jwks_uri,
                    )
                )
                logger_sink.emit(
                    "step-completed",
                    step_id="jwks",
                    payload={"status": "failed", "message": str(error)},
                )
                return _finalise(config.environment, steps, started_at=started_at, logger_sink=logger_sink)

            keys = jwks_response.body.get("keys")
            key_count = len(keys) if isinstance(keys, list) else 0
            logger_sink.emit(
                "response-received",
                step_id="jwks",
                payload={"statusCode": jwks_response.status_code, "url": jwks_response.url},
            )
            steps.append(
                StepResult(
                    name="jwks",
                    status="passed",
                    message="Fetched JWKS document",
                    url=jwks_response.url,
                    status_code=jwks_response.status_code,
                    details={"keyCount": key_count},
                )
            )
            logger_sink.emit(
                "step-completed",
                step_id="jwks",
                payload={"status": "passed", "statusCode": jwks_response.status_code, "keyCount": key_count},
            )
            return _finalise(config.environment, steps, started_at=started_at, logger_sink=logger_sink)
        finally:
            if owns_client and model_bank_client is not None:
                model_bank_client.close()
    except Exception as error:
        logger_sink.emit("application-error", payload={"message": str(error)})
        raise


def _finalise(
    environment: str,
    steps: list[StepResult],
    *,
    started_at: datetime,
    logger_sink: ExecutionLogger,
) -> SmokeCheckResult:
    """Build the aggregate result and emit the terminating ``run-completed`` event.

    Args:
        environment: Environment name copied into the result file.
        steps: Ordered step results collected by the smoke-check run.
        started_at: UTC timestamp captured before execution began.
        logger_sink: Execution-log sink that receives the ``run-completed`` event.

    Returns:
        Aggregate smoke-check result returned to the caller.
    """
    result = build_smoke_check_result(environment, steps, started_at=started_at)
    logger_sink.emit(
        "run-completed",
        payload={
            "status": result.status,
            "summary": {
                "total": len(result.steps),
                "passed": sum(1 for step in result.steps if step.status == "passed"),
                "failed": sum(1 for step in result.steps if step.status == "failed"),
                "warn": sum(1 for step in result.steps if step.status == "warn"),
                "skipped": sum(1 for step in result.steps if step.status == "skipped"),
            },
        },
    )
    return result
