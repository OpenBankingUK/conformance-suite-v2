"""Regression checks for cross-caller image publication and release serialization."""

from __future__ import annotations

import json
import os
import re
import subprocess
import textwrap
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


def test_trusted_assessment_is_separate_and_candidate_bound() -> None:
    assessment = (WORKFLOWS / "security-assessment.yml").read_text()
    ci = (WORKFLOWS / "ci.yml").read_text()
    automatic = (WORKFLOWS / "auto-promote.yml").read_text()
    promotion = (WORKFLOWS / "_promote-image.yml").read_text()

    assert 'workflows: ["CI"]' in assessment
    assert 'EXPECTED_CI_WORKFLOW_ID: "268848247"' in assessment
    assert "and .run_attempt == 1" in assessment
    assert "and .workflow_sha_under_test == $sha" in assessment
    assert "commits/$source_sha" in assessment
    assert '--arg head_sha "$check_sha"' in assessment
    assert "environment: security-exception" in assessment
    assert "actions/runs/$GITHUB_RUN_ID/approvals" in assessment
    assert 'allow-exceptions: "false"' in assessment
    assert "uses: ./.github/actions/vulnerability-scan" not in ci
    assert 'workflows: ["Trusted security assessment"]' in automatic
    assert "security_assessment_run_id:" in automatic
    assert "security_assessment_run_id:" in promotion
    assert "security-assessment-receipt" in promotion
    assert 'verify-dhi-provenance: "true"' in promotion


def test_build_keeps_max_mode_evidence_and_scanned_leaf_identity() -> None:
    ci = (WORKFLOWS / "ci.yml").read_text()

    assert "--provenance=mode=max,version=v0.2" in ci
    assert "--sbom=true" in ci
    assert "type=oci,oci-artifact=false,compression=gzip,force-compression=true" in ci
    assert "scripts.verify_build_evidence" in ci
    assert "--add-docker-load-manifest" in ci
    assert "'.image_digest'" in ci


def test_assessor_and_promoter_accept_legacy_archives_without_weakening_policy() -> None:
    assessment = (WORKFLOWS / "security-assessment.yml").read_text()
    promotion = (WORKFLOWS / "_promote-image.yml").read_text()

    assert "--allow-legacy" in assessment
    assert 'docker load --input "$RUNNER_TEMP/candidate/image-$SLUG.tar"' in assessment
    assert "--allow-legacy" in promotion
    assert 'docker load --input "$archive"' in promotion
    assert "skopeo copy --all --preserve-digests" in promotion
    assert "scripts.verify_build_evidence" in promotion
    assert "scripts/verify_loaded_image.py" in promotion
    assert 'allow-exceptions: "false"' in promotion
    assert 'fail-on-findings: "false"' in promotion


def test_published_version_is_passed_through_environment_not_shell_interpolation() -> None:
    promotion = (WORKFLOWS / "_promote-image.yml").read_text()
    publish = promotion.split("      - name: Assemble and publish the multi-arch manifest list\n", 1)[1].split(
        "\n      - name:", 1
    )[0]

    assert "RAW_VERSION: ${{ steps.validate.outputs.raw_version }}" in publish
    assert "AMD64_INDEX: ${{ steps.stage.outputs.amd64_index }}" in publish
    assert "ARM64_INDEX: ${{ steps.stage.outputs.arm64_index }}" in publish
    assert '"${IMAGE_NAME}:${RAW_VERSION}"' in publish
    assert '"${IMAGE_NAME}@${AMD64_INDEX}"' in publish
    assert '"${IMAGE_NAME}@${ARM64_INDEX}"' in publish
    assert "${{ steps.validate.outputs.raw_version }}" not in publish.split("        run: |\n", 1)[1]


def test_promotion_run_blocks_do_not_interpolate_dynamic_github_context() -> None:
    workflow = (WORKFLOWS / "_promote-image.yml").read_text()
    blocks = re.findall(
        r"^        run: \|\n((?:^          .*\n|^\n)+)",
        workflow,
        re.MULTILINE,
    )
    assert blocks
    for block in blocks:
        assert not re.search(r"\$\{\{\s*(?:steps\.[\w-]+\.outputs\.|inputs\.|github\.event\.|needs\.)", block)


def test_rescan_fetches_candidate_before_reading_its_dockerfile() -> None:
    promotion = (WORKFLOWS / "_promote-image.yml").read_text()
    rescan = promotion.split("\n  rescan:\n", 1)[1].split("\n  decision:", 1)[0]
    load = rescan.split("      - name: Verify and load the candidate archive\n", 1)[1]
    assert load.index('git fetch --no-tags origin "$INPUT_SOURCE_SHA"') < load.index(
        'git show "$INPUT_SOURCE_SHA:Dockerfile"'
    )


