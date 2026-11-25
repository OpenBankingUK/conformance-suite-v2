"""Execute CI-to-promotion handoffs and image import at offline boundaries."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import textwrap
from collections.abc import Mapping
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
SCOUT_STEP = "Report Docker Scout findings"

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
        "head_branch": "main",
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
                    "headBranch": "main",
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
        "SOURCE_BRANCH": "main",
        "INPUT_CI_RUN_ID": "123",
        "INPUT_SOURCE_SHA": SOURCE_SHA,
        "INPUT_BRANCH": "main",
        "TRUSTED_CI_WORKFLOW_ID": "268848247",
        "TRUSTED_CI_WORKFLOW_PATH": ".github/workflows/ci.yml",
    }


@pytest.mark.parametrize(
    ("workflow", "step", "occurrence"),
    [(AUTOMATIC, IDENTITY, 0), (PROMOTION, LOCATE, 0), (PROMOTION, LOCATE, 1)],
)
@pytest.mark.parametrize("branch", ["main", "preview/new-flow"])
def test_successful_ci_handoff_without_assessment_receipt(
    handoff: dict[str, str], workflow: str, step: str, occurrence: int, branch: str
) -> None:
    path = Path(handoff["TEST_DATA"]) / "run.json"
    run = json.loads(path.read_text())
    run["head_branch"] = branch
    path.write_text(json.dumps(run))
    handoff["SOURCE_BRANCH"] = branch
    handoff["INPUT_BRANCH"] = branch
    result = _run_script(_shell(workflow, step, occurrence), handoff)
    assert result.returncode == 0, result.stderr
    expected = (
        {"ci_run_id": "123", "source_sha": SOURCE_SHA, "source_branch": branch}
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
        ("SOURCE_BRANCH", "release/2.0.0"),
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


@pytest.mark.parametrize(
    ("event", "branch", "candidate"),
    [
        ("push", "main", "true"),
        ("push", "preview/new-flow", "true"),
        ("push", "release/2.0.0", "false"),
        ("push", "feature/new-flow", "false"),
        ("pull_request", "358/merge", "false"),
    ],
)
def test_ci_only_builds_publishable_candidates_on_active_push_branches(
    tmp_path: Path, event: str, branch: str, candidate: str
) -> None:
    env = {
        **os.environ,
        "TEST_PYTHON": sys.executable,
        "GITHUB_OUTPUT": str(tmp_path / "output"),
        "EVENT_NAME": event,
        "PUSH_REF_NAME": branch,
    }
    result = _run_script('python3() { "$TEST_PYTHON" "$@"; }\n' + _shell("ci.yml", "Classify this run"), env)
    assert result.returncode == 0, result.stderr
    assert _outputs(env) == {"target_branch": branch, "is_hardened": "true", "is_candidate": candidate}


@pytest.mark.parametrize(
    ("version", "promote", "channel", "environment"),
    [
        ("2.0.0-beta.27", "true", "beta", "beta-release"),
        ("2.0.0-beta.26", "false", "beta", "beta-release"),
        ("2.0.0", "false", "ga", ""),
    ],
)
def test_main_resolution_handoff_publishes_only_unpublished_betas(
    tmp_path: Path, version: str, promote: str, channel: str, environment: str
) -> None:
    (tmp_path / "candidate-pyproject.toml").write_text(f'[project]\nversion = "{version}"\n')
    (tmp_path / "published-versions.txt").write_text("2.0.0-beta.26\n")
    env = {
        **os.environ,
        "TEST_PYTHON": sys.executable,
        "PYTHONPATH": str(ROOT),
        "GITHUB_OUTPUT": str(tmp_path / "output"),
        "SOURCE_BRANCH": "main",
        "SOURCE_SHA": SOURCE_SHA,
    }
    result = _run_script(
        'uv() { [[ "$1 $2" == "run python" ]] || return 95; shift 2; "$TEST_PYTHON" "$@"; }\n'
        + _shell(AUTOMATIC, "Resolve promotion policy"),
        env,
        tmp_path,
    )
    assert result.returncode == 0, result.stderr
    assert _outputs(env) == {
        "should_promote": promote,
        "channel": channel,
        "environment_name": environment,
        "tag": "",
    }
    if promote == "false":
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


def _scout_environment(tmp_path: Path, provenance: Mapping[str, object], failure: str = "") -> dict[str, str]:
    stubs = tmp_path / "bin"
    stubs.mkdir()
    commands = {
        "curl": r"""#!/bin/bash
