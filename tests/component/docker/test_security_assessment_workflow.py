"""Execute candidate identity, assessment and promotion handoffs at offline boundaries."""

from __future__ import annotations

import base64
import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path
from zipfile import ZipFile

import pytest

pytestmark = pytest.mark.component

ROOT = Path(__file__).resolve().parents[3]
REPOSITORY = "OpenBankingUK/conformance-suite-v2"
SOURCE_SHA = "1" * 40
HEAD_SHA = "2" * 40
BASE_SHA = "3" * 40
IDENTITY = "Validate the CI run and create its pending check"
SOURCE = "Verify context and retrieve candidate files as data"
WORKFLOW = ".github/workflows/security-assessment.yml"

# Model gh's method inference and API responses, not the workflow's decisions.
# All unrecognised requests fail, so these subprocesses cannot reach the network.
BOUNDARIES = r"""
gh() {
  [[ "$1" == api ]] || return 90
  shift
  local endpoint="" method="" fields=false filter="" input="" arg
  while (( $# )); do
    arg="$1"; shift
    case "$arg" in
      --method) method="$1"; shift ;;
      -f) fields=true; [[ "$1" == "ref=$TEST_SOURCE_SHA" ]] || return 91; shift ;;
      --jq) filter="$1"; shift ;;
      --input) input="$1"; shift ;;
      --paginate) ;;
      repos/*) endpoint="$arg" ;;
      *) return 92 ;;
    esac
  done
  if [[ -z "$method" ]]; then
    method=GET
    if [[ "$fields" == true || -n "$input" ]]; then method=POST; fi
  fi
  printf '%s %s\n' "$method" "$endpoint" >> "$TEST_DATA/requests"
  local response=""
  case "$method $endpoint" in
    "GET repos/$GITHUB_REPOSITORY/actions/runs/123")
      response="$TEST_DATA/run.json" ;;
    "GET repos/$GITHUB_REPOSITORY/actions/runs/123/artifacts?per_page=100")
      response="$TEST_DATA/artifacts.json" ;;
    "GET repos/$GITHUB_REPOSITORY/commits/$TEST_HEAD_SHA/pulls")
      response="$TEST_DATA/prs.json" ;;
    "GET repos/$GITHUB_REPOSITORY/pulls/42")
      response="$TEST_DATA/pr.json" ;;
    "GET repos/$GITHUB_REPOSITORY/commits/$TEST_SOURCE_SHA")
      response="$TEST_DATA/commit.json" ;;
    "POST repos/$GITHUB_REPOSITORY/check-runs")
      cat "$input" > "$TEST_DATA/check.json"
      response="$TEST_DATA/check-response.json" ;;
    "PATCH repos/$GITHUB_REPOSITORY/check-runs/789")
      [[ "${TEST_FAIL_CHECK_UPDATE:-}" != true ]] || return 1
      cat "$input" > "$TEST_DATA/check-update.json"
      response="$TEST_DATA/check-response.json" ;;
    "GET repos/$GITHUB_REPOSITORY/actions/runs/456/approvals?per_page=100")
      response="$TEST_DATA/approvals.json" ;;
    "GET repos/$GITHUB_REPOSITORY/actions/runs/456")
      response="$TEST_DATA/assessment.json" ;;
    "GET repos/$GITHUB_REPOSITORY/actions/workflows/security-assessment.yml")
      response="$TEST_DATA/workflow.json" ;;
    "GET repos/$GITHUB_REPOSITORY/contents/"*)
      [[ "$fields" == true ]] || return 93
      local file="${endpoint##*/}"
      [[ "$file" != "${TEST_FAIL_FILE:-}" ]] || { echo "HTTP 404" >&2; return 1; }
      response="$TEST_DATA/$file.json" ;;
    *) echo "Unexpected API request: $method $endpoint" >&2; return 94 ;;
  esac
  if [[ -n "$filter" ]]; then jq -r "$filter" "$response"; else cat "$response"; fi
}
uv() {
  [[ "$1 $2" == "run python" ]] || return 95
  shift 2
  "$TEST_PYTHON" "$@"
}
"""


def _step(workflow: str, name: str) -> str:
    return (ROOT / workflow).read_text().split(f"      - name: {name}\n", 1)[1].split("      - name:", 1)[0]