@pytest.mark.parametrize(
    ("filename", "step", "result_variable"),
    [
        ("security-assessment.yml", "Combine decisions and bind any approval to the candidate", "SCAN_RESULT"),
        ("_promote-image.yml", "Combine decisions", "RESCAN_RESULT"),
    ],
)
@pytest.mark.parametrize("missing_summary", [False, True])
def test_platform_decisions_show_findings_and_fail_closed_when_summary_missing(
    tmp_path: Path, filename: str, step: str, result_variable: str, missing_summary: bool
) -> None:
    workflow = (WORKFLOWS / filename).read_text()
    step_text = workflow.split(f"      - name: {step}\n", 1)[1].split("\n      - name:", 1)[0]
    step_text = step_text.split("\n  approve-risk:", 1)[0]
    script = textwrap.dedent(step_text.split("        run: |\n", 1)[1])
    decisions = tmp_path / "decisions"
    decisions.mkdir()
    for platform in ("linux-amd64", "linux-arm64"):
        (decisions / f"decision-{platform}.json").write_text(
            json.dumps({"platform": platform, "decision": "approval_required"})
        )
        if not (missing_summary and platform == "linux-arm64"):
            (decisions / f"brief-{platform}.md").write_text(f"{platform}: CVE-2026-117610 OpenVEX\n")
    output = tmp_path / "output"
    summary = tmp_path / "summary"
    env = {
        **os.environ,
        "RUNNER_TEMP": str(tmp_path),
        "GITHUB_OUTPUT": str(output),
        "GITHUB_STEP_SUMMARY": str(summary),
        result_variable: "success",
        "SOURCE_RUN_ID": "42",
        "SOURCE_SHA": "source-sha",
        "WORKFLOW_SHA": "source-sha",
        "CHECK_SHA": "check-sha",
        "INPUT_SOURCE_SHA": "source-sha",
    }

    completed = subprocess.run(  # noqa: S603  # Exercise the checked-in workflow step.
        ["/bin/bash", "-c", script], env=env, capture_output=True, text=True, check=False
    )

    expected = "blocked" if missing_summary else "approval_required"
    assert f"decision={expected}" in output.read_text()
    assert "linux-amd64: CVE-2026-117610 OpenVEX" in summary.read_text()
    if not missing_summary:
        assert "linux-arm64: CVE-2026-117610 OpenVEX" in summary.read_text()
        assert "APPROVE source-sha" in summary.read_text()
    assert completed.returncode == (1 if missing_summary and filename == "_promote-image.yml" else 0)


def test_check_retains_findings_after_approval(tmp_path: Path) -> None:
    workflow = (WORKFLOWS / "security-assessment.yml").read_text()
    assert workflow.index("Show findings on pending candidate check") < workflow.index("  approve-exception:")
    (tmp_path / "check-summary").mkdir()
    (tmp_path / "check-summary" / "assessment-summary.md").write_text("CVE-2026-117610 OpenVEX\n")
    (tmp_path / "assessment-summary.md").write_text("CVE-2026-117610 OpenVEX\n")
    gh = tmp_path / "gh"
    gh.write_text('#!/bin/bash\ncp "$6" "$CAPTURE"\n')
    gh.chmod(0o755)
    capture = tmp_path / "captured.json"
    env = {
        **os.environ,
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "RUNNER_TEMP": str(tmp_path),
        "CAPTURE": str(capture),
        "GITHUB_REPOSITORY": "example/repo",
        "CHECK_ID": "1",
        "DECISION": "approval_required",
        "DECIDE_RESULT": "success",
        "APPROVAL_RESULT": "success",
        "VERIFY_RESULT": "success",
        "SOURCE_SHA": "source-sha",
    }
    pending = workflow.split("      - name: Show findings on pending candidate check\n", 1)[1]
    pending = pending.split("\n      - name:", 1)[0]
    pending_script = textwrap.dedent(pending.split("        run: |\n", 1)[1])
    subprocess.run(  # noqa: S603  # Exercise the checked-in workflow step with a stub GitHub CLI.
        ["/bin/bash", "-c", pending_script], env=env, capture_output=True, text=True, check=True
    )
    pending_payload = json.loads(capture.read_text())
    assert pending_payload["status"] == "in_progress"
    assert "CVE-2026-117610 OpenVEX" in pending_payload["output"]["summary"]

    step = workflow.split("      - name: Complete the candidate check\n", 1)[1]
    script = textwrap.dedent(step.split("        run: |\n", 1)[1])
    subprocess.run(  # noqa: S603  # Exercise the checked-in workflow step with a stub GitHub CLI.
        ["/bin/bash", "-c", script], env=env, capture_output=True, text=True, check=True
    )
    payload = json.loads(capture.read_text())
    assert payload["conclusion"] == "success"
    assert "candidate-bound" in payload["output"]["summary"]
    assert "CVE-2026-117610 OpenVEX" in payload["output"]["summary"]
