"""Regression checks for CI-driven publication and release serialization."""

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
    assert not re.search(r"^\s*concurrency:", (WORKFLOWS / filename).read_text(), re.MULTILINE)


@pytest.mark.parametrize("filename", ("auto-promote.yml", "promote-beta.yml", "promote-ga.yml"))
def test_release_finalization_remains_after_successful_promotion(filename: str) -> None:
    finalize = (WORKFLOWS / filename).read_text().split("\n  finalize:\n", 1)[1]
    assert FINALIZATION_CALL in finalize
    assert re.search(r"^    needs: (?:promote|\[resolve, promote\])$", finalize, re.MULTILINE)
    assert "always()" not in finalize
    assert "    environment:" not in finalize


def test_automatic_promotion_uses_only_successful_eligible_push_ci() -> None:
    automatic = (WORKFLOWS / "auto-promote.yml").read_text()
    assert 'workflows: ["CI"]' in automatic
    resolve = automatic.split("\n  resolve:\n", 1)[1].split("\n  promote:\n", 1)[0]
    assert "github.event.workflow_run.conclusion == 'success'" in resolve
    assert "github.event.workflow_run.event == 'push'" in resolve
    assert "github.event.workflow_run.head_repository.full_name == github.repository" in resolve
    assert "head_branch == 'main'" in resolve
    assert "            main|preview/*) ;;\n" in resolve
    assert "startsWith(github.event.workflow_run.head_branch, 'release/')" not in resolve
    assert "startsWith(github.event.workflow_run.head_branch, 'preview/')" in resolve
    assert "CI_RUN_ID: ${{ github.event.workflow_run.id }}" in resolve
    assert "      group: auto-promote-resolve-${{ github.event.workflow_run.head_branch }}\n" in resolve
    assert "      cancel-in-progress: true\n" in resolve
    assert "    environment:" not in resolve
    assert "needs.resolve.outputs.should_promote == 'true'" in automatic
    assert "ci_run_id: ${{ needs.resolve.outputs.ci_run_id }}" in automatic
    assert "needs.resolve.outputs.channel == 'beta'" in automatic
    assert "needs.resolve.outputs.channel == 'ga'" not in automatic
    assert FINALIZATION_CALL not in (WORKFLOWS / "promote-preview.yml").read_text()
    policy = resolve.split("      - name: Resolve promotion policy\n", 1)[1]
    assert "if: steps.artifact.outputs.available == 'true'" in policy


def test_manual_beta_recovery_uses_main_without_a_branch_input() -> None:
    workflow = (WORKFLOWS / "promote-beta.yml").read_text()
    header, jobs = workflow.split("\njobs:\n", 1)
    assert "      branch:" not in header
    assert "      branch: main\n" in jobs
    assert "      channel: beta\n" in jobs
    assert "      environment_name: beta-release\n" in jobs


def test_promotion_preserves_credential_boundary_without_scanner_dependencies() -> None:
    for path in WORKFLOWS.glob("*.yml"):
        workflow = path.read_text()
        for retired in (
            "security-assessment",
            "security_assessment_run_id",
            "security-exception",
            "vulnerability-scan",
            "SNYK_TOKEN",
            "/approvals",
            "candidate-context",
        ):
            assert retired not in workflow, (path.name, retired)
    promotion = (WORKFLOWS / "_promote-image.yml").read_text()
    publish = promotion.split("\n  promote:\n", 1)[1]
    assert "    needs: locate\n" in publish
    assert "    environment: ${{ inputs.environment_name }}\n" in publish
    assert "always()" not in publish
    assert "git diff --quiet" in publish
    assert "No merged pull request found" in publish
    assert "skopeo copy --all --preserve-digests" in publish
    assert "--allow-legacy" in publish
    assert "scripts.verify_build_evidence" in publish
    assert '--expected-digest "$expected" --require-revision-label' in publish
    assert "--runtime-base" not in publish
    assert "scripts.validate_docker_base --candidate-dhi --stdin" in publish
    assert "password: ${{ secrets.DOCKER_ORG_ACCESS_TOKEN }}" in publish


def test_build_keeps_quality_checks_and_platform_evidence() -> None:
    ci = (WORKFLOWS / "ci.yml").read_text()
    assert "make check SECRET_SCAN_EXCLUDE_PATHSPEC=" in ci
    assert "--provenance=mode=max \\\n" in ci
    assert "--sbom=true" in ci
    assert "type=oci,oci-artifact=false,compression=gzip,force-compression=true" in ci
    assert "scripts.verify_build_evidence" in ci
    assert "--runtime-base" not in ci
    assert "--require-revision-label" in ci
    assert "--add-docker-load-manifest" in ci
    assert "'.image_digest'" in ci
    upload = ci.split("      - name: Upload candidate artifacts\n", 1)[1].split("\n  candidate-manifest:", 1)[0]
    assert "if: needs.plan.outputs.is_candidate == 'true'" in upload
    assert "artifacts/image-${{ matrix.slug }}.tar" in upload
    assert "artifacts/sbom-${{ matrix.slug }}.spdx.json" in upload


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
    for filename in (*CALLERS, "_promote-image.yml"):
        blocks = re.findall(
            r"^        run: \|\n((?:^          .*\n|^\n)+)",
            (WORKFLOWS / filename).read_text(),
            re.MULTILINE,
        )
        for block in blocks:
            assert not re.search(r"\$\{\{\s*(?:steps\.[\w-]+\.outputs\.|inputs\.|github\.event\.|needs\.)", block)
