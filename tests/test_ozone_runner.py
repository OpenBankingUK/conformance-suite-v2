from pathlib import Path

import httpx
import pytest

from conformance.model_bank_config import ModelBankConfig
from conformance.ozone_client import OzoneModelBankClient
from conformance.runner import run_model_bank_smoke_check


@pytest.mark.unit
def test_run_model_bank_smoke_check_preserves_client_construction_error(monkeypatch: pytest.MonkeyPatch) -> None:
    expected_error = RuntimeError("invalid client configuration")

    def raise_client_construction_error(_config: ModelBankConfig) -> OzoneModelBankClient:
        raise expected_error

    monkeypatch.setattr(OzoneModelBankClient, "from_config", raise_client_construction_error)
    config = ModelBankConfig(
        environment="ozone-model-bank",
        discovery_url="https://modelbank.example.com/.well-known/openid-configuration",
        result_output_path=Path("results.json"),
    )

    with pytest.raises(RuntimeError) as exc_info:
        run_model_bank_smoke_check(config)

    assert exc_info.value is expected_error


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

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
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
    with httpx.Client(transport=httpx.MockTransport(lambda _request: httpx.Response(500))) as http_client:
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

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
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
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                200,
                json={
                    "issuer": "https://modelbank.example.com",
                    "jwks_uri": "http://modelbank.example.com/jwks",
                },
            )
        )
    ) as http_client:
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
def test_run_model_bank_smoke_check_rejects_non_https_issuer() -> None:
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                200,
                json={
                    "issuer": "http://modelbank.example.com",
                    "jwks_uri": "https://modelbank.example.com/jwks",
                },
            )
        )
    ) as http_client:
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
    assert result.steps[0].message == "issuer must be an HTTPS URL"


@pytest.mark.unit
def test_run_model_bank_smoke_check_rejects_issuer_userinfo() -> None:
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                200,
                json={
                    "issuer": "https://client@modelbank.example.com",
                    "jwks_uri": "https://modelbank.example.com/jwks",
                },
            )
        )
    ) as http_client:
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
    assert result.steps[0].message == "issuer must not include credentials"


@pytest.mark.unit
def test_run_model_bank_smoke_check_rejects_jwks_uri_userinfo() -> None:
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                200,
                json={
                    "issuer": "https://modelbank.example.com",
                    "jwks_uri": "https://client@modelbank.example.com/jwks",
                },
            )
        )
    ) as http_client:
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

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
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