def _shell(workflow: str, name: str) -> str:
    block = _step(workflow, name).split("        run: |\n", 1)[1]
    return textwrap.dedent("\n".join(line for line in block.splitlines() if line.startswith("          ") or not line))


def _run(workflow: str, name: str, env: dict[str, str], cwd: Path = ROOT) -> subprocess.CompletedProcess[str]:
    bash = shutil.which("bash")
    assert bash is not None, "Workflow shell tests require bash."
    return subprocess.run(  # noqa: S603 - repository-owned shell with GitHub/uv boundaries replaced offline
        [bash, "--noprofile", "--norc", "-c", _shell(workflow, name)],
        env=env,
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )


def _json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value))


@pytest.fixture(params=["push", "pull_request"])
def handoff(tmp_path: Path, request: pytest.FixtureRequest) -> dict[str, str]:
    event = str(request.param)
    is_pr = event == "pull_request"
    branch = "assessment-fix" if is_pr else "main"
    data = tmp_path / "api"
    data.mkdir()
    runner = tmp_path / "runner"
    runner.mkdir()
    boundary = tmp_path / "boundaries.sh"
    boundary.write_text(BOUNDARIES)
    env = {
        **os.environ,
        "BASH_ENV": str(boundary),
        "TEST_DATA": str(data),
        "TEST_PYTHON": sys.executable,
        "TEST_SOURCE_SHA": SOURCE_SHA,
        "TEST_HEAD_SHA": HEAD_SHA,
        "GH_TOKEN": "offline-test-placeholder",
        "RUNNER_TEMP": str(runner),
        "GITHUB_OUTPUT": str(tmp_path / "output"),
        "GITHUB_REPOSITORY": REPOSITORY,
        "GITHUB_SERVER_URL": "https://github.com",
        "GITHUB_RUN_ID": "456",
        "GITHUB_REF": "refs/heads/main",
        "EVENT_NAME": event,
        "REPOSITORY": REPOSITORY,
        "RUN_ID": "123",
        "RUN_ATTEMPT": "1",
        "WORKFLOW_SHA": SOURCE_SHA,
        "REF": "refs/pull/42/merge" if is_pr else "refs/heads/main",
        "REF_NAME": "42/merge" if is_pr else branch,
        "HEAD_REF": branch if is_pr else "",
        "BASE_REF": "main" if is_pr else "",
        "PR_NUMBER": "42" if is_pr else "",
        "PR_HEAD_SHA": HEAD_SHA if is_pr else "",
        "PR_BASE_SHA": BASE_SHA if is_pr else "",
        "IS_HARDENED": "true",
        "SOURCE_RUN_ID": "123",
        "EXPECTED_CI_WORKFLOW_ID": "268848247",
        "EXPECTED_CI_WORKFLOW_PATH": ".github/workflows/ci.yml",
    }
    workspace = tmp_path / "candidate"
    workspace.mkdir()
    result = _run(".github/workflows/ci.yml", "Record GitHub event identity as data", env, workspace)
    assert result.returncode == 0, result.stderr
    for file in ("Dockerfile", "pyproject.toml", "uv.lock"):
        (workspace / file).write_text("Untrusted uploaded copy: never use this as an assessment input.\n")
        _json(data / f"{file}.json", {"content": base64.b64encode((ROOT / file).read_bytes()).decode()})

    # upload-artifact preserves paths relative to the least common ancestor.
    # Derive the archive from the real producer's path list, then extract it at
    # the real consumer's download destination rather than placing JSON by hand.
    upload = _step(".github/workflows/ci.yml", "Upload candidate context")
    paths = [
        workspace / line.strip()
        for line in upload.split("          path: |\n")[1].splitlines()
        if line.startswith("            ")
    ]
    common = Path(os.path.commonpath([path.parent for path in paths]))
    download = _step(WORKFLOW, "Download candidate context")
    destination = re.search(r"          path: (.+)", download)
    assert destination is not None
    extracted = Path(destination[1].replace("${{ runner.temp }}", str(runner)))
    with ZipFile(tmp_path / "candidate-context.zip", "w") as archive:
        for path in paths:
            archive.write(path, path.relative_to(common))
    with ZipFile(tmp_path / "candidate-context.zip") as archive:
        assert set(archive.namelist()) == {
            "artifacts/candidate-context.json",
            "Dockerfile",
            "pyproject.toml",
            "uv.lock",
        }
        archive.extractall(extracted)

    _json(
        data / "run.json",
        {
            "id": 123,
            "workflow_id": 268848247,
            "path": ".github/workflows/ci.yml",
            "name": "CI",
            "head_repository": {"full_name": REPOSITORY},
            "run_attempt": 1,
            "conclusion": "success",
            "event": event,
            "head_sha": HEAD_SHA if is_pr else SOURCE_SHA,
            "head_branch": branch,
        },
    )
    pr = {
        "number": 42,
        "state": "open",
        "head": {"repo": {"full_name": REPOSITORY}, "ref": branch, "sha": HEAD_SHA},
        "base": {"sha": BASE_SHA},
    }
    _json(data / "pr.json", pr)
    _json(data / "prs.json", [pr])
    _json(data / "commit.json", {"parents": [{"sha": BASE_SHA}, {"sha": HEAD_SHA}]})
    _json(data / "artifacts.json", {"artifacts": [{"name": "candidate-context", "expired": False}]})
    _json(data / "check-response.json", {"id": 789})
    env["EVENT_NAME"] = "workflow_run"
    return env


