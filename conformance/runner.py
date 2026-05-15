from __future__ import annotations

from datetime import UTC, datetime

from conformance.model_bank_config import ModelBankConfig
from conformance.ozone_client import OzoneClientError, OzoneModelBankClient
from conformance.results import SmokeCheckResult, StepResult, build_smoke_check_result


def run_model_bank_smoke_check(
    config: ModelBankConfig,
    *,
    client: OzoneModelBankClient | None = None,
) -> SmokeCheckResult:
    started_at = datetime.now(UTC)
    steps: list[StepResult] = []
    owns_client = client is None
    model_bank_client = client if client is not None else OzoneModelBankClient.from_config(config)

    try:
        try:
            discovery_document, discovery_response = model_bank_client.fetch_discovery_document(config.discovery_url)
        except OzoneClientError as error:
            steps.append(
                StepResult(
                    name="openid-discovery",
                    status="failed",
                    message=str(error),
                    url=config.discovery_url,
                )
            )
            return build_smoke_check_result(config.environment, steps, started_at=started_at)

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

        if config.follow_up_mode == "discovery_only":
            return build_smoke_check_result(config.environment, steps, started_at=started_at)

        try:
            jwks_response = model_bank_client.fetch_jwks(discovery_document.jwks_uri)
        except OzoneClientError as error:
            steps.append(
                StepResult(
                    name="jwks",
                    status="failed",
                    message=str(error),
                    url=discovery_document.jwks_uri,
                )
            )
            return build_smoke_check_result(config.environment, steps, started_at=started_at)

        keys = jwks_response.body.get("keys")
        key_count = len(keys) if isinstance(keys, list) else 0
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
        return build_smoke_check_result(config.environment, steps, started_at=started_at)
    finally:
        if owns_client:
            model_bank_client.close()
