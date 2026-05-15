import json
from pathlib import Path

import httpx
import pytest

from conformance import cli

REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_CONFIG_PATH = REPO_ROOT / "config" / "model-bank-example.json"


@pytest.mark.unit
def test_cli_writes_result_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    config_path = tmp_path / "model-bank.json"
    result_path = tmp_path / "result.json"
    config_path.write_text(
        json.dumps(
            {
                "environment": "ozone-model-bank",
                "discoveryUrl": "https://modelbank.example.com/.well-known/openid-configuration",
                "resultOutputPath": str(result_path),
            }
        ),
        encoding="utf-8",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == "https://modelbank.example.com/.well-known/openid-configuration":
            return httpx.Response(
                200,
                json={
                    "issuer": "https://modelbank.example.com",
                    "jwks_uri": "https://modelbank.example.com/jwks",
                },
            )
        return httpx.Response(200, json={"keys": []})

    original_client = httpx.Client

    def mock_client(*, timeout: float, verify: bool | str, cert: tuple[str, str] | None) -> httpx.Client:
        return original_client(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(httpx, "Client", mock_client)

    exit_code = cli.run([str(config_path)])

    assert exit_code == 0
    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert result["status"] == "passed"
    assert result["summary"] == {"total": 2, "passed": 2, "failed": 0}


@pytest.mark.unit
def test_cli_runs_committed_example_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    requested_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_urls.append(str(request.url))
        return httpx.Response(
            200,
            json={
                "issuer": "https://auth1.obie.uk.ozoneapi.io",
                "jwks_uri": "https://keystore.openbankingtest.org.uk/example.jwks",
            },
        )

    original_client = httpx.Client

    def mock_client(*, timeout: float, verify: bool | str, cert: tuple[str, str] | None) -> httpx.Client:
        return original_client(transport=httpx.MockTransport(handler))

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(httpx, "Client", mock_client)

    exit_code = cli.run([str(EXAMPLE_CONFIG_PATH)])

    assert exit_code == 0
    assert requested_urls == ["https://auth1.obie.uk.ozoneapi.io/.well-known/openid-configuration"]
    result = json.loads((tmp_path / "test-results.json").read_text(encoding="utf-8"))
    assert result["status"] == "passed"
    assert result["summary"] == {"total": 1, "passed": 1, "failed": 0}


@pytest.mark.unit
def test_cli_returns_failure_when_model_bank_check_fails(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    config_path = tmp_path / "model-bank.json"
    result_path = tmp_path / "result.json"
    config_path.write_text(
        json.dumps(
            {
                "environment": "ozone-model-bank",
                "discoveryUrl": "https://modelbank.example.com/.well-known/openid-configuration",
                "resultOutputPath": str(result_path),
            }
        ),
        encoding="utf-8",
    )

    original_client = httpx.Client

    def mock_client(*, timeout: float, verify: bool | str, cert: tuple[str, str] | None) -> httpx.Client:
        return original_client(transport=httpx.MockTransport(lambda _request: httpx.Response(500)))

    monkeypatch.setattr(httpx, "Client", mock_client)

    exit_code = cli.run([str(config_path)])

    assert exit_code == 1
    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert result["status"] == "failed"
    assert result["summary"] == {"total": 1, "passed": 0, "failed": 1}


@pytest.mark.unit
def test_cli_returns_write_error_when_result_file_cannot_be_written(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "model-bank.json"
    result_path = tmp_path / "result.json"
    result_path.mkdir()
    config_path.write_text(
        json.dumps(
            {
                "environment": "ozone-model-bank",
                "discoveryUrl": "https://modelbank.example.com/.well-known/openid-configuration",
                "followUp": {"mode": "discovery_only"},
                "resultOutputPath": str(result_path),
            }
        ),
        encoding="utf-8",
    )

    original_client = httpx.Client

    def mock_client(*, timeout: float, verify: bool | str, cert: tuple[str, str] | None) -> httpx.Client:
        return original_client(
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(
                    200,
                    json={
                        "issuer": "https://modelbank.example.com",
                        "jwks_uri": "https://modelbank.example.com/jwks",
                    },
                )
            )
        )

    monkeypatch.setattr(httpx, "Client", mock_client)

    exit_code = cli.run([str(config_path)])

    assert exit_code == 3


@pytest.mark.unit
def test_cli_returns_config_error_for_invalid_config(tmp_path: Path) -> None:
    config_path = tmp_path / "invalid.json"
    config_path.write_text("{}", encoding="utf-8")

    exit_code = cli.run([str(config_path)])

    assert exit_code == 2


@pytest.mark.unit
def test_cli_returns_argparse_error_for_missing_config() -> None:
    exit_code = cli.run([])

    assert exit_code == 2