def _identity(env: dict[str, str]) -> None:
    result = _run(WORKFLOW, IDENTITY, env)
    assert result.returncode == 0, result.stderr
    outputs = dict(line.split("=", 1) for line in Path(env["GITHUB_OUTPUT"]).read_text().splitlines())
    assert outputs == {
        "source_run_id": "123",
        "source_sha": SOURCE_SHA,
        "source_branch": env["HEAD_REF"] or "main",
        "check_sha": HEAD_SHA if env["PR_NUMBER"] else SOURCE_SHA,
        "check_id": "789",
        "pr_number": env["PR_NUMBER"],
    }
    check = json.loads((Path(env["TEST_DATA"]) / "check.json").read_text())
    assert check["head_sha"] == outputs["check_sha"]
    assert check["status"] == "in_progress"
    env.update({key.upper(): value for key, value in outputs.items()})
    Path(env["GITHUB_OUTPUT"]).write_text("")


@pytest.mark.parametrize("trigger", ["workflow_run", "workflow_dispatch"])
def test_archive_identity_and_commit_bound_inputs(handoff: dict[str, str], trigger: str) -> None:
    handoff["EVENT_NAME"] = trigger
    _identity(handoff)
    result = _run(WORKFLOW, SOURCE, handoff)
    assert result.returncode == 0, result.stderr
    for file in ("Dockerfile", "pyproject.toml", "uv.lock"):
        assert (Path(handoff["RUNNER_TEMP"]) / "assessment-inputs" / file).read_bytes() == (ROOT / file).read_bytes()
    outputs = Path(handoff["GITHUB_OUTPUT"]).read_text()
    assert outputs.startswith("hardened=true\nruntime_base=dhi.io/python:")
    requests = (Path(handoff["TEST_DATA"]) / "requests").read_text().splitlines()
    assert [line for line in requests if "/contents/" in line] == [
        f"GET repos/{REPOSITORY}/contents/{file}" for file in ("Dockerfile", "pyproject.toml", "uv.lock")
    ]


def test_manual_assessment_rejects_non_main_ref(handoff: dict[str, str]) -> None:
    handoff.update(EVENT_NAME="workflow_dispatch", GITHUB_REF="refs/heads/assessment-fix")
    result = _run(WORKFLOW, IDENTITY, handoff)
    assert result.returncode != 0
    assert "Manual reassessments must be dispatched from main." in result.stderr
    assert not (Path(handoff["TEST_DATA"]) / "requests").exists()


