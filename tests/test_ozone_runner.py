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
    assert result.to_json_object()["summary"] == {"total": 2, "passed": 2, "failed": 0, "warn": 0, "skipped": 0}


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


@pytest.mark.unit
@pytest.mark.parametrize(
    ("field", "url", "expected_message"),
    [
        ("issuer", "https://127.0.0.1", "issuer must use a DNS hostname, not an IP literal"),
        ("jwks_uri", "https://127.0.0.1/jwks", "jwks_uri must use a DNS hostname, not an IP literal"),
        ("issuer", "https://bad_host.example", "issuer must be a valid HTTPS URL"),
        ("jwks_uri", "https://bad_host.example/jwks", "jwks_uri must be a valid HTTPS URL"),
    ],
)
def test_run_model_bank_smoke_check_rejects_ip_literal_and_malformed_hostname(
    field: str, url: str, expected_message: str
) -> None:
    discovery_body: dict[str, str] = {
        "issuer": "https://modelbank.example.com",
        "jwks_uri": "https://modelbank.example.com/jwks",
    }
    discovery_body[field] = url

    with httpx.Client(
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, json=discovery_body))
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
    assert result.steps[0].message == expected_message


@pytest.mark.unit
def test_run_model_bank_smoke_check_emits_event_sequence_on_success() -> None:
    """Successful run emits run-started, step-started/response/step-completed pairs, then run-completed."""
    from conformance.execution_log import BufferedExecutionLogger

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url).endswith("openid-configuration"):
            return httpx.Response(
                200,
                json={
                    "issuer": "https://modelbank.example.com",
                    "jwks_uri": "https://modelbank.example.com/jwks",
                },
            )
        return httpx.Response(200, json={"keys": [{"kid": "k"}]})

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        client = OzoneModelBankClient(http_client)
        config = ModelBankConfig(
            environment="env",
            discovery_url="https://modelbank.example.com/.well-known/openid-configuration",
            result_output_path=Path("r.json"),
        )
        execution_logger = BufferedExecutionLogger(run_id="run-1", developer_mode=False)
        run_model_bank_smoke_check(config, client=client, execution_logger=execution_logger)

    events = list(execution_logger.events())
    types = [event.type for event in events]
    assert types[0] == "run-started"
    assert types[-1] == "run-completed"
    assert types.count("step-started") == 2
    assert types.count("step-completed") == 2
    assert types.count("response-received") == 2
    # Each outbound HTTP call must emit `request-sent` per the documented event
    # taxonomy (README / DEVELOPER_GUIDE), and each `request-sent` must precede
    # the matching `response-received` for the same step.
    assert types.count("request-sent") == 2
    for step_id in ("openid-discovery", "jwks"):
        step_event_types = [event.type for event in events if event.step_id == step_id]
        assert step_event_types.index("request-sent") < step_event_types.index("response-received"), (
            f"request-sent must precede response-received for step {step_id!r}; got {step_event_types}"
        )


@pytest.mark.unit
def test_run_model_bank_smoke_check_emits_application_error_on_discovery_failure() -> None:
    """Discovery transport failure emits an application-error event."""
    from conformance.execution_log import BufferedExecutionLogger

    with httpx.Client(transport=httpx.MockTransport(lambda _r: httpx.Response(500))) as http_client:
        client = OzoneModelBankClient(http_client)
        config = ModelBankConfig(
            environment="env",
            discovery_url="https://modelbank.example.com/.well-known/openid-configuration",
            result_output_path=Path("r.json"),
        )
        execution_logger = BufferedExecutionLogger(run_id="run-1", developer_mode=False)
        run_model_bank_smoke_check(config, client=client, execution_logger=execution_logger)

    types = [event.type for event in execution_logger.events()]
    assert "application-error" in types
    assert types[-1] == "run-completed"


@pytest.mark.unit
def test_run_model_bank_smoke_check_emits_application_error_on_engine_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unexpected engine exception emits application-error then re-raises.

    If an exception escapes before any step starts (e.g. from_config raises),
    the log must still contain an application-error event so the log contract
    introduced by PR #22 is upheld: run-started is always followed by a
    terminal event.
    """
    from conformance.execution_log import BufferedExecutionLogger

    expected_error = RuntimeError("unexpected engine failure")

    def raise_from_config(_config: ModelBankConfig) -> OzoneModelBankClient:
        raise expected_error

    monkeypatch.setattr(OzoneModelBankClient, "from_config", raise_from_config)
    config = ModelBankConfig(
        environment="ozone-model-bank",
        discovery_url="https://modelbank.example.com/.well-known/openid-configuration",
        result_output_path=Path("results.json"),
    )
    execution_logger = BufferedExecutionLogger(run_id="run-1", developer_mode=False)

    with pytest.raises(RuntimeError) as exc_info:
        run_model_bank_smoke_check(config, execution_logger=execution_logger)

    assert exc_info.value is expected_error
    types = [event.type for event in execution_logger.events()]
    assert types[0] == "run-started"
    assert types[-1] == "application-error"
