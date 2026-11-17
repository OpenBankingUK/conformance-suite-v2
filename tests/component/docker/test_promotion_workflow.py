"""Execute CI-to-promotion handoffs and image import at offline boundaries."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

pytestmark = pytest.mark.component

ROOT = Path(__file__).resolve().parents[3]
REPOSITORY = "OpenBankingUK/conformance-suite-v2"
SOURCE_SHA = "1" * 40
AUTOMATIC = "auto-promote.yml"
PROMOTION = "_promote-image.yml"
IDENTITY = "Validate the triggering CI run"
LOCATE = "Locate and verify the successful candidate-image run"

BOUNDARIES = r"""
gh() {
  if [[ "$1 $2" == "run list" ]]; then
    cat "$TEST_DATA/runs.json"
    return
  fi
  [[ "$1" == api && "${TEST_API_FAIL:-}" != true ]] || return 90
  shift
  local endpoint="" filter="" arg
  while (( $# )); do
    arg="$1"; shift
    case "$arg" in
      --paginate) ;;
      --jq) filter="$1"; shift ;;
      repos/*) endpoint="$arg" ;;
      *) return 91 ;;
    esac
  done
  local response=""
  case "$endpoint" in
    "repos/$GITHUB_REPOSITORY/actions/runs/123")
      response="$TEST_DATA/run.json" ;;
    "repos/$GITHUB_REPOSITORY/actions/runs/123/artifacts?per_page=100")
      response="$TEST_DATA/artifacts.json" ;;
    *) echo "Unexpected API request: $endpoint" >&2; return 92 ;;
  esac
  if [[ -n "$filter" ]]; then jq -r "$filter" "$response"; else cat "$response"; fi
}
"""


def _shell(workflow: str, name: str, occurrence: int = 0) -> str:
    content = (ROOT / ".github/workflows" / workflow).read_text()
    step = content.split(f"      - name: {name}\n")[occurrence + 1].split("      - name:", 1)[0]
    block = step.split("        run: |\n", 1)[1]
    return textwrap.dedent("\n".join(line for line in block.splitlines() if line.startswith("          ") or not line))


def _run_script(script: str, env: dict[str, str], cwd: Path = ROOT) -> subprocess.CompletedProcess[str]:
    bash = shutil.which("bash")
    assert bash is not None, "Workflow shell tests require bash."
    return subprocess.run(  # noqa: S603 - repository-owned shell with external boundaries replaced offline
        [bash, "--noprofile", "--norc", "-c", script],
        env=env,
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )


def _outputs(env: dict[str, str]) -> dict[str, str]:
    path = Path(env["GITHUB_OUTPUT"])
    return dict(line.split("=", 1) for line in path.read_text().splitlines()) if path.exists() else {}


@pytest.fixture
def handoff(tmp_path: Path) -> dict[str, str]:
    boundary = tmp_path / "boundary.sh"
    boundary.write_text(BOUNDARIES)
    run = {
        "workflow_id": 268848247,
        "name": "CI",
        "path": ".github/workflows/ci.yml",
        "head_sha": SOURCE_SHA,
        "head_branch": "release/2.0.0",
        "event": "push",
        "conclusion": "success",
        "run_attempt": 1,
        "head_repository": {"full_name": REPOSITORY},
    }
    (tmp_path / "run.json").write_text(json.dumps(run))
    (tmp_path / "runs.json").write_text(
        json.dumps(
            [
                {
                    "databaseId": 123,
                    "headSha": SOURCE_SHA,
                    "headBranch": "release/2.0.0",
                    "event": "push",
                    "conclusion": "success",
                }
            ]
        )
    )
    return {
        **os.environ,
        "BASH_ENV": str(boundary),
        "TEST_DATA": str(tmp_path),
        "GITHUB_REPOSITORY": REPOSITORY,
        "GITHUB_OUTPUT": str(tmp_path / "output"),
        "CI_RUN_ID": "123",
        "SOURCE_SHA": SOURCE_SHA,
        "SOURCE_BRANCH": "release/2.0.0",
        "INPUT_CI_RUN_ID": "123",
        "INPUT_SOURCE_SHA": SOURCE_SHA,
        "INPUT_BRANCH": "release/2.0.0",
        "TRUSTED_CI_WORKFLOW_ID": "268848247",
        "TRUSTED_CI_WORKFLOW_PATH": ".github/workflows/ci.yml",
    }


@pytest.mark.parametrize(
    ("workflow", "step", "occurrence"),
    [(AUTOMATIC, IDENTITY, 0), (PROMOTION, LOCATE, 0), (PROMOTION, LOCATE, 1)],
)
def test_successful_ci_handoff_without_assessment_receipt(
    handoff: dict[str, str], workflow: str, step: str, occurrence: int
) -> None:
    result = _run_script(_shell(workflow, step, occurrence), handoff)
    assert result.returncode == 0, result.stderr
    expected = (
        {"ci_run_id": "123", "source_sha": SOURCE_SHA, "source_branch": "release/2.0.0"}
        if workflow == AUTOMATIC
        else {"run_id": "123"}
    )
    assert _outputs(handoff) == expected


@pytest.mark.parametrize(
    ("field", "invalid"),
    [
        ("workflow_id", 999),
        ("name", "Other CI"),
        ("path", ".github/workflows/other.yml"),
        ("head_sha", "2" * 40),
        ("head_branch", "preview/other"),
        ("event", "pull_request"),
        ("conclusion", "failure"),
        ("head_repository", {"full_name": "fork/repository"}),
    ],
)
@pytest.mark.parametrize(
    ("workflow", "step", "occurrence"),
    [(AUTOMATIC, IDENTITY, 0), (PROMOTION, LOCATE, 0), (PROMOTION, LOCATE, 1)],
)
def test_untrusted_or_failed_ci_cannot_handoff(
    handoff: dict[str, str], field: str, invalid: object, workflow: str, step: str, occurrence: int
) -> None:
    path = Path(handoff["TEST_DATA"]) / "run.json"
    run = json.loads(path.read_text())
    run[field] = invalid
    path.write_text(json.dumps(run))
    result = _run_script(_shell(workflow, step, occurrence), handoff)
    assert result.returncode != 0
    assert "::error::" in result.stderr
    assert _outputs(handoff) == {}


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("CI_RUN_ID", "not-an-id"),
        ("SOURCE_SHA", "invalid"),
        ("SOURCE_BRANCH", "feature/not-a-release"),
        ("SOURCE_BRANCH", "main"),
    ],
)
def test_automatic_handoff_rejects_invalid_event_identity(handoff: dict[str, str], key: str, value: str) -> None:
    handoff[key] = value
    result = _run_script(_shell(AUTOMATIC, IDENTITY), handoff)
    assert result.returncode != 0
    assert _outputs(handoff) == {}


@pytest.mark.parametrize("failure", ["api-error", "malformed", "rerun"])
def test_automatic_handoff_fails_closed(handoff: dict[str, str], failure: str) -> None:
    path = Path(handoff["TEST_DATA"]) / "run.json"
    if failure == "api-error":
        handoff["TEST_API_FAIL"] = "true"
    elif failure == "malformed":
        path.write_text("not json")
    else:
        run = json.loads(path.read_text())
        run["run_attempt"] = 2
        path.write_text(json.dumps(run))
    result = _run_script(_shell(AUTOMATIC, IDENTITY), handoff)
    assert result.returncode != 0
    assert _outputs(handoff) == {}


@pytest.mark.parametrize("available", [True, False])
def test_manual_recovery_locates_ci_without_security_run_input(handoff: dict[str, str], available: bool) -> None:
    handoff["INPUT_CI_RUN_ID"] = ""
    if not available:
        (Path(handoff["TEST_DATA"]) / "runs.json").write_text("[]")
    result = _run_script(_shell(PROMOTION, LOCATE), handoff)
    assert result.returncode == (0 if available else 1)
    assert _outputs(handoff) == ({"run_id": "123"} if available else {})


@pytest.mark.parametrize("state", ["present", "missing", "expired"])
def test_only_an_available_candidate_manifest_enables_resolution(handoff: dict[str, str], state: str) -> None:
    artifacts = [] if state == "missing" else [{"id": 456, "name": "promotion-manifest", "expired": state == "expired"}]
    (Path(handoff["TEST_DATA"]) / "artifacts.json").write_text(json.dumps({"artifacts": artifacts}))
    result = _run_script(_shell(AUTOMATIC, "Check for the CI promotion manifest"), handoff, Path(handoff["TEST_DATA"]))
    assert result.returncode == 0, result.stderr
    assert _outputs(handoff) == {"available": "true" if state == "present" else "false"}
    if state != "present":
        assert "::notice::" in result.stdout


@pytest.mark.parametrize("mismatch", [None, "config", "platform", "layers"])
def test_ci_image_import_checks_the_loaded_artifact(tmp_path: Path, mismatch: str | None) -> None:
    layer = "sha256:" + "2" * 64
    config = {"User": "app", "Labels": {"org.opencontainers.image.revision": SOURCE_SHA}}
    evidence = {
        "format": "oci",
        "platform": "linux/amd64",
        "image_config": config,
        "rootfs_layers": [layer],
        "image_digest": "sha256:" + "a" * 64,
    }
    evidence_path = tmp_path / "evidence.json"
    evidence_path.write_text(json.dumps(evidence))
    inspected = {
        "Os": "linux",
        "Architecture": "arm64" if mismatch == "platform" else "amd64",
        "Config": {**config, "User": "untrusted-config-value"} if mismatch == "config" else config,
        "RootFS": {"Layers": ["sha256:" + "3" * 64] if mismatch == "layers" else [layer]},
    }
    inspect_path = tmp_path / "inspect.json"
    inspect_path.write_text(json.dumps(inspected))
    boundary = tmp_path / "boundary.sh"
    boundary.write_text(
        r"""
uv() {
  [[ "$1 $2" == "run python" ]] || return 95
  shift 2
  if [[ "$1 $2" == "-m scripts.verify_build_evidence" ]]; then
    cat "$TEST_EVIDENCE"
  elif [[ "$1 $2" == "-m scripts.validate_docker_base" ]]; then
    printf '%s\n' "$TEST_RUNTIME_BASE"
  else
    "$TEST_PYTHON" "$@"
  fi
}
docker() {
  [[ "$1 $2" == "image inspect" ]] || return 96
  cat "$TEST_INSPECT"
}
skopeo() { [[ "$1" == copy ]]; }
"""
    )
    (tmp_path / "artifacts").mkdir()
    env = {
        **os.environ,
        "BASH_ENV": str(boundary),
        "TEST_PYTHON": sys.executable,
        "TEST_EVIDENCE": str(evidence_path),
        "TEST_INSPECT": str(inspect_path),
        "TEST_RUNTIME_BASE": "dhi.io/python:3.14-debian13@sha256:" + "4" * 64,
        "GITHUB_OUTPUT": str(tmp_path / "output"),
        "SLUG": "linux-amd64",
        "LOCAL_TAG": "ob-conformance-candidate:fixture",
        "IS_HARDENED": "true",
        "PYTHONPATH": str(ROOT),
    }
    script = _shell("ci.yml", "Load image for local verification")
    script = (
        script.replace("${{ matrix.platform }}", "linux/amd64")
        .replace("${{ matrix.slug }}", "linux-amd64")
        .replace("${{ github.sha }}", SOURCE_SHA)
    )
    result = _run_script(script, env, tmp_path)
    if mismatch is None:
        assert result.returncode == 0, result.stderr
        assert _outputs(env) == {"local_tag": env["LOCAL_TAG"]}
    else:
        assert result.returncode != 0
        assert _outputs(env) == {}
        assert "Loaded image" in result.stderr
        assert "untrusted-config-value" not in result.stderr
