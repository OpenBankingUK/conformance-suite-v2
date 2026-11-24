"""Offline contract checks for Dependabot's DHI Python update rules."""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

CONFIG = Path(__file__).resolve().parents[3] / ".github/dependabot.yml"


def test_dhi_digest_updates_keep_major_minor_ignore_and_group_matching() -> None:
    config = CONFIG.read_text()
    docker_updates = [block for block in config.split("  - package-ecosystem: ")[1:] if block.startswith("docker\n")]
    assert len(docker_updates) == 1
    assert "target-branch:" not in config
    update = docker_updates[0]
    # Docker dependency names omit the registry host, including for private DHI.
    assert update.split("    ignore:\n", 1)[1].split("    groups:\n", 1)[0].strip() == (
        "- dependency-name: python\n        update-types: [version-update:semver-major, version-update:semver-minor]"
    )
    assert '        patterns: ["python"]\n' in update
    assert "    registries: [dhi]\n" in update
    assert "      interval: daily\n" in update
