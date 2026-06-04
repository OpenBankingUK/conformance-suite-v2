import json
import sys
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path
from typing import cast

import httpx
import pytest

from conformance import cli
from conformance.api.auth_session_store import auth_session_store
from conformance.context import RuntimeConfig
from conformance.execution_log import ExecutionLogger
from conformance.results import SmokeCheckResult

REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_CONFIG_PATH = REPO_ROOT / "config" / "model-bank-example.json"
EXAMPLE_MANIFEST_PATH = REPO_ROOT / "config" / "manifest-v0-openid-jwks-example.json"


class _TtyStringIO(StringIO):
    """String buffer that reports TTY status for CLI tests."""

    def isatty(self) -> bool:
        """Return True so tests can exercise interactive CLI output.

        Returns:
            Always True.
        """
        return True


def _write_suite_config(tmp_path: Path) -> Path:
    """Write a config that selects the bundled discovery/JWKS suite.

    Args:
        tmp_path: Temporary directory used for config and output paths.

    Returns:
        Path to the written config file.
    """
    config_path = tmp_path / "suite-config.json"
    config_path.write_text(
        json.dumps(
            {
                "environment": "suite-env",
                "discoveryUrl": "https://suite.example.com/.well-known/openid-configuration",
                "resultOutputPath": str(tmp_path / "result.json"),
                "executionLogPath": str(tmp_path / "log.ndjson"),
                "testSuite": {
                    "standard": "ob-read-write",
                    "specVersion": "v4.0",
                    "profile": "fapi1-advanced",
                    "suite": "discovery-jwks",
                },
            }
        ),
        encoding="utf-8",
    )
    return config_path


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
    result_path = tmp_path / "out" / "test-results.json"
    assert result_path.parent.is_dir()
    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert result["status"] == "passed"
    assert result["summary"] == {"total": 1, "passed": 1, "failed": 0, "warn": 0, "skipped": 0}


@pytest.mark.unit
def test_cli_runs_manifest_from_committed_example_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    requested_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_urls.append(str(request.url))
        if str(request.url) == "https://auth1.obie.uk.ozoneapi.io/.well-known/openid-configuration":
            return httpx.Response(
                200,
                json={
                    "issuer": "https://auth1.obie.uk.ozoneapi.io",
                    "jwks_uri": "https://keystore.openbankingtest.org.uk/example.jwks",
                },
            )
        return httpx.Response(200, json={"keys": []})

    original_client = httpx.Client

    def mock_client(*, timeout: float, verify: bool | str, cert: tuple[str, str] | None) -> httpx.Client:
        return original_client(transport=httpx.MockTransport(handler))

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(httpx, "Client", mock_client)

    exit_code = cli.run([str(EXAMPLE_CONFIG_PATH), "--manifest", str(EXAMPLE_MANIFEST_PATH)])

    assert exit_code == 0
    assert requested_urls == [
        "https://auth1.obie.uk.ozoneapi.io/.well-known/openid-configuration",
        "https://keystore.openbankingtest.org.uk/example.jwks",
    ]
    result = json.loads((tmp_path / "out" / "test-results.json").read_text(encoding="utf-8"))
    assert result["status"] == "passed"
    assert result["summary"] == {"total": 2, "passed": 2, "failed": 0, "warn": 0, "skipped": 0}


