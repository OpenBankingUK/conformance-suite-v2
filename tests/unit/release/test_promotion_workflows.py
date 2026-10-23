"""Regression checks for cross-caller image publication and release serialization."""

from __future__ import annotations

import ast
import os
import re
import shlex
import subprocess
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.validate_docker_base import EXPECTED_RUNTIME_BASE

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


def test_published_rescan_authenticates_for_assessed_dhi_ancestry() -> None:
    workflow = (WORKFLOWS / "security-rescan.yml").read_text()
    published = workflow.split("\n  scan-image:\n", 1)[1].split("\n  issues:\n", 1)[0]
    assert 'vex-requires-dhi-login: "true"' in published
    assert "vex-image: docker.io/openbanking/conformance-suite-v2:${{ matrix.tag }}" in published
    assert "gate-directory: ." in published
    assert "exceptions-file: security/vulnerability-exceptions.toml" in published


@pytest.mark.parametrize("approved", [True, False])
def test_changed_digest_scan_uses_only_trusted_validator_evidence(tmp_path: Path, approved: bool) -> None:
    workflow = (WORKFLOWS / "ci.yml").read_text()
    step = workflow.split("      - name: Resolve the DHI runtime base reference\n", 1)[1]
    script = textwrap.dedent(step.split("        run: |\n", 1)[1].split("\n      - name:", 1)[0])
    source = tmp_path / ".scan-input"
    source.mkdir()
    base = EXPECTED_RUNTIME_BASE if approved else "dhi.io/python:3.14-debian13@sha256:" + "0" * 64
    (source / "Dockerfile").write_text(f"FROM {base} AS runtime\n")
    output = tmp_path / "output"
    script = script.replace("uv run python", shlex.quote(sys.executable))
    result = subprocess.run(  # noqa: S603 - Execute only the repository's fixed CI shell block with fixture data.
        ["/bin/bash", "-c", script],
        cwd=tmp_path,
        env={
            **os.environ,
            "PYTHONPATH": str(WORKFLOWS.parents[1]),
            "IS_HARDENED": "true",
            "GITHUB_OUTPUT": str(output),
        },
        capture_output=True,
        text=True,
        check=True,
    )
    assert output.read_text() == f"ref={EXPECTED_RUNTIME_BASE if approved else ''}\n"
    assert ("Unassessed DHI runtime base" in result.stdout) == (not approved)
    assert "vex-image: ${{ steps.runtime_base.outputs.ref }}" in workflow


def test_hardened_smoke_callers_remain_separate_from_plain_image_checks() -> None:
    workflow = (WORKFLOWS / "ci.yml").read_text()
    calls = re.findall(
        r"      - name: Run hardened smoke test\n(.*?)(?=\n      - name:)",
        workflow,
        re.DOTALL,
    )
    assert len(calls) == 2
    for call in calls:
        assert "if: needs.plan.outputs.is_hardened == 'true'" in call
        assert "./scripts/docker_smoke_test.sh" in call


@pytest.mark.parametrize("distro", ["alpine", "debian", "unknown"])
@pytest.mark.parametrize("valid_inventory", [True, False])
def test_shared_smoke_distribution_checks(tmp_path: Path, distro: str, valid_inventory: bool) -> None:
    smoke = (WORKFLOWS.parents[1] / "scripts/docker_smoke_test.sh").read_text()
    code = smoke.split('--entrypoint python3 "$IMAGE_REF" -c "\n', 1)[1].split('\n" || fail ', 1)[0]
    tree = ast.parse(code)

    class FixtureImports(ast.NodeTransformer):
        def visit_Import(self, node: ast.Import) -> ast.Pass:
            return ast.copy_location(ast.Pass(), node)

        def visit_ImportFrom(self, node: ast.ImportFrom) -> ast.Pass:
            return ast.copy_location(ast.Pass(), node)

    tree = FixtureImports().visit(tree)
    status = tmp_path / "var/lib/dpkg/status"
    status.parent.mkdir(parents=True)
    status.write_text(
        "Package: conformance-python\nVersion: 3.14.9\n\nPackage: zlib1g\nVersion: 1:1.3.dfsg+really1.3.1-1+dhi4\n"
        if valid_inventory
        else "Package: python-3.14\nVersion: 3.14.8\n"
    )
    namespace = {
        "platform": SimpleNamespace(freedesktop_os_release=lambda: {"ID": distro}),
        "Path": lambda path: tmp_path / path.removeprefix("/"),
        "sys": SimpleNamespace(version_info=(3, 14, 9), version="3.14.9", base_prefix="/opt/python"),
        "pyexpat": SimpleNamespace(version_info=(2, 9, 0), EXPAT_VERSION="expat_2.9.0"),
        "zlib": SimpleNamespace(ZLIB_RUNTIME_VERSION="1.3.1"),
        "certifi": SimpleNamespace(__version__="2026.10.19"),
    }
    compiled = compile(ast.fix_missing_locations(tree), "<smoke-runtime-check>", "exec")
    if distro == "unknown":
        with pytest.raises(RuntimeError, match="Unsupported runtime distribution"):
            exec(compiled, namespace)  # noqa: S102 - Fixed repository smoke code, isolated fixture dependencies.
    elif distro == "debian" and not valid_inventory:
        with pytest.raises(AssertionError):
            exec(compiled, namespace)  # noqa: S102 - Fixed repository smoke code, isolated fixture dependencies.
    else:
        exec(compiled, namespace)  # noqa: S102 - Fixed repository smoke code, isolated fixture dependencies.
