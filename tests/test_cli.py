import json
from io import StringIO
from pathlib import Path

import httpx
import pytest

from conformance import cli


class _TtyStringIO(StringIO):
    """String buffer that reports TTY status for CLI tests."""

    def isatty(self) -> bool:
        """Return True so tests can exercise interactive CLI output.

        Returns:
            Always True.
        """
        return True


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
    monkeypatch.chdir(tmp_path)

    exit_code = cli.run([str(config_path)])

    assert exit_code == 0
    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert result["status"] == "passed"
    assert result["summary"] == {"total": 2, "passed": 2, "failed": 0, "warn": 0, "skipped": 0}


@pytest.mark.unit
def test_cli_runs_discovery_only_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    requested_urls: list[str] = []
    config_path = tmp_path / "model-bank.json"
    config_path.write_text(
        json.dumps(
            {
                "environment": "ozone-model-bank",
                "discoveryUrl": "https://auth1.obie.uk.ozoneapi.io/.well-known/openid-configuration",
                "followUp": {"mode": "discovery_only"},
            }
        ),
        encoding="utf-8",
    )

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

    exit_code = cli.run([str(config_path)])

    assert exit_code == 0
    assert requested_urls == ["https://auth1.obie.uk.ozoneapi.io/.well-known/openid-configuration"]
    result_path = tmp_path / "out" / "test-results.json"
    assert result_path.parent.is_dir()
    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert result["status"] == "passed"
    assert result["summary"] == {"total": 1, "passed": 1, "failed": 0, "warn": 0, "skipped": 0}


@pytest.mark.unit
def test_cli_rejects_removed_manifest_flag(tmp_path: Path) -> None:
    config_path = tmp_path / "model-bank.json"
    config_path.write_text(
        json.dumps(
            {
                "environment": "test-env",
                "discoveryUrl": "https://example.com/.well-known/openid-configuration",
                "resultOutputPath": str(tmp_path / "result.json"),
            }
        ),
        encoding="utf-8",
    )

    exit_code = cli.run([str(config_path), "--manifest", "manifest.json"])

    assert exit_code == 2


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
    monkeypatch.chdir(tmp_path)

    exit_code = cli.run([str(config_path)])

    assert exit_code == 1
    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert result["status"] == "failed"
    assert result["summary"] == {"total": 1, "passed": 0, "failed": 1, "warn": 0, "skipped": 0}


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
    monkeypatch.chdir(tmp_path)

    exit_code = cli.run([str(config_path)])

    assert exit_code == 3


@pytest.mark.unit
def test_cli_returns_config_error_for_invalid_config(tmp_path: Path) -> None:
    config_path = tmp_path / "invalid.json"
    config_path.write_text('{"discoveryUrl": "http://example.com/discovery"}', encoding="utf-8")

    exit_code = cli.run([str(config_path)])

    assert exit_code == 2


@pytest.mark.unit
def test_cli_rejects_removed_plan_spec_flag(tmp_path: Path) -> None:
    """The legacy --plan-spec public execution path is no longer available."""
    config_path = tmp_path / "model-bank.json"
    plan_spec_path = tmp_path / "plan-spec.json"
    config_path.write_text(
        json.dumps(
            {
                "environment": "ozone-model-bank",
                "discoveryUrl": "https://modelbank.example.com/.well-known/openid-configuration",
                "resultOutputPath": str(tmp_path / "result.json"),
            }
        ),
        encoding="utf-8",
    )
    plan_spec_path.write_text("{}", encoding="utf-8")

    exit_code = cli.run([str(config_path), "--plan-spec", str(plan_spec_path)])

    assert exit_code == 2


@pytest.mark.unit
def test_cli_returns_argparse_error_for_missing_config() -> None:
    exit_code = cli.run([])

    assert exit_code == 2


