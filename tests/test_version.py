from pathlib import Path

import pytest

import conformance.version as version_module
from conformance.version import CONFORMANCE_TOOL_VERSION_ENV, UNKNOWN_TOOL_VERSION, resolve_conformance_tool_version


@pytest.mark.unit
def test_resolve_conformance_tool_version_prefers_environment(tmp_path: Path) -> None:
    pyproject_path = tmp_path / "pyproject.toml"
    pyproject_path.write_text('[project]\nversion = "1.2.3"\n', encoding="utf-8")

    version = resolve_conformance_tool_version(
        environ={CONFORMANCE_TOOL_VERSION_ENV: " 2.4.6 "},
        pyproject_path=pyproject_path,
    )

    assert version == "2.4.6"


@pytest.mark.unit
def test_resolve_conformance_tool_version_reads_pyproject_when_env_empty(tmp_path: Path) -> None:
    pyproject_path = tmp_path / "pyproject.toml"
    pyproject_path.write_text('[project]\nversion = "1.2.3"\n', encoding="utf-8")

    version = resolve_conformance_tool_version(
        environ={CONFORMANCE_TOOL_VERSION_ENV: ""},
        pyproject_path=pyproject_path,
    )

    assert version == "1.2.3"


@pytest.mark.unit
def test_resolve_conformance_tool_version_falls_back_when_unavailable(tmp_path: Path) -> None:
    version = resolve_conformance_tool_version(environ={}, pyproject_path=tmp_path / "missing.toml")

    assert version == UNKNOWN_TOOL_VERSION


@pytest.mark.unit
def test_resolve_conformance_tool_version_caches_pyproject_version(tmp_path: Path) -> None:
    version_module._PYPROJECT_VERSION_CACHE.clear()
    pyproject_path = tmp_path / "pyproject.toml"
    pyproject_path.write_text('[project]\nversion = "1.2.3"\n', encoding="utf-8")

    first_version = resolve_conformance_tool_version(environ={}, pyproject_path=pyproject_path)
    pyproject_path.write_text('[project]\nversion = "9.9.9"\n', encoding="utf-8")
    second_version = resolve_conformance_tool_version(environ={}, pyproject_path=pyproject_path)

    assert first_version == "1.2.3"
    assert second_version == "1.2.3"


@pytest.mark.unit
def test_resolve_conformance_tool_version_caches_pyproject_failures(tmp_path: Path) -> None:
    version_module._PYPROJECT_VERSION_CACHE.clear()
    pyproject_path = tmp_path / "missing.toml"

    first_version = resolve_conformance_tool_version(environ={}, pyproject_path=pyproject_path)
    pyproject_path.write_text('[project]\nversion = "1.2.3"\n', encoding="utf-8")
    second_version = resolve_conformance_tool_version(environ={}, pyproject_path=pyproject_path)

    assert first_version == UNKNOWN_TOOL_VERSION
    assert second_version == UNKNOWN_TOOL_VERSION
