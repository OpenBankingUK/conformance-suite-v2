"""Regression checks for cross-caller image publication and release serialization."""

from __future__ import annotations

import re
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