@pytest.mark.parametrize(
    "invalid",
    [
        "missing",
        "malformed",
        "run-id",
        "repository",
        "workflow-sha",
        "head-sha",
        "attempt",
        "conclusion",
        "workflow",
        "expired-artifact",
    ],
)
def test_identity_rejects_invalid_evidence_before_creating_check(handoff: dict[str, str], invalid: str) -> None:
    data = Path(handoff["TEST_DATA"])
    context = Path(handoff["RUNNER_TEMP"]) / "candidate-context/artifacts/candidate-context.json"
    if invalid == "missing":
        context.unlink()
    elif invalid == "malformed":
        context.write_text("{")
    elif invalid in {"run-id", "repository", "workflow-sha"}:
        value = json.loads(context.read_text())
        value[{"run-id": "run_id", "repository": "repository", "workflow-sha": "workflow_sha"}[invalid]] = "wrong"
        _json(context, value)
    elif invalid == "expired-artifact":
        _json(data / "artifacts.json", {"artifacts": [{"name": "candidate-context", "expired": True}]})
    else:
        value = json.loads((data / "run.json").read_text())
        value[
            {"head-sha": "head_sha", "attempt": "run_attempt", "conclusion": "conclusion", "workflow": "workflow_id"}[
                invalid
            ]
        ] = "wrong"
        _json(data / "run.json", value)
    result = _run(WORKFLOW, IDENTITY, handoff)
    assert result.returncode != 0
    assert result.stderr
    assert not (data / "check.json").exists()
    assert not Path(handoff["GITHUB_OUTPUT"]).exists()


@pytest.mark.parametrize("invalid", ["context", "pr-head", "merge-parents"])
def test_source_rechecks_binding_before_fetching_files(handoff: dict[str, str], invalid: str) -> None:
    if not handoff["PR_NUMBER"] and invalid != "context":
        pytest.skip("Only pull requests have merge parents and a separate head.")
    _identity(handoff)
    data = Path(handoff["TEST_DATA"])
    if invalid == "context":
        context = Path(handoff["RUNNER_TEMP"]) / "candidate-context/artifacts/candidate-context.json"
        value = json.loads(context.read_text())
        value["run_id"] = 999
        _json(context, value)
    elif invalid == "pr-head":
        value = json.loads((data / "pr.json").read_text())
        value["head"]["sha"] = "4" * 40
        _json(data / "pr.json", value)
    else:
        _json(data / "commit.json", {"parents": [{"sha": HEAD_SHA}, {"sha": BASE_SHA}]})
    result = _run(WORKFLOW, SOURCE, handoff)
    assert result.returncode != 0
    assert "/contents/" not in (data / "requests").read_text()
    assert Path(handoff["GITHUB_OUTPUT"]).read_text() == ""


@pytest.mark.parametrize("file", ["Dockerfile", "pyproject.toml", "uv.lock"])
@pytest.mark.parametrize("failure", ["http-error", "missing-content", "invalid-base64"])
def test_input_retrieval_fails_closed(handoff: dict[str, str], file: str, failure: str) -> None:
    _identity(handoff)
    if failure == "http-error":
        handoff["TEST_FAIL_FILE"] = file
    else:
        _json(
            Path(handoff["TEST_DATA"]) / f"{file}.json",
            {"content": None if failure == "missing-content" else "!!!not base64!!!"},
        )
    result = _run(WORKFLOW, SOURCE, handoff)
    assert result.returncode != 0
    assert result.stderr
    assert Path(handoff["GITHUB_OUTPUT"]).read_text() == ""


COMBINE = "Combine decisions and bind any approval to the candidate"
PROMOTION = ".github/workflows/_promote-image.yml"
AUTOMATIC = ".github/workflows/auto-promote.yml"
FINALIZE = "Complete the candidate check"
APPROVE = "Require a fresh, candidate-bound environment approval"
CREATE_APPROVAL = "Create candidate-bound approval receipt"
PLATFORMS = ("linux-amd64", "linux-arm64")


