import contextlib
import json
import os
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path

import httpx
import pytest

from conformance import BETA_NOTICE, FEEDBACK_EMAIL, cli
from conformance.catalogue import CatalogueKey, CompiledTestPlan
from conformance.cli_callback import CallbackListenerError
from conformance.results import SmokeCheckResult
from tests.support.run_config import RUN_READY_SECURITY_ENVIRONMENT

pytestmark = pytest.mark.unit


class _TtyStringIO(StringIO):
    """String buffer that reports TTY status for CLI tests."""

    def isatty(self) -> bool:
        """Return True so tests can exercise interactive CLI output.

        Returns:
            Always True.
        """
        return True


def test_cli_writes_result_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
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
    captured = capsys.readouterr()
    assert captured.err.count(BETA_NOTICE) == 1
    assert f"Any feedback? Please email {FEEDBACK_EMAIL}." in captured.err
    assert f"send SIGTERM to Python PID {os.getpid()}" in captured.err
    assert BETA_NOTICE not in captured.out
    assert "[Run +" in captured.err
    assert 'Step "openid-discovery" started' in captured.err
    assert 'Step "openid-discovery" passed' in captured.err
    assert "[CLI] Writing result and execution log..." in captured.err
    execution_log = tmp_path / "out/execution-log.ndjson"
    events = [json.loads(line) for line in execution_log.read_text().splitlines()]
    assert events[0]["type"] == "run-started"
    assert events[-1]["type"] == "run-completed"


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
                    "openApiDocumentUpdate": "Release-5",
                    "profile": "FAPI1_ADVANCED",
                },
                "executionMode": "development",
                "securityEnvironment": {
                    **RUN_READY_SECURITY_ENVIRONMENT,
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
    monkeypatch.setattr(cli, "_callback_listener_for", lambda *_args, **_kwargs: contextlib.nullcontext())
    monkeypatch.chdir(tmp_path)

    exit_code = cli.run(["--test-plan", str(plan_path)])

    assert exit_code == 0
    assert len(captured_plans) == 1
    assert captured_plans[0].catalogue_key == CatalogueKey(
        "open-banking-uk",
        "3.1.11",
        "read-write",
        "3.1.11",
    )
    assert all(
        "/v4.0/" not in request.path
        for test_case in captured_plans[0].test_cases
        for request in test_case.request_steps
    )


def _write_v311_plan(tmp_path: Path) -> Path:
    """Write a minimal run-ready v3.1.11 canonical plan and return its path."""
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(
        json.dumps(
            {
                "schemaVersion": "1.0",
                "specification": {
                    "family": "OBL_READ_WRITE",
                    "version": "3.1.11",
                    "openApiDocumentUpdate": "Release-5",
                    "profile": "FAPI1_ADVANCED",
                },
                "executionMode": "development",
                "securityEnvironment": {
                    **RUN_READY_SECURITY_ENVIRONMENT,
                    "discoveryUrl": "https://auth.example.com/.well-known/openid-configuration",
                    "resourceBaseUrl": "https://resource.example.com",
                },
                "resourceGroups": [
                    {"id": "AIS", "endpoints": [{"method": "GET", "path": "/open-banking/v3.1/aisp/accounts"}]}
                ],
                "businessTestData": {},
                "metadata": {},
            }
        ),
        encoding="utf-8",
    )
    return plan_path


def test_cli_returns_2_when_callback_listener_cannot_start(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A listener bind failure is a configuration error and never runs the plan."""
    plan_path = _write_v311_plan(tmp_path)
    ran: list[object] = []

    def failing_listener(*_args: object, **_kwargs: object) -> contextlib.AbstractContextManager[None]:
        """Raise the error the real listener raises when it cannot bind."""
        raise CallbackListenerError("cannot listen on 127.0.0.1:443; pass --callback-listen HOST:PORT")

    monkeypatch.setattr(cli, "_callback_listener_for", failing_listener)
    monkeypatch.setattr(cli, "_run_cli_compiled_plan", lambda **kwargs: ran.append(kwargs))
    monkeypatch.chdir(tmp_path)

    assert cli.run(["--test-plan", str(plan_path)]) == 2
    assert ran == []


def test_cli_prints_run_summary_to_stdout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The CLI writes a human-readable verdict and file pointers to stdout."""
    plan_path = _write_v311_plan(tmp_path)

    def run_compiled_plan(**_kwargs: object) -> SmokeCheckResult:
        """Return a minimal passing result."""
        now = datetime.now(UTC)
        return SmokeCheckResult(status="passed", started_at=now, finished_at=now, steps=())

    monkeypatch.setattr(cli, "_run_cli_compiled_plan", run_compiled_plan)
    monkeypatch.setattr(cli, "_callback_listener_for", lambda *_args, **_kwargs: contextlib.nullcontext())
    monkeypatch.chdir(tmp_path)

    assert cli.run(["--test-plan", str(plan_path)]) == 0

    captured = capsys.readouterr()
    stdout = captured.out
    assert "Conformance run PASSED" in stdout
    assert "Result file:" in stdout
    assert "conformance.result_gate" in stdout
    assert captured.err.count(BETA_NOTICE) == 1
    assert "[CLI] Reading, validating and compiling test plan..." in captured.err
    assert "[CLI] Test plan ready in " in captured.err
    result = json.loads((tmp_path / "out/test-results.json").read_text())
    assert result["status"] == "passed"
    assert result["testPlanValidation"]["valid"] is True


def test_cli_beta_notice_precedes_invalid_plan_error(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    plan_path = tmp_path / "invalid.json"
    plan_path.write_text("{invalid", encoding="utf-8")

    assert cli.run(["--test-plan", str(plan_path)]) == 2
    captured = capsys.readouterr()
    assert captured.err.count(BETA_NOTICE) == 1
    assert "[CLI] Reading, validating and compiling test plan..." in captured.err
    assert "Test plan ready" not in captured.err
    assert captured.out == ""


def test_cli_help_does_not_print_run_notice(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.run(["--help"]) == 0
    captured = capsys.readouterr()
    assert "--test-plan" in captured.out
    assert BETA_NOTICE not in captured.err


@pytest.mark.parametrize(
    ("stdout_tty", "stderr_tty", "no_colour"), [(True, False, False), (False, True, False), (True, True, True)]
)
def test_cli_colour_uses_each_stream_without_styling_result_files(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, stdout_tty: bool, stderr_tty: bool, no_colour: bool
) -> None:
    plan_path = _write_v311_plan(tmp_path)
    stdout = _TtyStringIO() if stdout_tty else StringIO()
    stderr = _TtyStringIO() if stderr_tty else StringIO()
    monkeypatch.setattr("sys.stdout", stdout)
    monkeypatch.setattr("sys.stderr", stderr)
    if no_colour:
        monkeypatch.setenv("NO_COLOR", "")
    else:
        monkeypatch.delenv("NO_COLOR", raising=False)
    now = datetime.now(UTC)
    monkeypatch.setattr(
        cli,
        "_run_cli_compiled_plan",
        lambda **_kwargs: SmokeCheckResult(status="passed", started_at=now, finished_at=now, steps=()),
    )
    monkeypatch.setattr(cli, "_callback_listener_for", lambda *_args, **_kwargs: contextlib.nullcontext())
    monkeypatch.chdir(tmp_path)

    assert cli.run(["--test-plan", str(plan_path)]) == 0
    assert ("\033[32mPASSED\033[0m" in stdout.getvalue()) == (stdout_tty and not no_colour)
    assert ("\033[33m[BETA]\033[0m" in stderr.getvalue()) == (stderr_tty and not no_colour)
    result_text = (tmp_path / "out/test-results.json").read_text()
    assert "\\u001b" not in result_text
    assert json.loads(result_text)["status"] == "passed"
    assert "\033" not in (tmp_path / "out/execution-log.ndjson").read_text()
