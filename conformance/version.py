"""Version metadata helpers for generated conformance reports."""

from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from pathlib import Path

CONFORMANCE_TOOL_VERSION_ENV = "CONFORMANCE_TOOL_VERSION"
"""Environment variable used to stamp release builds with the tool version."""

REPORT_METADATA_VERSION = "1.0"
"""Stable report metadata version emitted in generated result files."""

UNKNOWN_TOOL_VERSION = "0+unknown"
"""Fallback tool version when no configured or project version can be resolved."""

_PYPROJECT_VERSION_CACHE: dict[Path, str | None] = {}
"""Cached ``[project].version`` values keyed by ``pyproject.toml`` path."""


def resolve_conformance_tool_version(
    *,
    environ: Mapping[str, str] | None = None,
    pyproject_path: Path | None = None,
) -> str:
    """Resolve the conformance tool version used to stamp reports.

    Args:
        environ: Optional environment mapping. Defaults to ``os.environ`` and
            is injectable for tests.
        pyproject_path: Optional path to ``pyproject.toml``. Defaults to the
            repository root's file and is injectable for source-layout tests.

    Returns:
        Version string sourced from ``CONFORMANCE_TOOL_VERSION``, then
        ``pyproject.toml``'s ``[project].version``, then ``0+unknown``.
    """
    environment = os.environ if environ is None else environ
    configured_version = environment.get(CONFORMANCE_TOOL_VERSION_ENV)
    if configured_version is not None:
        stripped_version = configured_version.strip()
        if stripped_version:
            return stripped_version

    project_version = _read_pyproject_version(pyproject_path or _default_pyproject_path())
    return project_version or UNKNOWN_TOOL_VERSION


def _default_pyproject_path() -> Path:
    """Return the source-tree ``pyproject.toml`` path.

    Returns:
        Absolute path to the repository root ``pyproject.toml`` when running
        from the source or Docker layout used by this project.
    """
    return Path(__file__).resolve().parents[1] / "pyproject.toml"


def _read_pyproject_version(pyproject_path: Path) -> str | None:
    """Read the project version from a ``pyproject.toml`` file.

    Args:
        pyproject_path: Path to the candidate ``pyproject.toml`` file.

    Returns:
        Stripped ``[project].version`` value, or ``None`` when the file is
        missing, malformed, or does not contain a non-empty string version.
    """
    if pyproject_path in _PYPROJECT_VERSION_CACHE:
        return _PYPROJECT_VERSION_CACHE[pyproject_path]

    try:
        with pyproject_path.open("rb") as pyproject_file:
            parsed_pyproject: object = tomllib.load(pyproject_file)
    except OSError, tomllib.TOMLDecodeError:
        _PYPROJECT_VERSION_CACHE[pyproject_path] = None
        return None

    if not isinstance(parsed_pyproject, dict):
        _PYPROJECT_VERSION_CACHE[pyproject_path] = None
        return None
    project_table = parsed_pyproject.get("project")
    if not isinstance(project_table, dict):
        _PYPROJECT_VERSION_CACHE[pyproject_path] = None
        return None
    raw_version = project_table.get("version")
    if not isinstance(raw_version, str):
        _PYPROJECT_VERSION_CACHE[pyproject_path] = None
        return None

    version = raw_version.strip()
    resolved_version = version or None
    _PYPROJECT_VERSION_CACHE[pyproject_path] = resolved_version
    return resolved_version