@pytest.fixture
def decisions(tmp_path: Path) -> dict[str, str]:
    data = tmp_path / "api"
    data.mkdir()
    runner = tmp_path / "runner"
    (runner / "decisions").mkdir(parents=True)
    boundary = tmp_path / "boundaries.sh"
    boundary.write_text(BOUNDARIES)
    output = tmp_path / "output"
    output.touch()
    env = {
        **os.environ,
        "BASH_ENV": str(boundary),
        "TEST_DATA": str(data),
        "RUNNER_TEMP": str(runner),
        "TEST_HEAD_SHA": HEAD_SHA,
        "TEST_SOURCE_SHA": SOURCE_SHA,
        "GITHUB_OUTPUT": str(output),
        "GITHUB_STEP_SUMMARY": str(tmp_path / "summary"),
        "GITHUB_REPOSITORY": REPOSITORY,
        "GITHUB_RUN_ID": "456",
        "SCAN_RESULT": "success",
        "RESCAN_RESULT": "success",
        "SOURCE_RUN_ID": "123",
        "CI_RUN_ID": "123",
        "ASSESSMENT_RUN_ID": "456",
        "SOURCE_SHA": SOURCE_SHA,
        "WORKFLOW_SHA": SOURCE_SHA,
        "CHECK_SHA": SOURCE_SHA,
        "INPUT_SOURCE_SHA": SOURCE_SHA,
        "CHECK_ID": "789",
        "VERIFY_RESULT": "success",
        "DECIDE_RESULT": "success",
        "APPROVAL_RESULT": "skipped",
        "EXPECTED_PHRASE": f"APPROVE {SOURCE_SHA}",
        "EXPECTED_COMMENT": f"APPROVE {SOURCE_SHA}",
        "APPROVAL_PHRASE": f"APPROVE {SOURCE_SHA}",
        "TRUSTED_ASSESSMENT_WORKFLOW_PATH": WORKFLOW,
    }
    _json(data / "check-response.json", {"id": 789})
    _json(
        data / "assessment.json",
        {
            "workflow_id": 4567,
            "path": WORKFLOW,
            "name": "Trusted security assessment",
            "head_repository": {"full_name": REPOSITORY},
            "run_attempt": 1,
            "conclusion": "success",
        },
    )
    _json(data / "workflow.json", {"id": 4567})
    _json(
        data / "run.json",
        {
            "workflow_id": 268848247,
            "path": ".github/workflows/ci.yml",
            "head_sha": SOURCE_SHA,
            "event": "push",
            "conclusion": "success",
            "run_attempt": 1,
            "head_repository": {"full_name": REPOSITORY},
            "head_branch": "main",
        },
    )
    for platform in PLATFORMS:
        _platform(env, platform, "pass")
    return env


def _platform(env: dict[str, str], platform: str, decision: str, *, promotion: bool = False) -> None:
    """Run the real producer, then model the decision artifact's download."""
    env = {**env, "SLUG": platform, "DECISION": decision, "PROVENANCE": "verified"}
    result = _run(
        PROMOTION if promotion else WORKFLOW,
        "Save promotion scan decision" if promotion else "Save platform decision",
        env,
    )
    assert result.returncode == 0, result.stderr
    runner = Path(env["RUNNER_TEMP"])
    shutil.move(runner / f"decision-{platform}.json", runner / "decisions" / f"decision-{platform}.json")


def _outputs(env: dict[str, str]) -> dict[str, str]:
    return dict(line.split("=", 1) for line in Path(env["GITHUB_OUTPUT"]).read_text().splitlines())


def _combine(env: dict[str, str], *, promotion: bool = False) -> subprocess.CompletedProcess[str]:
    return _run(PROMOTION if promotion else WORKFLOW, "Combine decisions" if promotion else COMBINE, env)


def _receipt(env: dict[str, str], decision: str) -> None:
    """Model upload/download using the workflow's actual artifact and path."""
    name = (
        "Upload candidate-bound approval receipt"
        if decision == "approved"
        else "Upload candidate-bound assessment receipt"
    )
    upload = _step(WORKFLOW, name)
    assert "name: security-assessment-receipt\n" in upload
    path = re.search(r"          path: (.+)", upload)
    assert path is not None
    source = Path(path[1].replace("${{ runner.temp }}", env["RUNNER_TEMP"]))
    destination = Path(env["RUNNER_TEMP"]) / "security-assessment"
    destination.mkdir()
    with ZipFile(destination / "receipt.zip", "w") as archive:
        archive.write(source, source.name)
    with ZipFile(destination / "receipt.zip") as archive:
        assert archive.namelist() == ["assessment-receipt.json"]
        archive.extractall(destination)
    value = json.loads((destination / "assessment-receipt.json").read_text())
    assert value == {
        "schema_version": 1,
        "decision": decision,
        "source_run_id": 123,
        "source_sha": SOURCE_SHA,
        "checked_sha": env["CHECK_SHA"],
        "approval_phrase": f"APPROVE {SOURCE_SHA}",
    }
    for workflow, step in (
        (AUTOMATIC, "Download candidate-bound assessment receipt"),
        (PROMOTION, "Download candidate-bound security assessment receipt"),
    ):
        download = _step(workflow, step)
        assert "name: security-assessment-receipt\n" in download
        assert "path: ${{ runner.temp }}/security-assessment\n" in download
    assert "if-no-files-found: error\n" in upload


