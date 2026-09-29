"""Filesystem anchors for tests that read files shipped in the repository."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
"""Repository root, so bundled-data paths survive moves inside ``tests/``."""
