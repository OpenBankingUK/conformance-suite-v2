"""Move the Docker Hub beta alias to an attested multi-architecture manifest.

Called only after exact-version publication and provenance/SBOM attestations.
Registry inventory is refreshed here so older backfills cannot move the alias.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys

import httpx

from scripts.list_docker_hub_tags import list_tags
from scripts.release_metadata import (
    IMAGE_NAME,
    ReleaseChannel,
    ReleaseMetadataError,
    classify_raw_version,
    should_update_beta_latest,
)


def _inspect_digest(reference: str) -> str:
    result = subprocess.run(  # noqa: S603  # fixed argv with validated release/digest references; no shell
        ["docker", "buildx", "imagetools", "inspect", reference, "--raw"],  # noqa: S607  # trusted runner's Docker
        check=True,
        capture_output=True,
    )
    return f"sha256:{hashlib.sha256(result.stdout).hexdigest()}"


def update_beta_latest(raw_version: str, published_digest: str) -> dict[str, str | bool]:
    """Copy the highest published beta's manifest by digest and verify identity."""
    metadata = classify_raw_version(raw_version)
    if metadata.channel is not ReleaseChannel.BETA:
        raise ReleaseMetadataError("Only beta promotions may update beta-latest.")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", published_digest):
        raise ReleaseMetadataError("Published manifest digest must be a lowercase SHA-256 digest.")

    tags = list_tags()
    if raw_version not in tags:
        raise ReleaseMetadataError(f"Exact beta version {raw_version!r} is not visible in Docker Hub.")
    if not should_update_beta_latest(metadata, tags):
        return {"updated": False, "notice": f"Older backfill {raw_version}: beta-latest left unchanged."}

    if _inspect_digest(f"{IMAGE_NAME}:{raw_version}") != published_digest:
        raise ReleaseMetadataError("Exact beta version no longer matches the attested manifest digest.")

    subprocess.run(  # noqa: S603  # fixed argv, constant repository, validated SHA-256 digest; no shell
        [  # noqa: S607  # Docker is supplied by the trusted hosted runner
            "docker",
            "buildx",
            "imagetools",
            "create",
            "--tag",
            f"{IMAGE_NAME}:beta-latest",
            f"{IMAGE_NAME}@{published_digest}",
        ],
        check=True,
        stdout=sys.stderr,
    )
    if _inspect_digest(f"{IMAGE_NAME}:beta-latest") != published_digest:
        raise ReleaseMetadataError("beta-latest does not match the attested multi-architecture manifest digest.")
    return {"updated": True, "notice": f"beta-latest now points to {raw_version} at {published_digest}."}


def main(argv: list[str] | None = None) -> int:
    """Update the beta alias, printing a structured result or an explicit error."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--digest", required=True)
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    try:
        result = update_beta_latest(args.version, args.digest)
    except (ReleaseMetadataError, httpx.HTTPError, OSError, ValueError, subprocess.CalledProcessError) as error:
        if isinstance(error, subprocess.CalledProcessError) and error.stderr:
            sys.stderr.write(error.stderr.decode(errors="replace"))
        sys.stderr.write(
            f"error: beta-latest update failed after exact-version publication; "
            f"the version may already be published: {error}\n"
        )
        return 1
    sys.stdout.write(json.dumps(result) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
