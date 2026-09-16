"""CLI coverage for independent schema-version 2.0 certification validation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from conformance import certification_cli
from conformance.configuration_contracts.v2_loader import (
    dump_execution_manifest,
    dump_resolved_plan,
)
from tests.support.certification import (
    RELEASE_PATH,
    CertificationFixture,
    build_certification_fixture,
    report_with_statuses,
)
from tests.support.paths import REPO_ROOT

pytestmark = pytest.mark.unit


@pytest.fixture
def certification_fixture() -> CertificationFixture:
    return build_certification_fixture()


def test_cli_prints_summary_and_returns_zero_for_valid_report(
    capsys: pytest.CaptureFixture[str],
    certification_fixture: CertificationFixture,
    tmp_path: Path,
) -> None:
    report_path, resolved_path, manifest_path = _write_inputs(certification_fixture, tmp_path)

    exit_code = certification_cli.run(
        _arguments(
            report_path,
            resolved_path=resolved_path,
            manifest_path=manifest_path,
        )
    )

    assert exit_code == 0
    output = capsys.readouterr().out
    assert output.startswith("Certification report validation: PASS\n")
    assert "Automated assessment: passed" in output
    assert "Certification eligibility: eligible" in output


def test_cli_prints_summary_and_returns_one_for_failed_approved_test(
    capsys: pytest.CaptureFixture[str],
    certification_fixture: CertificationFixture,
    tmp_path: Path,
) -> None:
    step_id = str(certification_fixture.manifest.steps[0].id)
    report = report_with_statuses(certification_fixture, {step_id: "failed"})
    report_path, resolved_path, manifest_path = _write_inputs(
        certification_fixture,
        tmp_path,
        report=report,
    )

    exit_code = certification_cli.run(
        _arguments(
            report_path,
            resolved_path=resolved_path,
            manifest_path=manifest_path,
        )
    )

    assert exit_code == 1
    output = capsys.readouterr().out
    assert output.startswith("Certification report validation: FAIL\n")
    assert "Automated assessment: failed" in output
    assert "approved_test_failed" in output


def test_cli_returns_two_for_invalid_input(
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
    certification_fixture: CertificationFixture,
    tmp_path: Path,
) -> None:
    report_path, resolved_path, manifest_path = _write_inputs(certification_fixture, tmp_path)
    report_path.write_text(json.dumps({"tool": {"version": "0.1.0"}, "steps": []}), encoding="utf-8")

    with caplog.at_level("ERROR", logger="conformance.certification_cli"):
        exit_code = certification_cli.run(
            _arguments(
                report_path,
                resolved_path=resolved_path,
                manifest_path=manifest_path,
            )
        )

    assert exit_code == 2
    assert capsys.readouterr().out == ""
    assert "report.metadata is required" in caplog.text


def test_cli_writes_summary_output_instead_of_stdout(
    capsys: pytest.CaptureFixture[str],
    certification_fixture: CertificationFixture,
    tmp_path: Path,
) -> None:
    report_path, resolved_path, manifest_path = _write_inputs(certification_fixture, tmp_path)
    summary_path = tmp_path / "nested" / "summary.txt"

    exit_code = certification_cli.run(
        [
            *_arguments(
                report_path,
                resolved_path=resolved_path,
                manifest_path=manifest_path,
            ),
            "--summary-output",
            str(summary_path),
        ]
    )

    assert exit_code == 0
    assert capsys.readouterr().out == ""
    assert summary_path.read_text(encoding="utf-8").startswith("Certification report validation: PASS\n")


def test_cli_returns_three_when_summary_output_cannot_be_written(
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
    certification_fixture: CertificationFixture,
    tmp_path: Path,
) -> None:
    report_path, resolved_path, manifest_path = _write_inputs(certification_fixture, tmp_path)
    summary_path = tmp_path / "summary.txt"
    summary_path.mkdir()

    with caplog.at_level("ERROR", logger="conformance.certification_cli"):
        exit_code = certification_cli.run(
            [
                *_arguments(
                    report_path,
                    resolved_path=resolved_path,
                    manifest_path=manifest_path,
                ),
                "--summary-output",
                str(summary_path),
            ]
        )

    assert exit_code == 3
    assert capsys.readouterr().out == ""
    assert "Unable to write certification summary" in caplog.text


def test_cli_returns_two_for_argparse_errors() -> None:
    assert certification_cli.run([]) == 2


def _arguments(
    report_path: Path,
    *,
    resolved_path: Path,
    manifest_path: Path,
) -> list[str]:
    return [
        str(report_path),
        "--suite-release",
        str(RELEASE_PATH),
        "--resolved-plan",
        str(resolved_path),
        "--manifest",
        str(manifest_path),
        "--trusted-root",
        str(REPO_ROOT),
    ]


def _write_inputs(
    fixture: CertificationFixture,
    root: Path,
    *,
    report: object | None = None,
) -> tuple[Path, Path, Path]:
    report_path = root / "report.json"
    resolved_path = root / "resolved-plan.json"
    manifest_path = root / "execution-manifest.json"
    report_path.write_text(json.dumps(report or fixture.report), encoding="utf-8")
    resolved_path.write_text(dump_resolved_plan(fixture.resolved_plan), encoding="utf-8")
    manifest_path.write_text(dump_execution_manifest(fixture.manifest), encoding="utf-8")
    return report_path, resolved_path, manifest_path