while (( $# )); do
  if [[ "$1" == -o ]]; then
    mkdir -p "$(dirname "$2")"
    : > "$2"
    exit 0
  fi
  shift
done
exit 1
""",
        "sha256sum": '#!/bin/bash\ncat > "$RUNNER_TEMP/checksum-input"\n',
        "tar": r"""#!/bin/bash
target=""
last=""
while (( $# )); do
  if [[ "$1" == -C ]]; then
    target="$2"
    shift 2
  else
    last="$1"
    shift
  fi
done
if [[ "$last" == docker-scout ]]; then
  : > "$target/docker-scout"
fi
""",
        "docker": r"""#!/bin/bash
case "$*" in
  "buildx imagetools inspect "*)
    [[ "${TEST_SCOUT_FAIL:-}" != manifest ]] || exit 41
    if [[ "$*" == *"{{.Manifest.Digest}}"* ]]; then
      printf 'sha256:%064d\n' 1
    else
      cat "$TEST_PROVENANCE"
    fi
    ;;
  "scout compare "*)
    [[ "${TEST_SCOUT_FAIL:-}" != compare ]] || exit 42
    while (( $# )); do
      if [[ "$1" == --output ]]; then
        printf '<img alt="No changes" src="https://example.invalid/badge"/>\n' > "$2"
        exit 0
      fi
      shift
    done
    exit 1
    ;;
  "scout cves "*)
    if [[ "$*" == *"--format gitlab"* ]]; then
      [[ "$*" == "scout cves oci-dir://$RUNNER_TEMP/scout-impact/oci --platform $PLATFORM --format gitlab --output "* ]] || exit 45
      cp "$TEST_REPORT" "$9" || exit 46
      # Simulate a scanner leaving output on disk before reporting a failure.
      [[ "${TEST_SCOUT_FAIL:-}" != cves ]]
      exit
    fi
    [[ "${TEST_SCOUT_FAIL:-}" != cves ]] || exit 43
    printf 'fixture-package 1.0.0\n'
    ;;
  *)
    echo "Unexpected docker invocation: $*" >&2
    exit 44
    ;;
esac
""",
    }
    for name, content in commands.items():
        path = stubs / name
        path.write_text(content)
        path.chmod(0o755)

    provenance_path = tmp_path / "provenance.json"
    provenance_path.write_text(json.dumps(provenance))
    home = tmp_path / "home"
    runner_temp = tmp_path / "runner-temp"
    home.mkdir()
    runner_temp.mkdir()
    work = runner_temp / "scout-impact"
    (work / "cache").mkdir(parents=True)
    docker_scout = home / ".docker" / "scout"
    docker_scout.mkdir(parents=True)
    (work / "cache" / "sentinel").write_text("private cache state")
    (docker_scout / "sentinel").write_text("private Scout state")
    archive = tmp_path / "candidate.tar"
    archive.touch()

    return {
        "PATH": f"{stubs}{os.pathsep}{os.environ['PATH']}",
        "HOME": str(home),
        "RUNNER_TEMP": str(runner_temp),
        "RUNNER_ARCH": "X64",
        "SCOUT_CLI_VERSION": "1.29.0",
        "SCOUT_CLI_SHA256_AMD64": "0" * 64,
        "SCOUT_CLI_SHA256_ARM64": "1" * 64,
        "PR_SCOUT_PREVIEW": "false",
        "COMPARE_BETA": "true",
        "PUBLISHED_IMAGE": "openbanking/conformance-suite-v2:2.0.0-beta-latest",
        "RUNTIME_BASE": "dhi.io/python:3.14@sha256:" + "a" * 64,
        "PLATFORM": "linux/amd64",
        "ARCHIVE": str(archive),
        "GITHUB_STEP_SUMMARY": str(tmp_path / "summary.md"),
        "DOCKER_SCOUT_HUB_USER": "stub",
        "DOCKER_SCOUT_HUB_PASSWORD": "stub",  # pragma: allowlist secret -- fake test stub
        "TEST_PROVENANCE": str(provenance_path),
        "TEST_SCOUT_FAIL": failure,
    }


@pytest.mark.parametrize(
    ("published_base_digest", "expected_status"),
    [
        ("sha256:" + "a" * 64, "**unchanged** from the published image"),
        ("sha256:" + "b" * 64, "**changed** from the published `sha256:" + "b" * 64 + "`"),
        ("", "not found in the published image's provenance"),
    ],
)
def test_scout_report_summarizes_published_base_provenance_and_cleans_state(
    tmp_path: Path, published_base_digest: str, expected_status: str
) -> None:
    materials = (
        [{"uri": "pkg:docker/dhi.io/python@3.14", "digest": {"sha256": published_base_digest.removeprefix("sha256:")}}]
        if published_base_digest
        else []
    )
    env = _scout_environment(tmp_path, {"linux/amd64": {"SLSA": {"materials": materials}}})
    result = _run_script(_shell("ci.yml", SCOUT_STEP), env)

    assert result.returncode == 0, result.stderr
    summary = Path(env["GITHUB_STEP_SUMMARY"]).read_text()
    assert "## Docker Scout impact (linux/amd64)" in summary
    assert f"**Runtime base:** `{env['RUNTIME_BASE']}` is {expected_status}." in summary
    assert "`No changes`" in summary
    assert "fixture-package 1.0.0" in summary
    assert not (Path(env["RUNNER_TEMP"]) / "scout-impact").exists()
    assert not (Path(env["HOME"]) / ".docker" / "scout").exists()


def test_failed_scout_command_warns_and_cleans_both_state_directories(tmp_path: Path) -> None:
    provenance = {
        "linux/amd64": {
            "SLSA": {
                "materials": [{"uri": "pkg:docker/dhi.io/python@3.14", "digest": {"sha256": "a" * 64}}],
            }
        }
    }
    env = _scout_environment(tmp_path, provenance, failure="compare")
    result = _run_script(_shell("ci.yml", SCOUT_STEP), env)

    assert result.returncode == 42
    assert "::warning title=Scout impact report incomplete::" in result.stdout
    assert not (Path(env["RUNNER_TEMP"]) / "scout-impact").exists()
    assert not (Path(env["HOME"]) / ".docker" / "scout").exists()


def _preview_report(tmp_path: Path) -> tuple[str, str]:
    path = tmp_path / "summary.md"
    env = {
        "PATH": os.environ["PATH"],
        "GITHUB_STEP_SUMMARY": str(path),
        "GITHUB_SHA": SOURCE_SHA,
        "GITHUB_REPOSITORY": REPOSITORY,
        "GITHUB_RUN_ID": "123",
        "GITHUB_SERVER_URL": "https://github.com",
    }
    result = _run_script(_shell("ci.yml", "View vulnerability report (packages and CVEs)"), env, cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    return path.read_text(), result.stdout


def test_pr_scout_preview_reports_both_final_images_without_a_published_baseline(tmp_path: Path) -> None:
    reports = tmp_path / "reports"
    reports.mkdir()
    for arch, severities in (("amd64", ["Critical", "High", "High", "Medium", "Low", "Unknown"]), ("arm64", [])):
        platform_dir = tmp_path / arch
        platform_dir.mkdir()
        env = _scout_environment(platform_dir, {}, failure="manifest")
        fixture = platform_dir / "input.json"
        vulnerabilities = [
            {
                "severity": s,
                "identifiers": [{"value": f"CVE-2026-{1000 + index}"}],
                "location": {"dependency": {"package": {"name": "fixture-package"}, "version": "1.0"}},
            }
            for index, s in enumerate(severities)
        ]
        fixture.write_text(json.dumps({"scan": {"status": "success"}, "vulnerabilities": vulnerabilities}))
        env.update(
            PR_SCOUT_PREVIEW="true",
            COMPARE_BETA="true" if arch == "amd64" else "false",
            PLATFORM=f"linux/{arch}",
            RUNNER_ARCH="X64" if arch == "amd64" else "ARM64",
            TEST_REPORT=str(fixture),
        )
        result = _run_script(_shell("ci.yml", SCOUT_STEP), env)
        assert result.returncode == 0, result.stderr
        assert ("Could not resolve the published beta" in result.stdout) == (arch == "amd64")
        temp = Path(env["RUNNER_TEMP"])
        assert (temp / "checksum-input").read_text().split() == [
            env[f"SCOUT_CLI_SHA256_{arch.upper()}"],
            str(temp / f"scout-impact/docker-scout_1.29.0_linux_{arch}.tar.gz"),
        ]
        report = temp / "scout-preview"
        assert [p.name for p in report.iterdir()] == ["cves.json"]
        report.rename(reports / f"scout-preview-linux-{arch}")

    summary, log = _preview_report(tmp_path)
    assert SOURCE_SHA in summary
    assert "| linux/amd64 | 1 | 2 | 1 | 1 | 1 | Scanned |" in summary
    assert "| linux/arm64 | 0 | 0 | 0 | 0 | 0 | Scanned |" in summary
    assert summary in log
    assert "Package findings for linux/amd64\n" in log
    assert "Critical\tCVE-2026-1000\tfixture-package\t1.0" in log
    assert "Package findings for linux/arm64\nNo findings." in log


@pytest.mark.parametrize("cause", ["credentials", "cves", "invalid-report"])
def test_pr_scout_preview_distinguishes_unavailable_scans_from_zero_findings(tmp_path: Path, cause: str) -> None:
    env = _scout_environment(tmp_path, {}, failure=cause)
    fixture = tmp_path / "input.json"
    fixture.write_text('{"scan":{"status":"success"},"vulnerabilities":null}')
    env.update(PR_SCOUT_PREVIEW="true", COMPARE_BETA="false", TEST_REPORT=str(fixture))
    if cause == "credentials":
        env["DOCKER_SCOUT_HUB_PASSWORD"] = ""
    result = _run_script(_shell("ci.yml", SCOUT_STEP), env)
    assert result.returncode == 0, result.stderr
    reports = tmp_path / "reports"
    reports.mkdir()
    report = Path(env["RUNNER_TEMP"]) / "scout-preview"
    if cause != "invalid-report":
        assert not (report / "cves.json").exists()
    report.rename(reports / "scout-preview-linux-amd64")
    (reports / "scout-preview-linux-arm64").mkdir()
    (reports / "scout-preview-linux-arm64/cves.json").write_text('{"scan":{"status":"success"},"vulnerabilities":[]}')
    summary, log = _preview_report(tmp_path)
    reason = "no Docker Hub credentials" if cause == "credentials" else "see the Image job"
    assert f"| linux/amd64 | - | - | - | - | - | Unavailable: {reason} |" in summary
    assert "| linux/arm64 | 0 | 0 | 0 | 0 | 0 | Scanned |" in summary
    assert summary in log
    assert "Package findings for linux/amd64" not in log


def test_pr_scout_job_displays_unavailability_when_neither_report_exists(tmp_path: Path) -> None:
    summary, log = _preview_report(tmp_path)
    assert summary in log
    assert log.count("Unavailable: see the Image job") == 2
    assert "No findings." not in log