@pytest.mark.parametrize("promotion", [False, True])
@pytest.mark.parametrize(
    ("left", "right", "expected"),
    [
        ("pass", "pass", "pass"),
        ("approval_required", "pass", "approval_required"),
        ("pass", "approval_required", "approval_required"),
        ("approval_required", "approval_required", "approval_required"),
        ("blocked", "approval_required", "blocked"),
        ("pass", "blocked", "blocked"),
    ],
)
def test_combiner_emits_exact_unquoted_decision(
    decisions: dict[str, str], promotion: bool, left: str, right: str, expected: str
) -> None:
    for platform, decision in zip(PLATFORMS, (left, right), strict=True):
        _platform(decisions, platform, decision, promotion=promotion)
    result = _combine(decisions, promotion=promotion)
    assert result.returncode == (1 if promotion and expected == "blocked" else 0), result.stderr
    assert _outputs(decisions) == (
        {"decision": expected} if promotion else {"decision": expected, "approval_phrase": f"APPROVE {SOURCE_SHA}"}
    )
    if not promotion:
        value = json.loads((Path(decisions["RUNNER_TEMP"]) / "assessment-receipt.json").read_text())
        assert value["decision"] == expected


@pytest.mark.parametrize("promotion", [False, True])
@pytest.mark.parametrize(
    "invalid",
    [
        "missing",
        "none",
        "malformed",
        "unknown",
        "empty",
        "no-decision",
        "duplicate",
        "wrong-platform",
        "extra",
        "array",
        "null",
        "blank",
        "stream",
        "misnamed",
        "failed-scan",
        "cancelled-scan",
    ],
)
def test_combiner_never_approves_incomplete_or_invalid_evidence(
    decisions: dict[str, str], invalid: str, promotion: bool
) -> None:
    # An eligible result must never hide invalid/missing evidence from its peer.
    _platform(decisions, PLATFORMS[0], "approval_required", promotion=promotion)
    directory = Path(decisions["RUNNER_TEMP"]) / "decisions"
    peer = directory / "decision-linux-arm64.json"
    if invalid in {"missing", "none"}:
        peer.unlink()
        if invalid == "none":
            (directory / "decision-linux-amd64.json").unlink()
    elif invalid == "malformed":
        peer.write_text("{")
    elif invalid == "blank":
        peer.write_text(" \n")
    elif invalid == "stream":
        left = directory / "decision-linux-amd64.json"
        left.write_text(left.read_text() + peer.read_text())
        peer.write_text(" \n")
    elif invalid == "misnamed":
        peer.rename(directory / "decision-unexpected.json")
    elif invalid in {"unknown", "empty"}:
        _platform(decisions, PLATFORMS[1], "unexpected" if invalid == "unknown" else "", promotion=promotion)
    elif invalid == "no-decision":
        _json(peer, {"platform": PLATFORMS[1]})
    elif invalid in {"duplicate", "wrong-platform"}:
        _json(peer, {"platform": PLATFORMS[0] if invalid == "duplicate" else "windows-amd64", "decision": "pass"})
    elif invalid == "extra":
        _json(directory / "decision-extra.json", {"platform": "extra", "decision": "pass"})
    elif invalid in {"array", "null"}:
        _json(peer, [] if invalid == "array" else None)
    else:
        decisions["RESCAN_RESULT" if promotion else "SCAN_RESULT"] = (
            "failure" if invalid == "failed-scan" else "cancelled"
        )
    result = _combine(decisions, promotion=promotion)
    assert result.returncode == (1 if promotion else 0), result.stderr
    assert _outputs(decisions)["decision"] == "blocked"
    assert "cannot be overridden" in Path(decisions["GITHUB_STEP_SUMMARY"]).read_text()


