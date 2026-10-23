"""Regression checks for cross-caller image publication and release serialization."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

WORKFLOWS = Path(__file__).resolve().parents[3] / ".github/workflows"
PROMOTION_CALL = "uses: ./.github/workflows/_promote-image.yml"
FINALIZATION_CALL = "uses: ./.github/workflows/_finalize-release.yml"
CALLERS = ("auto-promote.yml", "promote-beta.yml", "promote-ga.yml", "promote-preview.yml")


def test_all_promotion_callers_are_covered() -> None:
    callers = {path.name for path in WORKFLOWS.glob("*.yml") if PROMOTION_CALL in path.read_text()}
    assert callers == set(CALLERS)


@pytest.mark.parametrize("filename", CALLERS)
def test_caller_holds_shared_non_cancelling_workflow_lock(filename: str) -> None:
    workflow = (WORKFLOWS / filename).read_text()
    header, jobs = workflow.split("\njobs:\n", 1)
    # A job-level lock cannot span the separate promote and finalize jobs.
    assert re.search(
        r"^concurrency:\n  group: image-promotion\n  cancel-in-progress: false\n",
        header,
        re.MULTILINE,
    )
    assert PROMOTION_CALL in jobs
    assert "group: image-promotion" not in jobs


@pytest.mark.parametrize("filename", ("_promote-image.yml", "_finalize-release.yml"))
def test_reusable_workflows_do_not_reacquire_the_caller_lock(filename: str) -> None:
    workflow = (WORKFLOWS / filename).read_text()
    assert not re.search(r"^\s*concurrency:", workflow, re.MULTILINE)


@pytest.mark.parametrize("filename", ("auto-promote.yml", "promote-beta.yml", "promote-ga.yml"))
def test_release_finalization_remains_after_successful_promotion(filename: str) -> None:
    workflow = (WORKFLOWS / filename).read_text()
    finalize = workflow.split("\n  finalize:\n", 1)[1]
    assert FINALIZATION_CALL in finalize
    assert re.search(r"^    needs: (?:promote|\[resolve, promote\])$", finalize, re.MULTILINE)
    assert "always()" not in finalize
    assert "    environment:" not in finalize


def test_preview_remains_publication_only_and_auto_resolution_keeps_its_own_group() -> None:
    assert FINALIZATION_CALL not in (WORKFLOWS / "promote-preview.yml").read_text()
    automatic = (WORKFLOWS / "auto-promote.yml").read_text()
    resolve = automatic.split("\n  resolve:\n", 1)[1].split("\n  promote:\n", 1)[0]
    assert "      group: auto-promote-resolve-${{ github.event.workflow_run.head_branch }}\n" in resolve
    assert "      cancel-in-progress: true\n" in resolve
    assert "    environment:" not in resolve
    assert "needs.resolve.outputs.should_promote == 'true'" in automatic
    assert "(needs.resolve.outputs.channel == 'ga' || needs.resolve.outputs.channel == 'beta')" in automatic


@pytest.mark.parametrize("base_case", ["assessed", "updated", "invalid"])
def test_pr_scan_uses_only_trusted_validator_and_withholds_unapproved_vex(tmp_path: Path, base_case: str) -> None:
    """Digest updates get raw scans, never authority to execute head code or approve VEX."""
    from scripts.validate_docker_base import EXPECTED_RUNTIME_BASE

    root = WORKFLOWS.parents[1]
    trusted_scripts = tmp_path / "scripts"
    trusted_scripts.mkdir()
    (trusted_scripts / "validate_docker_base.py").write_text((root / "scripts/validate_docker_base.py").read_text())
    scan_input = tmp_path / ".scan-input"
    scan_input.mkdir()
    runtime_base = EXPECTED_RUNTIME_BASE
    if base_case == "updated":
        runtime_base = EXPECTED_RUNTIME_BASE.split("@")[0] + "@sha256:" + "0" * 64
    dockerfile = f"FROM {runtime_base} AS runtime\n" if base_case != "invalid" else "not a Dockerfile\n"
    (scan_input / "Dockerfile").write_text(dockerfile)
    # A head-side validator must remain inert, even when the digest differs.
    (scan_input / "scripts").mkdir()
    (scan_input / "scripts/validate_docker_base.py").write_text("raise RuntimeError('head code executed')\n")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    uv = bin_dir / "uv"
    uv.write_text('#!/bin/sh\nshift 2\nexec "$TRUSTED_PYTHON" "$@"\n')
    uv.chmod(0o755)
    output = tmp_path / "output"
    workflow = (WORKFLOWS / "ci.yml").read_text()
    step = workflow.split("      - name: Resolve the DHI runtime base reference\n", 1)[1]
    run = step.split("        run: |\n", 1)[1].split("\n      - name:", 1)[0]
    script = "\n".join(line[10:] for line in run.splitlines())
    result = subprocess.run(  # noqa: S603 - Executes the repository-owned workflow with an isolated trusted-validator fixture.
        ["/bin/bash", "-eu", "-c", script],
        cwd=tmp_path,
        env={
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "TRUSTED_PYTHON": sys.executable,
            "IS_HARDENED": "true",
            "GITHUB_OUTPUT": str(output),
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "head code executed" not in result.stderr
    if base_case == "assessed":
        assert output.read_text() == f"ref={EXPECTED_RUNTIME_BASE}\n"
        assert "::warning" not in result.stdout
    else:
        assert output.read_text() == "ref=\n"
        assert "Scanning continues without OpenVEX exclusions." in result.stdout
        assert "error:" in result.stderr
    scan = workflow.split("      - name: Scan for vulnerabilities\n", 1)[1].split("\n  vulnerability-scan:", 1)[0]
    assert "vex-image: ${{ steps.runtime_base.outputs.ref }}" in scan
    assert "fail-on-findings:" not in scan
    assert "        if:" not in scan
