import json
from pathlib import Path

import pytest

from conformance import certification_cli
from conformance.certification_validator import APPROVED_RELEASE_POLICY_SCHEMA_VERSION
from conformance.json_types import JsonObject, JsonValue
from conformance.results import CheckStatus


@pytest.mark.unit
def test_certification_cli_prints_summary_and_returns_zero_for_valid_report(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    report_path, manifest_path, policy_path = _write_inputs(
        tmp_path,
        tool_version="1.2.3",
        policy_versions=("1.2.3",),
        steps=(("discovery", "passed"), ("jwks", "warn")),
    )

    exit_code = certification_cli.run(
        [str(report_path), "--manifest", str(manifest_path), "--approved-releases", str(policy_path)]
    )

    assert exit_code == 0
    output = capsys.readouterr().out
    assert output.startswith("Certification report validation: PASS\n")
    assert "Tool version: 1.2.3 (approved)" in output
    assert "Mandatory steps: 2 total, 1 passed, 1 warn" in output


@pytest.mark.unit
def test_certification_cli_prints_summary_and_returns_one_for_validation_failure(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    report_path, manifest_path, policy_path = _write_inputs(
        tmp_path,
        tool_version="1.2.3",
        policy_versions=("2.0.0",),
        steps=(("discovery", "failed"),),
    )

    exit_code = certification_cli.run(
        [str(report_path), "--manifest", str(manifest_path), "--approved-releases", str(policy_path)]
    )

    assert exit_code == 1
    output = capsys.readouterr().out
    assert output.startswith("Certification report validation: FAIL\n")
    assert "Tool version is not in the approved-release policy: 1.2.3" in output
    assert "Mandatory step failed in the submitted report: discovery" in output


@pytest.mark.unit
def test_certification_cli_returns_one_for_partial_coverage_manifest(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    report_path, manifest_path, policy_path = _write_inputs(
        tmp_path,
        tool_version="1.2.3",
        policy_versions=("1.2.3",),
        steps=(("discovery", "passed"),),
        certification_coverage="partial",
    )

    exit_code = certification_cli.run(
        [str(report_path), "--manifest", str(manifest_path), "--approved-releases", str(policy_path)]
    )

    assert exit_code == 1
    output = capsys.readouterr().out
    assert output.startswith("Certification report validation: FAIL\n")
    assert "Certification coverage: partial" in output
    assert "Manifest is not marked as complete certification coverage" in output


@pytest.mark.unit
def test_certification_cli_returns_two_for_invalid_input(
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    report_path, manifest_path, policy_path = _write_inputs(
        tmp_path,
        tool_version="1.2.3",
        policy_versions=("1.2.3",),
        steps=(("discovery", "passed"),),
    )
    report_path.write_text(json.dumps({"tool": {"version": "1.2.3"}, "steps": []}), encoding="utf-8")

    with caplog.at_level("ERROR", logger="conformance.certification_cli"):
        exit_code = certification_cli.run(
            [str(report_path), "--manifest", str(manifest_path), "--approved-releases", str(policy_path)]
        )

    assert exit_code == 2
    assert capsys.readouterr().out == ""
    assert "report.metadata is required" in caplog.text


@pytest.mark.unit
def test_certification_cli_writes_summary_output_instead_of_stdout(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    report_path, manifest_path, policy_path = _write_inputs(
        tmp_path,
        tool_version="1.2.3",
        policy_versions=("1.2.3",),
        steps=(("discovery", "passed"),),
    )
    summary_path = tmp_path / "nested" / "summary.txt"

    exit_code = certification_cli.run(
        [
            str(report_path),
            "--manifest",
            str(manifest_path),
            "--approved-releases",
            str(policy_path),
            "--summary-output",
            str(summary_path),
        ]
    )

    assert exit_code == 0
    assert capsys.readouterr().out == ""
    assert summary_path.read_text(encoding="utf-8").startswith("Certification report validation: PASS\n")


@pytest.mark.unit
def test_certification_cli_returns_three_when_summary_output_cannot_be_written(
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    report_path, manifest_path, policy_path = _write_inputs(
        tmp_path,
        tool_version="1.2.3",
        policy_versions=("1.2.3",),
        steps=(("discovery", "passed"),),
    )
    summary_path = tmp_path / "summary.txt"
    summary_path.mkdir()

    with caplog.at_level("ERROR", logger="conformance.certification_cli"):
        exit_code = certification_cli.run(
            [
                str(report_path),
                "--manifest",
                str(manifest_path),
                "--approved-releases",
                str(policy_path),
                "--summary-output",
                str(summary_path),
            ]
        )

    assert exit_code == 3
    assert capsys.readouterr().out == ""
    assert "Unable to write certification summary" in caplog.text


@pytest.mark.unit
def test_certification_cli_returns_two_for_argparse_errors() -> None:
    assert certification_cli.run([]) == 2


def _write_inputs(
    tmp_path: Path,
    *,
    tool_version: str,
    policy_versions: tuple[str, ...],
    steps: tuple[tuple[str, CheckStatus], ...],
    certification_coverage: str = "complete",
) -> tuple[Path, Path, Path]:
    report_path = tmp_path / "report.json"
    manifest_path = tmp_path / "manifest.json"
    policy_path = tmp_path / "policy.json"
    _write_json(report_path, _report_json(tool_version=tool_version, steps=steps))
    _write_json(
        manifest_path,
        _manifest_json(
            mandatory_step_ids=tuple(step_id for step_id, _status in steps),
            certification_coverage=certification_coverage,
        ),
    )
    _write_json(
        policy_path,
        {
            "schemaVersion": APPROVED_RELEASE_POLICY_SCHEMA_VERSION,
            "approvedToolVersions": list(policy_versions),
        },
    )
    return report_path, manifest_path, policy_path


def _manifest_json(*, mandatory_step_ids: tuple[str, ...], certification_coverage: str = "complete") -> JsonObject:
    steps: list[JsonValue] = []
    for step_id in mandatory_step_ids:
        steps.append(
            {
                "id": step_id,
                "name": step_id,
                "mandatory": True,
                "request": {"method": "GET", "url": f"https://example.com/{step_id}"},
                "assertions": [{"type": "http_status", "expected": 200}],
            }
        )
    return {
        "schemaVersion": "v1",
        "name": "validator-cli",
        "certificationCoverage": certification_coverage,
        "steps": steps,
    }


def _report_json(*, tool_version: str, steps: tuple[tuple[str, CheckStatus], ...]) -> JsonObject:
    rendered_steps: list[JsonValue] = []
    for step_id, status in steps:
        rendered_steps.append({"name": step_id, "status": status, "message": "ok"})
    return {
        "metadata": {"reportVersion": "1.0"},
        "tool": {"version": tool_version},
        "steps": rendered_steps,
    }


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")
