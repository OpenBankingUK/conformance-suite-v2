"""Offline contract checks for Dependabot's DHI Python update rules."""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

CONFIG = Path(__file__).resolve().parents[3] / ".github/dependabot.yml"


@pytest.mark.parametrize("target_branch", ["main", "release/2.0.0"])
def test_dhi_digest_updates_keep_major_minor_ignore_and_group_matching(target_branch: str) -> None:
    config = CONFIG.read_text()
    docker_updates = [block for block in config.split("  - package-ecosystem: ")[1:] if block.startswith("docker\n")]
    assert len(docker_updates) == 2
    matching = [
        block
        for block in docker_updates
        if (
            "    target-branch:" not in block
            if target_branch == "main"
            else f"    target-branch: {target_branch}\n" in block
        )
    ]
    assert len(matching) == 1
    update = matching[0]
    # Docker dependency names omit the registry host, including for private DHI.
    assert update.split("    ignore:\n", 1)[1].split("    groups:\n", 1)[0].strip() == (
        "- dependency-name: python\n        update-types: [version-update:semver-major, version-update:semver-minor]"
    )
    assert '        patterns: ["python"]\n' in update
    assert "    registries: [dhi]\n" in update
    assert "      interval: daily\n" in update
