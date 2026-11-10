"""Select current published digests and resolve their approved OpenVEX bases.

Only older strict SemVer beta, rc and GA versions are retired. Unknown tags
remain in scope, and aliases sharing a digest are scanned together. Release
git tags are read as data; only the trusted runtime-base validator approves VEX.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess  # noqa: S404  # git is invoked with fixed argv, never a shell
import sys
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

import httpx

from scripts.list_docker_hub_tags import DockerHubTag, list_tag_records
from scripts.validate_docker_base import DockerBaseError, get_candidate_dhi_bases, get_runtime_base

_VERSION = re.compile(
    r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-(beta|rc)\.(0|[1-9]\d*))?",
    re.ASCII,
)
_TAG = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}", re.ASCII)
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}", re.ASCII)
Runner = Callable[[Sequence[str]], str]


def _version(tag: str) -> tuple[str, tuple[int, ...]] | None:
    match = _VERSION.fullmatch(tag)
    if match is None:
        return None
    major, minor, patch, channel, number = match.groups()
    return channel or "ga", (int(major), int(minor), int(patch), int(number or 0))


@dataclass(frozen=True)
class PublishedImage:
    """One immutable manifest index and every tag represented by its scan."""

    tag: str
    digest: str
    tags: list[str]
    version: str

    def target(self, platform: str) -> str:
        """Return the report target, retaining aliases in issue evidence."""
        return f"published openbanking/conformance-suite-v2:{', '.join(self.tags)} ({platform})"


def select_images(records: Sequence[DockerHubTag]) -> tuple[list[PublishedImage], list[str]]:
    """Keep the newest version per channel plus unknown tags, grouped by digest.

    Missing digests or contradictory duplicate records fail closed rather than
    silently removing a published target from vulnerability tracking.
    """
    by_name: dict[str, str] = {}
    newest: dict[str, tuple[int, ...]] = {}
    for record in records:
        if not _TAG.fullmatch(record.name) or record.digest is None or not _DIGEST.fullmatch(record.digest):
            raise ValueError(f"Invalid published tag or missing index digest: {record.name!r}.")
        if record.name in by_name and by_name[record.name] != record.digest:
            raise ValueError(f"Published tag changed during enumeration: {record.name!r}.")
        by_name[record.name] = record.digest
        version = _version(record.name)
        if version is not None:
            channel, order = version
            newest[channel] = max(newest.get(channel, order), order)
    if not by_name:
        raise ValueError("Docker Hub returned no published tags; refusing to retire all image targets.")
    current_digests = {
        digest
        for name, digest in by_name.items()
        if (version := _version(name)) is None or version[1] == newest[version[0]]
    }
    groups: dict[str, list[str]] = {}
    superseded: list[str] = []
    for name, digest in sorted(by_name.items()):
        if digest in current_digests:
            groups.setdefault(digest, []).append(name)
        else:
            superseded.append(name)
    images: list[PublishedImage] = []
    for digest, tags in sorted(groups.items()):
        versions = [tag for tag in tags if _version(tag) is not None]
        version_tag = max(versions, key=lambda tag: _version(tag) or ("", ())) if versions else ""
        images.append(PublishedImage(version_tag or tags[0], digest, tags, version_tag))
    return images, superseded


def _git(args: Sequence[str]) -> str:
    return subprocess.run(  # noqa: S603  # fixed git operations and validated release ref; no shell
        ["git", *args],  # noqa: S607  # git comes from the trusted runner
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def resolve_runtime_base(version: str, runner: Runner = _git) -> str:
    """Read a matching release's Dockerfile and approve its exact runtime base.

    Unknown/missing release tags and unapproved bases are scanned without VEX,
    with an explicit notice. Git/IO failures are not treated as missing evidence.
    """
    if not version:
        sys.stderr.write("No matching release version; scanning published digest without VEX.\n")
        return ""
    if _version(version) is None:
        raise ValueError(f"Invalid release version: {version!r}.")
    ref = f"refs/tags/v{version}"
    refs = runner(["tag", "--list", f"v{version}"]).splitlines()
    if f"v{version}" not in refs:
        sys.stderr.write(f"No git tag {ref}; scanning published digest without VEX.\n")
        return ""
    dockerfile = runner(["show", f"{ref}:Dockerfile"])
    try:
        return get_candidate_dhi_bases(dockerfile).runtime
    except DockerBaseError as error:
        try:
            return get_runtime_base(dockerfile)
        except DockerBaseError as historical_error:
            sys.stderr.write(
                f"No validated candidate or approved historical runtime base for {ref}; "
                f"scanning without VEX: {error}; {historical_error}\n"
            )
            return ""


def main(argv: list[str] | None = None) -> int:
    """Write a selection manifest, or print an approved published runtime base."""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    select = commands.add_parser("select")
    select.add_argument("--branches", required=True, help="Selected branch names as JSON.")
    select.add_argument("--output", type=Path, required=True)
    base = commands.add_parser("base")
    base.add_argument("--version", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "base":
            sys.stdout.write(resolve_runtime_base(args.version) + "\n")
        else:
            branches = json.loads(args.branches)
            if not isinstance(branches, list) or not branches or not all(isinstance(item, str) for item in branches):
                raise ValueError("Expected a non-empty JSON list of branch names.")
            images, superseded = select_images(list_tag_records())
            expected = [f"branch {branch} (linux/amd64)" for branch in branches]
            expected += [image.target(platform) for image in images for platform in ("linux/amd64", "linux/arm64")]
            manifest = {
                "images": [asdict(image) for image in images],
                "superseded": superseded,
                "expected_targets": expected,
            }
            args.output.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    except (httpx.HTTPError, OSError, ValueError, subprocess.CalledProcessError) as error:
        sys.stderr.write(f"error: Published image rescan: {error}\n")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