@pytest.mark.unit
def test_cli_writes_execution_log_ndjson(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """CLI writes an NDJSON execution log alongside the result file."""
    config_path = tmp_path / "model-bank.json"
    result_path = tmp_path / "result.json"
    log_path = tmp_path / "execution.ndjson"
    config_path.write_text(
        json.dumps(
            {
                "environment": "ozone-model-bank",
                "discoveryUrl": "https://modelbank.example.com/.well-known/openid-configuration",
                "followUp": {"mode": "discovery_only"},
                "resultOutputPath": str(result_path),
                "executionLogPath": str(log_path),
            }
        ),
        encoding="utf-8",
    )

    original_client = httpx.Client

    def mock_client(*, timeout: float, verify: bool | str, cert: tuple[str, str] | None) -> httpx.Client:
        return original_client(
            transport=httpx.MockTransport(
                lambda _r: httpx.Response(
                    200,
                    json={
                        "issuer": "https://modelbank.example.com",
                        "jwks_uri": "https://modelbank.example.com/jwks",
                    },
                )
            )
        )

    monkeypatch.setattr(httpx, "Client", mock_client)
    monkeypatch.chdir(tmp_path)

    exit_code = cli.run([str(config_path)])

    assert exit_code == 0
    assert log_path.is_file()
    lines = log_path.read_text(encoding="utf-8").rstrip("\n").split("\n")
    parsed = [json.loads(line) for line in lines]
    types = [event["type"] for event in parsed]
    assert types[0] == "run-started"
    assert types[-1] == "run-completed"
    # RFC 3339 with Z suffix per the plan's verification step
    assert all(event["timestamp"].endswith("Z") for event in parsed)


@pytest.mark.unit
def test_cli_developer_mode_warn_line_logged(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """CONFORMANCE_DEVELOPER_MODE=true emits a prominent WARN startup line."""
    monkeypatch.setenv("CONFORMANCE_DEVELOPER_MODE", "true")
    config_path = tmp_path / "model-bank.json"
    config_path.write_text(
        json.dumps(
            {
                "environment": "env",
                "discoveryUrl": "https://modelbank.example.com/.well-known/openid-configuration",
                "followUp": {"mode": "discovery_only"},
                "resultOutputPath": str(tmp_path / "r.json"),
                "executionLogPath": str(tmp_path / "log.ndjson"),
            }
        ),
        encoding="utf-8",
    )

    original_client = httpx.Client

    def mock_client(*, timeout: float, verify: bool | str, cert: tuple[str, str] | None) -> httpx.Client:
        return original_client(
            transport=httpx.MockTransport(
                lambda _r: httpx.Response(
                    200,
                    json={
                        "issuer": "https://modelbank.example.com",
                        "jwks_uri": "https://modelbank.example.com/jwks",
                    },
                )
            )
        )

    monkeypatch.setattr(httpx, "Client", mock_client)
    monkeypatch.chdir(tmp_path)

    with caplog.at_level("WARNING", logger="conformance.execution_log"):
        cli.run([str(config_path)])

    assert any("CONFORMANCE_DEVELOPER_MODE" in record.message for record in caplog.records)


@pytest.mark.unit
def test_cli_returns_exit_code_3_when_execution_log_cannot_be_written(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A failed execution-log write returns exit code 3, mirroring the result-file behaviour."""
    config_path = tmp_path / "model-bank.json"
    log_path = tmp_path / "log.ndjson"
    log_path.mkdir()  # Make the destination a directory so write fails.
    config_path.write_text(
        json.dumps(
            {
                "environment": "env",
                "discoveryUrl": "https://modelbank.example.com/.well-known/openid-configuration",
                "followUp": {"mode": "discovery_only"},
                "resultOutputPath": str(tmp_path / "r.json"),
                "executionLogPath": str(log_path),
            }
        ),
        encoding="utf-8",
    )

    original_client = httpx.Client

    def mock_client(*, timeout: float, verify: bool | str, cert: tuple[str, str] | None) -> httpx.Client:
        return original_client(
            transport=httpx.MockTransport(
                lambda _r: httpx.Response(
                    200,
                    json={
                        "issuer": "https://modelbank.example.com",
                        "jwks_uri": "https://modelbank.example.com/jwks",
                    },
                )
            )
        )

    monkeypatch.setattr(httpx, "Client", mock_client)
    monkeypatch.chdir(tmp_path)

    exit_code = cli.run([str(config_path)])
    assert exit_code == 3


@pytest.mark.unit
def test_cli_rejects_removed_deselect_flag(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "environment": "test",
                "discoveryUrl": "https://example.com/.well-known/openid-configuration",
            }
        ),
        encoding="utf-8",
    )

    exit_code = cli.run([str(config_path), "--deselect", "any"])

    assert exit_code == 2