@pytest.mark.unit
def test_cli_resolves_config_selected_suite_when_manifest_is_omitted(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A config ``testSuite`` supplies the manifest when ``--manifest`` is omitted.

    Args:
        monkeypatch: Pytest fixture used to replace outbound HTTP with a mock
            transport and run the CLI under ``tmp_path``.
        tmp_path: Temporary directory used for config and output files.
    """
    config_path = _write_suite_config(tmp_path)
    requested_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        """Return discovery/JWKS responses for the bundled suite.

        Args:
            request: HTTP request issued by the CLI runner.

        Returns:
            Mock HTTP response for the requested URL.
        """
        requested_urls.append(str(request.url))
        if str(request.url) == "https://suite.example.com/.well-known/openid-configuration":
            return httpx.Response(
                200,
                json={"issuer": "https://suite.example.com", "jwks_uri": "https://suite.example.com/jwks"},
            )
        return httpx.Response(200, json={"keys": []})

    original_client = httpx.Client

    def mock_client(*, timeout: float, verify: bool | str, cert: tuple[str, str] | None) -> httpx.Client:
        """Build an HTTPX client backed by the mock suite transport.

        Args:
            timeout: Ignored timeout passed by production client construction.
            verify: Ignored TLS verification setting.
            cert: Ignored client certificate tuple.

        Returns:
            HTTPX client using ``handler`` as its transport.
        """
        return original_client(transport=httpx.MockTransport(handler))

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(httpx, "Client", mock_client)

    exit_code = cli.run([str(config_path)])

    assert exit_code == 0
    assert requested_urls == [
        "https://suite.example.com/.well-known/openid-configuration",
        "https://suite.example.com/jwks",
    ]
    result = json.loads((tmp_path / "result.json").read_text(encoding="utf-8"))
    assert result["status"] == "passed"
    assert result["plan"] == {
        "totalSteps": 2,
        "selectedSteps": 2,
        "deselectedSteps": 0,
        "mandatorySelected": 2,
        "mandatoryDeselected": 0,
    }


@pytest.mark.unit
def test_cli_explicit_manifest_overrides_config_selected_suite(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """``--manifest`` remains the CLI override even when config has ``testSuite``.

    Args:
        monkeypatch: Pytest fixture used to replace outbound HTTP with a mock
            transport and run the CLI under ``tmp_path``.
        tmp_path: Temporary directory used for config, manifest, and outputs.
    """
    config_path = _write_suite_config(tmp_path)
    manifest_path = tmp_path / "explicit-manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schemaVersion": "v1",
                "name": "explicit override",
                "steps": [
                    {
                        "id": "explicit",
                        "name": "Explicit step",
                        "request": {"method": "GET", "url": "https://explicit.example.com/status"},
                        "assertions": [{"type": "http_status", "expected": 200}],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    requested_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        """Record explicit manifest requests and return a passing response.

        Args:
            request: HTTP request issued by the CLI runner.

        Returns:
            Passing mock HTTP response for the explicit manifest step.
        """
        requested_urls.append(str(request.url))
        return httpx.Response(200, json={})

    original_client = httpx.Client

    def mock_client(*, timeout: float, verify: bool | str, cert: tuple[str, str] | None) -> httpx.Client:
        """Build an HTTPX client that records explicit manifest requests.

        Args:
            timeout: Ignored timeout passed by production client construction.
            verify: Ignored TLS verification setting.
            cert: Ignored client certificate tuple.

        Returns:
            HTTPX client backed by a mock transport.
        """
        return original_client(transport=httpx.MockTransport(handler))

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(httpx, "Client", mock_client)

    exit_code = cli.run([str(config_path), "--manifest", str(manifest_path)])

    assert exit_code == 0
    assert requested_urls == ["https://explicit.example.com/status"]
    result = json.loads((tmp_path / "result.json").read_text(encoding="utf-8"))
    assert [step["name"] for step in result["steps"]] == ["explicit"]


@pytest.mark.unit
def test_cli_prints_psu_authorization_url_to_tty_stderr(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "model-bank.json"
    manifest_path = tmp_path / "manifest.json"
    config_path.write_text(
        json.dumps(
            {
                "environment": "test-env",
                "discoveryUrl": "https://example.com/.well-known/openid-configuration",
                "resultOutputPath": str(tmp_path / "result.json"),
                "executionLogPath": str(tmp_path / "execution.ndjson"),
            }
        ),
        encoding="utf-8",
    )
    manifest_path.write_text(
        json.dumps(
            {
                "schemaVersion": "v1",
                "name": "cli psu url surfacing",
                "steps": [
                    {
                        "id": "placeholder",
                        "name": "Placeholder HTTP step",
                        "request": {"method": "GET", "url": "https://example.com/unused"},
                        "assertions": [{"type": "http_status", "expected": 200}],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    url = "https://auth.example.com/authorize?client_id=client-123&state=s"

    def fake_run_manifest(*_args: object, **kwargs: object) -> SmokeCheckResult:
        """Emit a PSU URL event and return a passing manifest result.

        Args:
            *_args: Positional arguments accepted by ``cli.run_manifest``.
            **kwargs: Keyword arguments accepted by ``cli.run_manifest``.

        Returns:
            Passing smoke-check result for the CLI to serialise.
        """
        execution_logger = cast(ExecutionLogger, kwargs["execution_logger"])
        runtime_config = cast(RuntimeConfig, kwargs["runtime_config"])
        assert kwargs["auth_session_store"] is auth_session_store
        assert runtime_config.discovery_url == "https://example.com/.well-known/openid-configuration"
        assert runtime_config.environment == "test-env"
        execution_logger.emit("psu-authorization-url", step_id="psu", payload={"url": url})
        now = datetime.now(UTC)
        return SmokeCheckResult(
            environment="test-env",
            status="passed",
            started_at=now,
            finished_at=now,
            steps=(),
        )

    stdout = _TtyStringIO()
    stderr = _TtyStringIO()
    monkeypatch.setattr(cli, "run_manifest", fake_run_manifest)
    monkeypatch.setattr(sys, "stdout", stdout)
    monkeypatch.setattr(sys, "stderr", stderr)

    exit_code = cli.run([str(config_path), "--manifest", str(manifest_path)])

    assert exit_code == 0
    assert stderr.getvalue() == f"\033[1m[PSU]\033[0m Open this URL to authorise: {url}\n"


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
    config_path.write_text("{}", encoding="utf-8")

    exit_code = cli.run([str(config_path)])

    assert exit_code == 2


@pytest.mark.unit
def test_cli_returns_manifest_error_for_invalid_manifest(tmp_path: Path) -> None:
    config_path = tmp_path / "model-bank.json"
    manifest_path = tmp_path / "manifest.json"
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
    manifest_path.write_text("{}", encoding="utf-8")

    exit_code = cli.run([str(config_path), "--manifest", str(manifest_path)])

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


# ─── --deselect flag ─────────────────────────────────────────────────────────


def _write_plan_config_and_manifest(tmp_path: Path) -> tuple[Path, Path]:
    """Write a config and v1 manifest with one mandatory step and one optional step.

    Args:
        tmp_path: Pytest-managed temp directory used for both files and the
            engine's output paths.

    Returns:
        ``(config_path, manifest_path)`` for ``cli.run``.
    """
    config_path = tmp_path / "config.json"
    manifest_path = tmp_path / "manifest.json"
    config_path.write_text(
        json.dumps(
            {
                "environment": "test",
                "discoveryUrl": "https://example.com/.well-known/openid-configuration",
                "resultOutputPath": str(tmp_path / "result.json"),
                "executionLogPath": str(tmp_path / "log.ndjson"),
            }
        ),
        encoding="utf-8",
    )
    manifest_path.write_text(
        json.dumps(
            {
                "schemaVersion": "v1",
                "name": "plan-cli",
                "steps": [
                    {
                        "id": "mandatory-step",
                        "name": "Mandatory step",
                        "mandatory": True,
                        "request": {"method": "GET", "url": "https://example.com/a"},
                        "assertions": [{"type": "http_status", "expected": 200}],
                    },
                    {
                        "id": "optional-step",
                        "name": "Optional step",
                        "request": {"method": "GET", "url": "https://example.com/b"},
                        "assertions": [{"type": "http_status", "expected": 200}],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    return config_path, manifest_path


@pytest.mark.unit
def test_cli_deselect_repeated_excludes_each_step_from_result(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """``--deselect`` can be repeated; each id is excluded from the result file."""
    config_path, manifest_path = _write_plan_config_and_manifest(tmp_path)
    original_client = httpx.Client

    def mock_client(*, timeout: float, verify: bool | str, cert: tuple[str, str] | None) -> httpx.Client:
        return original_client(transport=httpx.MockTransport(lambda _r: httpx.Response(200, json={})))

    monkeypatch.setattr(httpx, "Client", mock_client)
    monkeypatch.chdir(tmp_path)

    exit_code = cli.run([str(config_path), "--manifest", str(manifest_path), "--deselect", "optional-step"])

    # Only an optional step is deselected and the mandatory step passes,
    # so the run as a whole passes → exit code 0.
    assert exit_code == 0
    result = json.loads((tmp_path / "result.json").read_text(encoding="utf-8"))
    assert result["status"] == "passed"
    assert [step["name"] for step in result["steps"]] == ["mandatory-step"]
    assert result["plan"] == {
        "totalSteps": 2,
        "selectedSteps": 1,
        "deselectedSteps": 1,
        "mandatorySelected": 1,
        "mandatoryDeselected": 0,
    }


@pytest.mark.unit
def test_cli_deselect_applies_to_config_resolved_suite(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """``--deselect`` is valid when config ``testSuite`` supplies the manifest.

    Args:
        monkeypatch: Pytest fixture used to replace outbound HTTP with a mock
            transport and run the CLI under ``tmp_path``.
        tmp_path: Temporary directory used for config and outputs.
    """
    config_path = _write_suite_config(tmp_path)
    original_client = httpx.Client

    def mock_client(*, timeout: float, verify: bool | str, cert: tuple[str, str] | None) -> httpx.Client:
        """Build an HTTPX client that only needs to serve the selected step.

        Args:
            timeout: Ignored timeout passed by production client construction.
            verify: Ignored TLS verification setting.
            cert: Ignored client certificate tuple.

        Returns:
            HTTPX client backed by a mock transport.
        """
        return original_client(
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(
                    200,
                    json={"issuer": "https://suite.example.com", "jwks_uri": "https://suite.example.com/jwks"},
                )
            )
        )

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(httpx, "Client", mock_client)

    exit_code = cli.run([str(config_path), "--deselect", "jwks-fetch"])

    assert exit_code == 0
    result = json.loads((tmp_path / "result.json").read_text(encoding="utf-8"))
    assert [step["name"] for step in result["steps"]] == ["openid-discovery"]
    assert result["plan"] == {
        "totalSteps": 2,
        "selectedSteps": 1,
        "deselectedSteps": 1,
        "mandatorySelected": 1,
        "mandatoryDeselected": 1,
    }


@pytest.mark.unit
def test_cli_deselect_unknown_id_returns_exit_code_2(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """An unknown ``--deselect`` id exits with code 2 (invalid input)."""
    config_path, manifest_path = _write_plan_config_and_manifest(tmp_path)
    monkeypatch.chdir(tmp_path)

    exit_code = cli.run([str(config_path), "--manifest", str(manifest_path), "--deselect", "ghost-step"])
    assert exit_code == 2


@pytest.mark.unit
def test_cli_deselect_without_manifest_returns_exit_code_2(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """``--deselect`` without ``--manifest`` is rejected with exit code 2."""
    config_path, _ = _write_plan_config_and_manifest(tmp_path)
    monkeypatch.chdir(tmp_path)

    exit_code = cli.run([str(config_path), "--deselect", "any"])
    assert exit_code == 2
