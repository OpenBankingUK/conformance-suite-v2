"""Unit tests for :mod:`scripts.validate_promotion` (the CI-facing CLI)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.validate_promotion import main

pytestmark = pytest.mark.unit


def _write_manifest(tmp_path: Path, **overrides: object) -> Path:
    """Write a promotion manifest JSON file with sensible preview defaults.

    Args:
        tmp_path: Pytest-provided temporary directory.
        **overrides: Fields to override on top of the default preview manifest.

    Returns:
        Path to the written manifest JSON file.
    """
    manifest: dict[str, object] = {
        "raw_version": "2.0.0-dev.1",
        "comparison_version": "2.0.0.dev1",
        "channel": "preview",
        "image_name": "docker.io/openbanking/conformance-suite-v2",
        "source_sha": "a" * 40,
        "platform_digests": {
            "linux/amd64": "sha256:" + "1" * 64,
            "linux/arm64": "sha256:" + "2" * 64,
        },
        "oci_labels": {
            "org.opencontainers.image.revision": "a" * 40,
            "org.opencontainers.image.version": "2.0.0-dev.1",
        },
        "expected_tags": ["2.0.0-dev.1"],
    }
    manifest.update(overrides)
    if "raw_version" in overrides and "oci_labels" not in overrides:
        labels = manifest["oci_labels"]
        assert isinstance(labels, dict)
        labels["org.opencontainers.image.version"] = manifest["raw_version"]
    manifest_path = tmp_path / "promotion-manifest.json"
    manifest_path.write_text(json.dumps(manifest))
    return manifest_path


class TestMain:
    """Behaviour of the ``validate_promotion`` CLI entrypoint."""

    def test_accepts_valid_preview_manifest(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """A manifest matching its requested source SHA and branch is accepted."""
        manifest_path = _write_manifest(tmp_path)

        exit_code = main(
            [
                "--manifest",
                str(manifest_path),
                "--source-sha",
                "a" * 40,
                "--branch",
                "preview/new-cert-flow",
                "--channel",
                "preview",
            ]
        )

        assert exit_code == 0
        result = json.loads(capsys.readouterr().out)
        assert result["expected_tags"] == ["2.0.0-dev.1"]
        assert result["update_beta_latest"] is False

    def test_ga_manifest_expects_latest_tag(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """A valid GA manifest's expected tags include the version and latest."""
        manifest_path = _write_manifest(tmp_path, raw_version="2.0.0", channel="ga")

        exit_code = main(
            [
                "--manifest",
                str(manifest_path),
                "--source-sha",
                "a" * 40,
                "--branch",
                "main",
                "--channel",
                "ga",
                "--tag",
                "v2.0.0",
            ]
        )

        assert exit_code == 0
        result = json.loads(capsys.readouterr().out)
        assert result["expected_tags"] == ["2.0.0", "latest"]
        assert result["update_beta_latest"] is False

    @pytest.mark.parametrize(
        ("tags", "eligible"),
        [
            ("latest\nbeta-latest\n2.0.0-beta.9\n", True),
            ("2.1.0-beta.1\n", True),
            ("2.0.0-beta.11\n", False),
            ("2.0.0\n", False),
        ],
    )
    def test_beta_alias_is_separate_from_initial_tags(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str], tags: str, eligible: bool
    ) -> None:
        """Beta publication leaves the alias for the post-attestation update."""
        manifest_path = _write_manifest(tmp_path, raw_version="2.0.0-beta.10", channel="beta")
        published_path = tmp_path / "published.txt"
        published_path.write_text(tags)
        assert (
            main(
                [
                    "--manifest",
                    str(manifest_path),
                    "--source-sha",
                    "a" * 40,
                    "--branch",
                    "release/2.0.0",
                    "--channel",
                    "beta",
                    "--published-versions-file",
                    str(published_path),
                ]
            )
            == 0
        )
        result = json.loads(capsys.readouterr().out)
        assert result["expected_tags"] == ["2.0.0-beta.10"]
        assert result["update_beta_latest"] is eligible

    def test_supplied_missing_inventory_fails(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """A missing registry inventory must not silently disable publication guards."""
        manifest_path = _write_manifest(tmp_path)
        assert (
            main(
                [
                    "--manifest",
                    str(manifest_path),
                    "--source-sha",
                    "a" * 40,
                    "--branch",
                    "preview/new-cert-flow",
                    "--channel",
                    "preview",
                    "--published-versions-file",
                    str(tmp_path / "missing.txt"),
                ]
            )
            == 1
        )
        assert "error:" in capsys.readouterr().err

    def test_ga_manifest_without_tag_is_rejected(self, tmp_path: Path) -> None:
        """A GA promotion request missing --tag is rejected."""
        manifest_path = _write_manifest(tmp_path, raw_version="2.0.0", channel="ga")

        exit_code = main(
            [
                "--manifest",
                str(manifest_path),
                "--source-sha",
                "a" * 40,
                "--branch",
                "main",
                "--channel",
                "ga",
            ]
        )

        assert exit_code == 1


def _summary_args(tmp_path: Path, *, version: str = "2.0.0-dev.1", channel: str = "preview") -> list[str]:
    manifest = _write_manifest(tmp_path, raw_version=version, channel=channel)
    inventory = tmp_path / "published.txt"
    inventory.write_text("")
    branch = {"preview": "preview/new-cert-flow", "beta": f"release/{version.split('-')[0]}", "ga": "main"}[channel]
    args = [
        "--manifest",
        str(manifest),
        "--source-sha",
        "a" * 40,
        "--branch",
        branch,
        "--channel",
        channel,
        "--published-versions-file",
        str(inventory),
        "--summary-file",
        str(tmp_path / "summary.md"),
        "--environment-name",
        f"{channel}-release",
    ]
    if channel == "ga":
        args.extend(["--tag", f"v{version}"])
    return args


@pytest.mark.parametrize(
    ("version", "channel", "published", "publication_tags", "moving_tag", "eligible"),
    [
        ("2.0.0-dev.1", "preview", "", "<code>2.0.0-dev.1</code>", "None", False),
        ("2.0.0", "ga", "", "<code>2.0.0</code>, <code>latest</code>", "<code>latest</code>", False),
        (
            "2.0.0-beta.6",
            "beta",
            "2.0.0-beta.5\n",
            "<code>2.0.0-beta.6</code>",
            "<code>2.0.0-beta-latest</code> (eligible; rechecked after publication and attestations)",
            True,
        ),
        (
            "2.0.0-beta.6",
            "beta",
            "2.0.0-beta.7\n",
            "<code>2.0.0-beta.6</code>",
            "<code>2.0.0-beta-latest</code> unchanged (not eligible under current policy)",
            False,
        ),
        (
            "2.0.0-beta.6",
            "beta",
            "2.0.0\n",
            "<code>2.0.0-beta.6</code>",
            "<code>2.0.0-beta-latest</code> unchanged (not eligible under current policy)",
            False,
        ),
        (
            "2.1.0-beta.1",
            "beta",
            "",
            "<code>2.1.0-beta.1</code>",
            "<code>2.0.0-beta-latest</code> unchanged (not eligible under current policy)",
            False,
        ),
    ],
)
def test_writes_exact_preapproval_summary(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    version: str,
    channel: str,
    published: str,
    publication_tags: str,
    moving_tag: str,
    eligible: bool,
) -> None:
    args = _summary_args(tmp_path, version=version, channel=channel)
    (tmp_path / "published.txt").write_text(published)
    assert main(args) == 0
    result_with_summary = json.loads(capsys.readouterr().out)
    branch = args[args.index("--branch") + 1]
    assert (tmp_path / "summary.md").read_text() == (
        "## Release proposed for approval\n\n"
        "Proposed publication only; this summary does not mean the image has been published.\n\n"
        "| Release detail | Value |\n"
        "| --- | --- |\n"
        f"| Version | <code>{version}</code> |\n"
        "| Image | <code>docker.io/openbanking/conformance-suite-v2</code> |\n"
        f"| Publication tags | {publication_tags} |\n"
        f"| Moving tag | {moving_tag} |\n"
        f"| Channel | <code>{channel}</code> |\n"
        f"| Approval environment | <code>{channel}-release</code> |\n"
        f"| Source branch | <code>{branch}</code> |\n"
        f"| Source commit | <code>{'a' * 40}</code> |\n\n"
        "Tag eligibility reflects the current registry inventory. Publication is revalidated after approval.\n"
    )
    assert result_with_summary["update_beta_latest"] is eligible
    summary_index = args.index("--summary-file")
    assert main(args[:summary_index] + args[summary_index + 4 :]) == 0
    assert json.loads(capsys.readouterr().out) == result_with_summary


def test_summary_appends_and_escapes_environment_context(tmp_path: Path) -> None:
    args = _summary_args(tmp_path)
    args[args.index("--environment-name") + 1] = "preview|<script>\r\nreview & approve"
    summary_path = tmp_path / "summary.md"
    summary_path.write_text("Existing summary\n\n")
    assert main(args) == 0
    summary = summary_path.read_text()
    assert summary.startswith("Existing summary\n\n## Release proposed for approval")
    assert "| Approval environment | <code>preview&#124;&lt;script&gt;  review &amp; approve</code> |" in summary


@pytest.mark.parametrize(
    "failure", ["invalid-manifest", "already-published", "missing-inventory", "missing-environment"]
)
def test_failed_validation_does_not_write_summary(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], failure: str
) -> None:
    args = _summary_args(tmp_path)
    if failure == "invalid-manifest":
        _write_manifest(tmp_path, image_name="docker.io/example/other")
    elif failure == "already-published":
        (tmp_path / "published.txt").write_text("2.0.0-dev.1\n")
    elif failure == "missing-inventory":
        (tmp_path / "published.txt").unlink()
    else:
        args = args[:-2]
    assert main(args) == 1
    assert not (tmp_path / "summary.md").exists()
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "error:" in captured.err


