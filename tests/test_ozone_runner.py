from pathlib import Path

import httpx
import pytest

from conformance.model_bank_config import ModelBankConfig
from conformance.ozone_client import OzoneModelBankClient
from conformance.runner import run_model_bank_smoke_check


@pytest.mark.unit
def test_run_model_bank_smoke_check_fetches_discovery_and_jwks() -> None:
    requested_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_urls.append(str(request.url))
        if str(request.url) == "https://modelbank.example.com/.well-known/openid-configuration":
            return httpx.Response(
                200,
                json={
                    "issuer": "https://modelbank.example.com",
                    "jwks_uri": "https://modelbank.example.com/jwks",
                },
            )
        if str(request.url) == "https://modelbank.example.com/jwks":
            return httpx.Response(200, json={"keys": [{"kid": "signing-key"}]})
        return httpx.Response(404, json={"error": "not found"})

    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    client = OzoneModelBankClient(http_client)
    config = ModelBankConfig(
        environment="ozone-model-bank",
        discovery_url="https://modelbank.example.com/.well-known/openid-configuration",
        result_output_path=Path("results.json"),
    )

    result = run_model_bank_smoke_check(config, client=client)

    assert result.status == "passed"
    assert requested_urls == [
        "https://modelbank.example.com/.well-known/openid-configuration",
        "https://modelbank.example.com/jwks",
    ]
    assert result.to_json_object()["summary"] == {"total": 2, "passed": 2, "failed": 0}


@pytest.mark.unit
def test_run_model_bank_smoke_check_reports_discovery_failure() -> None:
    http_client = httpx.Client(transport=httpx.MockTransport(lambda _request: httpx.Response(500)))
    client = OzoneModelBankClient(http_client)
    config = ModelBankConfig(
        environment="ozone-model-bank",
        discovery_url="https://modelbank.example.com/.well-known/openid-configuration",
        result_output_path=Path("results.json"),
    )

    result = run_model_bank_smoke_check(config, client=client)

    assert result.status == "failed"
    assert result.steps[0].name == "openid-discovery"
    assert result.steps[0].status == "failed"


@pytest.mark.unit
def test_run_model_bank_smoke_check_reports_jwks_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == "https://modelbank.example.com/.well-known/openid-configuration":
            return httpx.Response(
                200,
                json={
                    "issuer": "https://modelbank.example.com",
                    "jwks_uri": "https://modelbank.example.com/jwks",
                },
            )
        return httpx.Response(200, json={"not_keys": []})

    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    client = OzoneModelBankClient(http_client)
    config = ModelBankConfig(
        environment="ozone-model-bank",
        discovery_url="https://modelbank.example.com/.well-known/openid-configuration",
        result_output_path=Path("results.json"),
    )

    result = run_model_bank_smoke_check(config, client=client)

    assert result.status == "failed"
    assert result.steps[0].status == "passed"
    assert result.steps[1].name == "jwks"
    assert result.steps[1].status == "failed"


@pytest.mark.unit
def test_run_model_bank_smoke_check_rejects_non_https_jwks_uri() -> None:
    http_client = httpx.Client(
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                200,
                json={
                    "issuer": "https://modelbank.example.com",
                    "jwks_uri": "http://modelbank.example.com/jwks",
                },
            )
        )
    )
    client = OzoneModelBankClient(http_client)
    config = ModelBankConfig(
        environment="ozone-model-bank",
        discovery_url="https://modelbank.example.com/.well-known/openid-configuration",
        result_output_path=Path("results.json"),
    )

    result = run_model_bank_smoke_check(config, client=client)

    assert result.status == "failed"
    assert result.steps[0].name == "openid-discovery"
    assert result.steps[0].status == "failed"
    assert result.steps[0].message == "jwks_uri must be an HTTPS URL"


@pytest.mark.unit
def test_run_model_bank_smoke_check_rejects_jwks_uri_userinfo() -> None:
    http_client = httpx.Client(
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                200,
                json={
                    "issuer": "https://modelbank.example.com",
                    "jwks_uri": "https://client@modelbank.example.com/jwks",
                },
            )
        )
    )
    client = OzoneModelBankClient(http_client)
    config = ModelBankConfig(
        environment="ozone-model-bank",
        discovery_url="https://modelbank.example.com/.well-known/openid-configuration",
        result_output_path=Path("results.json"),
    )

    result = run_model_bank_smoke_check(config, client=client)

    assert result.status == "failed"
    assert result.steps[0].name == "openid-discovery"
    assert result.steps[0].status == "failed"
    assert result.steps[0].message == "jwks_uri must not include credentials"


@pytest.mark.unit
def test_run_model_bank_smoke_check_can_stop_after_discovery() -> None:
    requested_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_urls.append(str(request.url))
        return httpx.Response(
            200,
            json={
                "issuer": "https://modelbank.example.com",
                "jwks_uri": "https://modelbank.example.com/jwks",
            },
        )

    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    client = OzoneModelBankClient(http_client)
    config = ModelBankConfig(
        environment="ozone-model-bank",
        discovery_url="https://modelbank.example.com/.well-known/openid-configuration",
        follow_up_mode="discovery_only",
    )

    result = run_model_bank_smoke_check(config, client=client)

    assert result.status == "passed"
    assert requested_urls == ["https://modelbank.example.com/.well-known/openid-configuration"]
    assert len(result.steps) == 1