@pytest.mark.parametrize("approved", [False, True])
def test_receipt_round_trip_through_both_promotion_consumers(decisions: dict[str, str], approved: bool) -> None:
    if approved:
        _platform(decisions, PLATFORMS[1], "approval_required")
    result = _combine(decisions)
    assert result.returncode == 0, result.stderr
    decision = "approved" if approved else "pass"
    if approved:
        _json(
            Path(decisions["TEST_DATA"]) / "approvals.json",
            [
                {
                    "state": "approved",
                    "comment": f"APPROVE {SOURCE_SHA}",
                    "environments": [{"name": "security-exception"}],
                }
            ],
        )
        result = _run(WORKFLOW, APPROVE, decisions)
        assert result.returncode == 0, result.stderr
        result = _run(WORKFLOW, CREATE_APPROVAL, decisions)
        assert result.returncode == 0, result.stderr
    _receipt(decisions, decision)
    Path(decisions["GITHUB_OUTPUT"]).write_text("")
    result = _run(AUTOMATIC, "Verify the triggering workflow identity", decisions)
    assert result.returncode == 0, result.stderr
    result = _run(AUTOMATIC, "Validate assessment receipt and source CI run", decisions)
    assert result.returncode == 0, result.stderr
    assert _outputs(decisions) == {"ci_run_id": "123", "source_sha": SOURCE_SHA, "source_branch": "main"}
    result = _run(PROMOTION, "Require receipt for this exact CI candidate", decisions)
    assert result.returncode == 0, result.stderr
    result = _combine(decisions, promotion=True)
    assert result.returncode == 0, result.stderr
    assert _outputs(decisions)["decision"] == ("approval_required" if approved else "pass")
    if approved:
        result = _run(PROMOTION, "Require fresh candidate-bound environment approval", decisions)
        assert result.returncode == 0, result.stderr
    decisions.update(
        DECISION="approval_required" if approved else "pass", APPROVAL_RESULT="success" if approved else "skipped"
    )
    result = _run(WORKFLOW, FINALIZE, decisions)
    assert result.returncode == 0, result.stderr
    assert json.loads((Path(decisions["TEST_DATA"]) / "check-update.json").read_text())["conclusion"] == "success"


def test_approved_receipt_has_shared_archive_layout(decisions: dict[str, str]) -> None:
    result = _run(WORKFLOW, CREATE_APPROVAL, decisions)
    assert result.returncode == 0, result.stderr
    _receipt(decisions, "approved")


@pytest.mark.parametrize("workflow", [WORKFLOW, PROMOTION])
@pytest.mark.parametrize("invalid", ["none", "wrong-sha", "wrong-state", "wrong-environment", "malformed"])
def test_approval_is_fresh_and_candidate_bound(decisions: dict[str, str], invalid: str, workflow: str) -> None:
    approval = {
        "state": "rejected" if invalid == "wrong-state" else "approved",
        "comment": f"APPROVE {HEAD_SHA if invalid == 'wrong-sha' else SOURCE_SHA}",
        "environments": [{"name": "ga-release" if invalid == "wrong-environment" else "security-exception"}],
    }
    evidence = Path(decisions["TEST_DATA"]) / "approvals.json"
    _json(evidence, [] if invalid == "none" else [approval])
    if invalid == "malformed":
        evidence.write_text("{")
    step = APPROVE if workflow == WORKFLOW else "Require fresh candidate-bound environment approval"
    result = _run(workflow, step, decisions)
    assert result.returncode != 0
    assert result.stderr
    assert not (Path(decisions["RUNNER_TEMP"]) / "assessment-receipt.json").exists()


@pytest.mark.parametrize("workflow", [AUTOMATIC, PROMOTION])
@pytest.mark.parametrize(
    "invalid", ["missing", "malformed", "blocked", "quoted", "run-id", "sha", "checked-sha", "schema"]
)
def test_consumers_reject_invalid_receipts(decisions: dict[str, str], workflow: str, invalid: str) -> None:
    assert _combine(decisions).returncode == 0
    _receipt(decisions, "pass")
    receipt = Path(decisions["RUNNER_TEMP"]) / "security-assessment/assessment-receipt.json"
    if invalid == "missing":
        receipt.unlink()
    elif invalid == "malformed":
        receipt.write_text("{")
    else:
        value = json.loads(receipt.read_text())
        key, replacement = {
            "blocked": ("decision", "blocked"),
            "quoted": ("decision", '"pass"'),
            "run-id": ("source_run_id", 999),
            "sha": ("source_sha", HEAD_SHA),
            "checked-sha": ("checked_sha", HEAD_SHA),
            "schema": ("schema_version", 2),
        }[invalid]
        value[key] = replacement
        _json(receipt, value)
    Path(decisions["GITHUB_OUTPUT"]).write_text("")
    step = (
        "Validate assessment receipt and source CI run"
        if workflow == AUTOMATIC
        else "Require receipt for this exact CI candidate"
    )
    result = _run(workflow, step, decisions)
    assert result.returncode != 0
    assert Path(decisions["GITHUB_OUTPUT"]).read_text() == ""