def test_summary_write_failure_is_explicit(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    args = _summary_args(tmp_path)
    (tmp_path / "summary.md").mkdir()
    assert main(args) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "error:" in captured.err
    assert "summary.md" in captured.err


def test_summary_is_in_ungated_job_for_verified_candidate() -> None:
    workflow = (Path(__file__).resolve().parents[3] / ".github/workflows/_promote-image.yml").read_text()
    locate = workflow.split("  locate:\n", 1)[1].split("  rescan:\n", 1)[0]
    assert "    environment:" not in locate
    assert locate.index("- name: Locate and verify") < locate.index("- name: Download the promotion manifest")
    assert locate.index("- name: Download the promotion manifest") < locate.index("- name: Summarize the release")
    assert "run-id: ${{ steps.locate.outputs.run_id }}" in locate
    assert "uv run python -m scripts.list_docker_hub_tags > published-versions.txt" in locate
    assert '--manifest "$RUNNER_TEMP/approval-manifest/promotion-manifest.json"' in locate
    assert '--source-sha "$INPUT_SOURCE_SHA"' in locate
    assert '--branch "$INPUT_BRANCH"' in locate
    assert '--channel "$INPUT_CHANNEL"' in locate
    assert '--summary-file "$GITHUB_STEP_SUMMARY"' in locate
    assert '--environment-name "$APPROVAL_ENVIRONMENT"' in locate
    assert 'args+=(--tag "$INPUT_TAG")' in locate
    assert 'uv run python -m scripts.validate_promotion "${args[@]}"' in locate
    assert "    needs: locate\n" in workflow.split("  rescan:\n", 1)[1]
    protected_job = workflow.split("  promote:\n", 1)[1]
    assert "    needs: [locate, rescan]\n" in protected_job
    assert "    environment: ${{ inputs.environment_name }}\n" in protected_job


class TestManifestRejections:
    """Invalid candidate manifests must not reach publication."""

    def test_rejects_mismatched_source_sha(self, tmp_path: Path) -> None:
        """A manifest whose source SHA differs from the request is rejected."""
        manifest_path = _write_manifest(tmp_path)

        exit_code = main(
            [
                "--manifest",
                str(manifest_path),
                "--source-sha",
                "b" * 40,
                "--branch",
                "preview/new-cert-flow",
                "--channel",
                "preview",
            ]
        )

        assert exit_code == 1

    def test_rejects_mismatched_branch(self, tmp_path: Path) -> None:
        """A preview manifest requested for an incompatible branch is rejected."""
        manifest_path = _write_manifest(tmp_path)

        exit_code = main(
            [
                "--manifest",
                str(manifest_path),
                "--source-sha",
                "a" * 40,
                "--branch",
                "main",
                "--channel",
                "preview",
            ]
        )

        assert exit_code == 1

    def test_rejects_incomplete_platform_set(self, tmp_path: Path) -> None:
        """A manifest missing a required platform digest is rejected."""
        manifest_path = _write_manifest(tmp_path, platform_digests={"linux/amd64": "sha256:" + "1" * 64})

        exit_code = main(
            [
                "--manifest",
                str(manifest_path),
                "--source-sha",
                "a" * 40,
                "--branch",
                "preview/new-cert-flow",
                "--channel",
                "preview",
            ]
        )

        assert exit_code == 1

    def test_rejects_already_published_version(self, tmp_path: Path) -> None:
        """A version already present in the published-versions file is rejected."""
        manifest_path = _write_manifest(tmp_path)
        published_path = tmp_path / "published-versions.txt"
        published_path.write_text("2.0.0-dev.1\n1.9.0\n")

        exit_code = main(
            [
                "--manifest",
                str(manifest_path),
                "--source-sha",
                "a" * 40,
                "--branch",
                "preview/new-cert-flow",
                "--channel",
                "preview",
                "--published-versions-file",
                str(published_path),
            ]
        )

        assert exit_code == 1

    def test_missing_manifest_file_is_rejected(self, tmp_path: Path) -> None:
        """A manifest path that does not exist fails cleanly rather than crashing."""
        exit_code = main(
            [
                "--manifest",
                str(tmp_path / "missing.json"),
                "--source-sha",
                "a" * 40,
                "--branch",
                "preview/new-cert-flow",
                "--channel",
                "preview",
            ]
        )

        assert exit_code == 1

    def test_rejects_channel_mismatch(self, tmp_path: Path) -> None:
        """A caller cannot promote a manifest through a different channel workflow."""
        manifest_path = _write_manifest(tmp_path)

        exit_code = main(
            [
                "--manifest",
                str(manifest_path),
                "--source-sha",
                "a" * 40,
                "--branch",
                "preview/new-cert-flow",
                "--channel",
                "beta",
            ]
        )

        assert exit_code == 1

    def test_rejects_unexpected_image_name(self, tmp_path: Path) -> None:
        """A manifest cannot redirect publication to another registry package."""
        manifest_path = _write_manifest(tmp_path, image_name="docker.io/example/other")

        exit_code = main(
            [
                "--manifest",
                str(manifest_path),
                "--source-sha",
                "a" * 40,
                "--branch",
                "preview/new-cert-flow",
                "--channel",
                "preview",
            ]
        )

        assert exit_code == 1

    def test_rejects_mismatched_revision_label(self, tmp_path: Path) -> None:
        """The OCI revision label must bind the image to the requested source SHA."""
        manifest_path = _write_manifest(
            tmp_path,
            oci_labels={"org.opencontainers.image.revision": "b" * 40},
        )

        exit_code = main(
            [
                "--manifest",
                str(manifest_path),
                "--source-sha",
                "a" * 40,
                "--branch",
                "preview/new-cert-flow",
                "--channel",
                "preview",
            ]
        )

        assert exit_code == 1
