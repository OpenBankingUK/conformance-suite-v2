import json
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path

import httpx
import pytest

from conformance import cli
from conformance.catalogue import CatalogueKey, CompiledTestPlan
from conformance.results import SmokeCheckResult

pytestmark = pytest.mark.unit


class _TtyStringIO(StringIO):
    """String buffer that reports TTY status for CLI tests."""

    def isatty(self) -> bool:
        """Return True so tests can exercise interactive CLI output.

        Returns:
            Always True.
        """
        return True


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


def test_cli_returns_config_error_for_invalid_config(tmp_path: Path) -> None:
    config_path = tmp_path / "invalid.json"
    config_path.write_text('{"discoveryUrl": "http://example.com/discovery"}', encoding="utf-8")

    exit_code = cli.run([str(config_path)])

    assert exit_code == 2


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


def test_cli_returns_argparse_error_for_missing_config() -> None:
    exit_code = cli.run([])

    assert exit_code == 2


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


def test_cli_compiles_v311_canonical_plan(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """CLI accepts v3.1.11 and passes only v3.1 requests to execution."""
    plan_path = tmp_path / "v311-plan.json"
    plan_path.write_text(
        json.dumps(
            {
                "schemaVersion": "1.0",
                "specification": {
                    "family": "OBL_READ_WRITE",
                    "version": "3.1.11",
                    "profile": "FAPI1_ADVANCED",
                },
                "executionMode": "development",
                "securityEnvironment": {
                    "discoveryUrl": "https://auth.example.com/.well-known/openid-configuration",
                    "resourceBaseUrl": "https://resource.example.com",
                },
                "resourceGroups": [
                    {
                        "id": "AIS",
                        "endpoints": [
                            {
                                "method": "GET",
                                "path": "/open-banking/v3.1/aisp/accounts",
                            }
                        ],
                    }
                ],
                "businessTestData": {},
                "metadata": {},
            }
        ),
        encoding="utf-8",
    )
    captured_plans: list[CompiledTestPlan] = []

    def run_compiled_plan(**kwargs: object) -> SmokeCheckResult:
        """Capture the compiled plan and return a successful CLI result.

        Args:
            **kwargs: Keyword arguments passed by the CLI execution wrapper.

        Returns:
            Minimal successful smoke-check result.
        """
        compiled_plan = kwargs["compiled_plan"]
        assert isinstance(compiled_plan, CompiledTestPlan)
        captured_plans.append(compiled_plan)
        now = datetime.now(UTC)
        return SmokeCheckResult(status="passed", started_at=now, finished_at=now, steps=())

    monkeypatch.setattr(cli, "_run_cli_compiled_plan", run_compiled_plan)
    monkeypatch.chdir(tmp_path)

    exit_code = cli.run(["--test-plan", str(plan_path)])

    assert exit_code == 0
    assert len(captured_plans) == 1
    assert captured_plans[0].catalogue_key == CatalogueKey(
        "open-banking-uk",
        "3.1.11",
        "read-write",
    )
    assert all(
        "/v4.0/" not in request.path
        for test_case in captured_plans[0].test_cases
        for request in test_case.request_steps
    )