@pytest.mark.parametrize("workflow", [AUTOMATIC, PROMOTION])
@pytest.mark.parametrize("invalid", ["failure", "attempt", "repository", "workflow", "path"])
def test_consumers_reject_failed_or_untrusted_assessment_runs(
    decisions: dict[str, str], workflow: str, invalid: str
) -> None:
    assert _combine(decisions).returncode == 0
    _receipt(decisions, "pass")
    path = Path(decisions["TEST_DATA"]) / "assessment.json"
    value = json.loads(path.read_text())
    key, replacement = {
        "failure": ("conclusion", "failure"),
        "attempt": ("run_attempt", 2),
        "repository": ("head_repository", {"full_name": "untrusted/repository"}),
        "workflow": ("workflow_id", 999),
        "path": ("path", ".github/workflows/untrusted.yml"),
    }[invalid]
    value[key] = replacement
    _json(path, value)
    Path(decisions["GITHUB_OUTPUT"]).write_text("")
    step = (
        "Verify the triggering workflow identity"
        if workflow == AUTOMATIC
        else "Require receipt for this exact CI candidate"
    )
    result = _run(workflow, step, decisions)
    assert result.returncode != 0
    assert Path(decisions["GITHUB_OUTPUT"]).read_text() == ""


def test_receipt_publication_and_finalization_are_gated_by_job_success() -> None:
    workflow = (ROOT / WORKFLOW).read_text()
    upload = _step(WORKFLOW, "Upload candidate-bound assessment receipt")
    assert "if: steps.combine.outputs.decision == 'pass'\n" in upload
    approval = workflow.split("\n  approve-exception:\n", 1)[1].split("\n  complete:\n", 1)[0]
    assert "if: needs.decide.outputs.decision == 'approval_required'\n" in approval
    assert "environment: security-exception\n" in approval
    assert "continue-on-error:" not in approval
    complete = workflow.split("\n  complete:\n", 1)[1]
    assert "needs: [verify, decide, approve-exception]\n" in complete
    assert "if: always() && needs.verify.outputs.check_id != ''\n" in complete
    assert "continue-on-error:" not in complete


@pytest.mark.parametrize(
    ("decision", "verify", "combine", "approval", "success"),
    [
        ("pass", "success", "success", "skipped", True),
        ("approval_required", "success", "success", "success", True),
        ("blocked", "success", "success", "skipped", False),
        ('"pass"', "success", "success", "skipped", False),
        ("", "success", "failure", "skipped", False),
        ("unknown", "success", "success", "success", False),
        ("approval_required", "success", "success", "skipped", False),
        ("approval_required", "success", "success", "failure", False),
        ("approval_required", "success", "success", "cancelled", False),
        ("pass", "failure", "success", "skipped", False),
        ("pass", "success", "failure", "skipped", False),
        ("pass", "success", "cancelled", "skipped", False),
    ],
)
def test_finalization_matches_check_and_workflow_outcome(
    decisions: dict[str, str], decision: str, verify: str, combine: str, approval: str, success: bool
) -> None:
    decisions.update(DECISION=decision, VERIFY_RESULT=verify, DECIDE_RESULT=combine, APPROVAL_RESULT=approval)
    result = _run(WORKFLOW, FINALIZE, decisions)
    assert result.returncode == (0 if success else 1), result.stderr
    value = json.loads((Path(decisions["TEST_DATA"]) / "check-update.json").read_text())
    assert value["status"] == "completed"
    assert value["conclusion"] == ("success" if success else "failure")
    assert value["output"]["summary"]
    assert value["completed_at"]
    automatic = (ROOT / AUTOMATIC).read_text()
    assert "github.event.workflow_run.conclusion == 'success'" in automatic


def test_finalization_fails_when_check_update_fails(decisions: dict[str, str]) -> None:
    decisions.update(DECISION="pass", TEST_FAIL_CHECK_UPDATE="true")
    result = _run(WORKFLOW, FINALIZE, decisions)
    assert result.returncode != 0
    assert not (Path(decisions["TEST_DATA"]) / "check-update.json").exists()
    assert f"PATCH repos/{REPOSITORY}/check-runs/789" in (Path(decisions["TEST_DATA"]) / "requests").read_text()
